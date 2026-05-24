#include "v2/room/room_backend_service.h"
#include "v2/diagnostics/diagnostics_manager.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"
#include "v2/service/error_codes.h"

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>
#include "app/audit_log.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace {

struct RoomMember {
    std::string user_id;
    std::string display_name;
    bool ready = false;
};

enum class RoomStatus : std::uint8_t {
    kWaiting = 0,
    kInInstance = 1,
    kClosed = 2,
};

struct RoomState {
    std::string room_id;
    std::string owner_user_id;
    std::vector<RoomMember> members;
    std::string active_battle_id;
    nlohmann::json metadata = nlohmann::json::object();
    std::uint32_t capacity = 0;       // 0 = unlimited
    std::string visibility = "public"; // public or private
    std::uint32_t version = 0;        // incremented on each state change
    RoomStatus status = RoomStatus::kWaiting;
    std::int64_t created_at_ms = 0;
    std::optional<std::chrono::steady_clock::time_point> last_activity_at_;
};

struct RoomStateManager {
    std::unordered_map<std::string, RoomState> rooms_;
    mutable std::mutex mutex_;
    int next_battle_id_ = 1;

    RoomState* find(const std::string& room_id) {
        auto it = rooms_.find(room_id);
        return it != rooms_.end() ? &it->second : nullptr;
    }

    const RoomState* find(const std::string& room_id) const {
        auto it = rooms_.find(room_id);
        return it != rooms_.end() ? &it->second : nullptr;
    }

    RoomMember* find_member(RoomState& room, const std::string& user_id) {
        for (auto& m : room.members) {
            if (m.user_id == user_id) return &m;
        }
        return nullptr;
    }

    bool all_members_ready(const RoomState& room) const {
        return room.members.size() >= 2 &&
               std::all_of(room.members.begin(), room.members.end(),
                           [](const RoomMember& m) { return m.ready; });
    }

    // Build a JSON object for a room (used by list and detail)
    nlohmann::json room_to_json(const RoomState& room) const {
        nlohmann::json members_json = nlohmann::json::array();
        for (const auto& m : room.members) {
            members_json.push_back({
                {"user_id", m.user_id},
                {"display_name", m.display_name},
                {"ready", m.ready},
            });
        }
        nlohmann::json j;
        j["room_id"] = room.room_id;
        j["owner_user_id"] = room.owner_user_id;
        j["members"] = std::move(members_json);
        j["member_count"] = room.members.size();
        j["capacity"] = room.capacity;
        j["visibility"] = room.visibility;
        j["version"] = room.version;
        j["status"] = status_to_string(room.status);
        j["created_at_ms"] = room.created_at_ms;
        if (!room.active_battle_id.empty()) {
            j["active_battle_id"] = room.active_battle_id;
        }
        if (!room.metadata.empty()) {
            j["metadata"] = room.metadata;
        }
        if (room.last_activity_at_.has_value()) {
            auto idle = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - *room.last_activity_at_).count();
            j["idle_ms"] = idle;
        }
        return j;
    }

    static const char* status_to_string(RoomStatus s) {
        switch (s) {
            case RoomStatus::kWaiting: return "waiting";
            case RoomStatus::kInInstance: return "in_instance";
            case RoomStatus::kClosed: return "closed";
        }
        return "waiting";
    }
};

v2::service::BackendEnvelope make_error(v2::service::ServiceErrorCode code, const std::string& reason) {
    v2::service::BackendEnvelope resp;
    resp.kind = v2::service::MessageKind::kError;
    resp.error_code = static_cast<std::int32_t>(code);
    nlohmann::json body{{"status", "error"}, {"reason", reason}};
    resp.payload = body.dump();
    return resp;
}

v2::service::BackendEnvelope make_ok(nlohmann::json extra = {}) {
    v2::service::BackendEnvelope resp;
    resp.kind = v2::service::MessageKind::kResponse;
    nlohmann::json body{{"status", "ok"}};
    if (!extra.empty()) {
        for (auto& [key, value] : extra.items()) {
            body[key] = std::move(value);
        }
    }
    resp.payload = body.dump();
    return resp;
}

}  // namespace

namespace v2::room {

class RoomBackendService::Impl {
public:
    explicit Impl(std::uint16_t port, std::uint32_t battle_max_frames,
                  std::uint32_t room_ttl_ms, std::uint32_t cleanup_interval_ms)
        : port_(port), battle_max_frames_(battle_max_frames),
          room_ttl_ms_(room_ttl_ms), cleanup_interval_ms_(cleanup_interval_ms) {}

    void start() {
        v2::service::BackendServer::HandlerMap handlers;

        // Wrap handlers with diagnostics tracking.
        auto diag_wrap = [this](const std::string& name,
                                std::function<v2::service::BackendEnvelope(
                                    const v2::service::BackendEnvelope&)> handler) {
            return [this, name, handler = std::move(handler)](
                       const v2::service::BackendEnvelope& req) {
                SPDLOG_DEBUG("RoomBackend: handling {} request", name);
                try {
                    auto resp = handler(req);
                    SPDLOG_DEBUG("RoomBackend: {} completed", name);
                    return resp;
                } catch (const std::exception& e) {
                    SPDLOG_ERROR("RoomBackend: {} failed: {}", name, e.what());
                    return make_error(v2::service::ServiceErrorCode::kInternalError, e.what());
                }
            };
        };

        handlers["room_create"] = diag_wrap("room_create", [this](const auto& req) { return handle_room_create(req); });
        handlers["room_join"] = diag_wrap("room_join", [this](const auto& req) { return handle_room_join(req); });
        handlers["room_ready"] = diag_wrap("room_ready", [this](const auto& req) { return handle_room_ready(req); });
        handlers["room_start_battle"] = diag_wrap("room_start_battle", [this](const auto& req) { return handle_room_start_battle(req); });
        handlers["room_leave"] = diag_wrap("room_leave", [this](const auto& req) { return handle_room_leave(req); });
        handlers["room_list"] = diag_wrap("room_list", [this](const auto& req) { return handle_room_list(req); });
        handlers["room_detail"] = diag_wrap("room_detail", [this](const auto& req) { return handle_room_detail(req); });
        handlers["room_kick"] = diag_wrap("room_kick", [this](const auto& req) { return handle_room_kick(req); });
        handlers["room_transfer_owner"] = diag_wrap("room_transfer_owner", [this](const auto& req) { return handle_room_transfer_owner(req); });
        handlers["room_state_push"] = diag_wrap("room_state_push", [this](const auto& req) { return handle_room_state_push(req); });
        handlers["room_battle_finished"] = diag_wrap("room_battle_finished", [this](const auto& req) { return handle_room_battle_finished(req); });

        server_ = std::make_unique<v2::service::BackendServer>(
            v2::service::BackendServerOptions{.port = port_, .tls_config = tls_config_},
            std::move(handlers));
        server_->start();
        start_cleanup_timer();
    }

    void stop() {
        stop_cleanup_timer();
        if (server_) {
            server_->stop();
        }
    }

    std::uint16_t local_port() const {
        return server_ ? server_->local_port() : port_;
    }

    void set_tls_config(std::optional<v3::cluster::TlsSessionConfig> tls_config) {
        tls_config_ = std::move(tls_config);
    }

private:
    std::uint16_t port_;
    std::uint32_t battle_max_frames_ = 3;
    std::unique_ptr<v2::service::BackendServer> server_;
    std::optional<v3::cluster::TlsSessionConfig> tls_config_;
    RoomStateManager room_manager_;

    std::uint32_t room_ttl_ms_ = 300000;
    std::uint32_t cleanup_interval_ms_ = 60000;
    std::atomic<bool> cleanup_running_{false};
    std::thread cleanup_thread_;

    // v2.2.0: Diagnostics integration — snapshot collector for health /metrics
    v2::diagnostics::DiagnosticsManager diagnostics_;

    void start_cleanup_timer() {
        cleanup_running_ = true;
        cleanup_thread_ = std::thread([this]() {
            while (cleanup_running_) {
                std::this_thread::sleep_for(std::chrono::milliseconds(cleanup_interval_ms_));
                if (!cleanup_running_) break;
                cleanup_expired_rooms();
            }
        });
    }

    void stop_cleanup_timer() {
        cleanup_running_ = false;
        if (cleanup_thread_.joinable()) {
            cleanup_thread_.join();
        }
    }

    void cleanup_expired_rooms() {
        auto now = std::chrono::steady_clock::now();
        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        std::vector<std::string> expired;
        for (const auto& [id, room] : room_manager_.rooms_) {
            if (room.status == RoomStatus::kWaiting && room.last_activity_at_.has_value()) {
                auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
                    now - *room.last_activity_at_).count();
                if (elapsed >= static_cast<std::int64_t>(room_ttl_ms_)) {
                    expired.push_back(id);
                }
            }
        }
        for (const auto& id : expired) {
            AUDIT_LOG("room_ttl_cleaned", "room_id=" + id);
            room_manager_.rooms_.erase(id);
        }
    }

    // ─── room_create ─────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_create(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() ||
            !decoded->payload.contains("user_id") || !decoded->payload.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }
        const auto& doc = decoded->payload;

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        std::string display_name = doc.value("display_name", user_id);
        std::string visibility = doc.value("visibility", std::string("public"));
        std::uint32_t capacity = doc.value("capacity", 0);

        if (user_id.empty() || room_id.empty()) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "empty_fields");
        }

        nlohmann::json metadata = nlohmann::json::object();
        if (doc.contains("metadata") && doc["metadata"].is_object()) {
            metadata = doc["metadata"];
        }

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        if (room_manager_.find(room_id) != nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "room_already_exists");
        }

        auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();

        RoomState room;
        room.room_id = room_id;
        room.owner_user_id = user_id;
        room.metadata = std::move(metadata);
        room.capacity = capacity;
        room.visibility = visibility;
        room.version = 1;
        room.status = RoomStatus::kWaiting;
        room.created_at_ms = now_ms;
        room.last_activity_at_ = std::chrono::steady_clock::now();
        room.members.push_back(RoomMember{.user_id = user_id, .display_name = display_name, .ready = false});
        room_manager_.rooms_[room_id] = std::move(room);

        AUDIT_LOG("room_created", "room_id=" + room_id + " owner=" + user_id);
        auto resp = make_ok({{"room_id", room_id}, {"member_count", 1}, {"version", 1}});
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kRoomCreateResponse);
    }

    // ─── room_join ───────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_join(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() ||
            !decoded->payload.contains("user_id") || !decoded->payload.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }
        const auto& doc = decoded->payload;

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        std::string display_name = doc.value("display_name", user_id);

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        if (room->status == RoomStatus::kInInstance) {
            return make_error(v2::service::ServiceErrorCode::kRoomInInstance, "room_in_instance");
        }
        if (room->status == RoomStatus::kClosed) {
            return make_error(v2::service::ServiceErrorCode::kRoomClosed, "room_closed");
        }

        if (room_manager_.find_member(*room, user_id) != nullptr) {
            auto resp = make_ok({{"room_id", room_id}, {"member_count", room->members.size()}});
            return v2::service::wrap_typed_response_if_needed(
                decoded->typed_request,
                std::move(resp),
                v3::proto::EnvelopeMessageKind::kRoomJoinResponse);
        }

        // Check capacity
        if (room->capacity > 0 && room->members.size() >= room->capacity) {
            return make_error(v2::service::ServiceErrorCode::kRoomFull, "room_full");
        }

        room->members.push_back(RoomMember{.user_id = user_id, .display_name = display_name, .ready = false});
        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        auto resp = make_ok({{"room_id", room_id}, {"member_count", room->members.size()}});
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kRoomJoinResponse);
    }

    // ─── room_ready ──────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_ready(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() ||
            !decoded->payload.contains("user_id") || !decoded->payload.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }
        const auto& doc = decoded->payload;

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        bool ready = doc.value("ready", true);

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        auto* member = room_manager_.find_member(*room, user_id);
        if (member == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomMember, "not_in_room");
        }

        member->ready = ready;
        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        auto resp = make_ok({
            {"room_id", room_id},
            {"all_ready", room_manager_.all_members_ready(*room)},
            {"version", room->version},
        });
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kRoomReadyResponse);
    }

    // ─── room_start_battle ───────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_start_battle(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        if (user_id != room->owner_user_id) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomOwner, "not_room_owner");
        }

        if (!room->active_battle_id.empty()) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "battle_already_started");
        }

        if (room->members.size() < 2) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "not_enough_players");
        }

        if (!room_manager_.all_members_ready(*room)) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "not_all_ready");
        }

        // Generate battle_id and record it on the room
        std::string battle_id = "battle_" + std::to_string(room_manager_.next_battle_id_++);
        room->active_battle_id = battle_id;
        room->status = RoomStatus::kInInstance;
        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        AUDIT_LOG("battle_created", "room_id=" + room_id + " battle_id=" + room->active_battle_id);

        nlohmann::json player_ids = nlohmann::json::array();
        for (const auto& m : room->members) {
            player_ids.push_back(m.user_id);
        }

        nlohmann::json forward_payload{
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"player_ids", player_ids},
            {"max_frames", battle_max_frames_},
        };

        return make_ok({
            {"room_id", room_id},
            {"player_ids", player_ids},
            {"version", room->version},
            {"forward", nlohmann::json{
                {"target", "battle"},
                {"message_type", "battle_create"},
                {"payload", std::move(forward_payload)},
            }},
        });
    }

    // ─── room_leave ──────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_leave(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        if (room->status == RoomStatus::kInInstance) {
            return make_error(v2::service::ServiceErrorCode::kRoomInInstance, "cannot_leave_during_instance");
        }

        auto& members = room->members;
        const bool was_owner = (room->owner_user_id == user_id);

        auto it = std::remove_if(members.begin(), members.end(),
            [&](const RoomMember& m) { return m.user_id == user_id; });
        if (it == members.end()) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomMember, "not_in_room");
        }
        members.erase(it, members.end());

        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        if (members.empty()) {
            AUDIT_LOG("room_deleted", "room_id=" + room_id);
            room_manager_.rooms_.erase(room_id);
        } else if (was_owner) {
            room->owner_user_id = members.front().user_id;
            AUDIT_LOG("room_owner_transferred",
                      "room_id=" + room_id + " new_owner=" + room->owner_user_id);
        }

        return make_ok({{"room_id", room_id}});
    }

    // ─── room_list ───────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_battle_finished(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("room_id") || !doc.contains("battle_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        const std::string room_id = doc["room_id"].get<std::string>();
        const std::string battle_id = doc["battle_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);
        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }
        if (!room->active_battle_id.empty() && room->active_battle_id != battle_id) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "battle_mismatch");
        }

        room->active_battle_id.clear();
        room->status = RoomStatus::kWaiting;
        for (auto& member : room->members) {
            member.ready = false;
        }
        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        return make_ok({{"room_id", room_id}, {"battle_id", battle_id}, {"version", room->version}});
    }

    v2::service::BackendEnvelope handle_room_list(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded()) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string filter_visibility = doc.value("visibility", std::string(""));
        std::string filter_status = doc.value("status", std::string(""));
        std::uint32_t page = doc.value("page", 1);
        std::uint32_t page_size = doc.value("page_size", 20);

        if (page < 1) page = 1;
        if (page_size < 1) page_size = 20;
        if (page_size > 100) page_size = 100;

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        // Collect matching rooms
        std::vector<nlohmann::json> matches;
        for (const auto& [id, room] : room_manager_.rooms_) {
            if (!filter_visibility.empty() && room.visibility != filter_visibility) continue;
            if (!filter_status.empty() && RoomStateManager::status_to_string(room.status) != filter_status) continue;
            matches.push_back(room_manager_.room_to_json(room));
        }

        // Paginate
        std::uint32_t total = static_cast<std::uint32_t>(matches.size());
        std::uint32_t total_pages = (total + page_size - 1) / page_size;
        if (total_pages < 1) total_pages = 1;

        std::uint32_t start = (page - 1) * page_size;
        std::uint32_t end = std::min(start + page_size, total);

        nlohmann::json rooms_json = nlohmann::json::array();
        if (start < total) {
            for (std::uint32_t i = start; i < end; ++i) {
                rooms_json.push_back(std::move(matches[i]));
            }
        }

        return make_ok({
            {"rooms", std::move(rooms_json)},
            {"total", total},
            {"page", page},
            {"page_size", page_size},
            {"total_pages", total_pages},
        });
    }

    // ─── room_detail ─────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_detail(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string room_id = doc["room_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        const auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        return make_ok({
            {"room", room_manager_.room_to_json(*room)},
        });
    }

    // ─── room_kick ───────────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_kick(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id") ||
            !doc.contains("target_user_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        std::string target_user_id = doc["target_user_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        if (user_id != room->owner_user_id) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomOwner, "not_room_owner");
        }

        if (target_user_id == room->owner_user_id) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "cannot_kick_self");
        }

        auto& members = room->members;
        auto it = std::remove_if(members.begin(), members.end(),
            [&](const RoomMember& m) { return m.user_id == target_user_id; });
        if (it == members.end()) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomMember, "target_not_in_room");
        }
        members.erase(it, members.end());
        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        AUDIT_LOG("room_kick", "room_id=" + room_id + " kicked=" + target_user_id +
                               " by=" + user_id);

        return make_ok({
            {"room_id", room_id},
            {"kicked_user_id", target_user_id},
            {"member_count", members.size()},
        });
    }

    // ─── room_transfer_owner ─────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_transfer_owner(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id") ||
            !doc.contains("new_owner_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        std::string new_owner_id = doc["new_owner_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        if (user_id != room->owner_user_id) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomOwner, "not_room_owner");
        }

        if (room_manager_.find_member(*room, new_owner_id) == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kNotRoomMember, "new_owner_not_in_room");
        }

        if (new_owner_id == user_id) {
            return make_error(v2::service::ServiceErrorCode::kRejected, "already_owner");
        }

        room->owner_user_id = new_owner_id;
        room->version++;
        room->last_activity_at_ = std::chrono::steady_clock::now();

        AUDIT_LOG("room_owner_transferred",
                  "room_id=" + room_id + " from=" + user_id + " to=" + new_owner_id);

        return make_ok({
            {"room_id", room_id},
            {"new_owner_id", new_owner_id},
            {"version", room->version},
        });
    }

    // ─── room_state_push ───────────────────────────────────────────────

    v2::service::BackendEnvelope handle_room_state_push(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("room_id")) {
            return make_error(v2::service::ServiceErrorCode::kInvalidRequest, "invalid_json");
        }

        std::string room_id = doc["room_id"].get<std::string>();
        std::string event_type = doc.value("event_type", std::string("state_changed"));

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        const auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(v2::service::ServiceErrorCode::kRoomNotFound, "room_not_found");
        }

        // Build a push payload with current room state
        nlohmann::json push_body;
        push_body["type"] = "room_state_push";
        push_body["event_type"] = event_type;
        push_body["room_id"] = room_id;
        push_body["room"] = room_manager_.room_to_json(*room);
        push_body["timestamp"] = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();

        // Collect member user_ids so the gateway knows who to push to
        nlohmann::json member_ids = nlohmann::json::array();
        for (const auto& m : room->members) {
            member_ids.push_back(m.user_id);
        }
        push_body["member_user_ids"] = std::move(member_ids);

        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kResponse;
        response.payload = push_body.dump();
        return response;
    }
};

RoomBackendService::RoomBackendService(std::uint16_t port)
    : RoomBackendService(port, 3) {}

RoomBackendService::RoomBackendService(std::uint16_t port, std::uint32_t battle_max_frames)
    : RoomBackendService(port, battle_max_frames, 300000, 60000) {}

RoomBackendService::RoomBackendService(std::uint16_t port, std::uint32_t battle_max_frames,
                                        std::uint32_t room_ttl_ms, std::uint32_t cleanup_interval_ms)
    : impl_(std::make_unique<Impl>(port, battle_max_frames > 0 ? battle_max_frames : 3,
                                   room_ttl_ms, cleanup_interval_ms)) {}

RoomBackendService::~RoomBackendService() = default;

void RoomBackendService::start() { impl_->start(); }
void RoomBackendService::stop() { impl_->stop(); }
std::uint16_t RoomBackendService::local_port() const { return impl_->local_port(); }

void RoomBackendService::set_tls_config(
    std::optional<v3::cluster::TlsSessionConfig> tls_config) {
    impl_->set_tls_config(std::move(tls_config));
}

}  // namespace v2::room
