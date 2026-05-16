#include "v2/room/room_backend_service.h"
#include "v2/service/backend_server.h"
#include "v3/proto/envelope_codec.h"

#include <nlohmann/json.hpp>
#include "app/audit_log.h"

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

std::uint32_t read_battle_max_frames_override(std::uint32_t fallback) {
    const char* raw = std::getenv("V2_BATTLE_MAX_FRAMES");
    if (raw == nullptr || raw[0] == '\0') {
        return fallback;
    }
    const auto parsed = std::strtoul(raw, nullptr, 10);
    return parsed > 0 ? static_cast<std::uint32_t>(parsed) : fallback;
}

struct RoomMember {
    std::string user_id;
    bool ready = false;
};

struct RoomState {
    std::string room_id;
    std::string owner_user_id;
    std::vector<RoomMember> members;
    std::string active_battle_id;
};

struct RoomStateManager {
    std::unordered_map<std::string, RoomState> rooms_;
    std::mutex mutex_;
    int next_battle_id_ = 1;

    RoomState* find(const std::string& room_id) {
        auto it = rooms_.find(room_id);
        return it != rooms_.end() ? &it->second : nullptr;
    }

    RoomMember* find_member(RoomState& room, const std::string& user_id) {
        for (auto& m : room.members) {
            if (m.user_id == user_id) return &m;
        }
        return nullptr;
    }

    bool all_members_ready(const RoomState& room) {
        return room.members.size() >= 2 &&
               std::all_of(room.members.begin(), room.members.end(),
                           [](const RoomMember& m) { return m.ready; });
    }
};

v2::service::BackendEnvelope make_error(int code, const std::string& reason) {
    v2::service::BackendEnvelope resp;
    resp.kind = v2::service::MessageKind::kError;
    resp.error_code = code;
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
    explicit Impl(std::uint16_t port) : port_(port) {}

    void start() {
        v2::service::BackendServer::HandlerMap handlers;
        handlers["room_create"] = [this](const auto& req) { return handle_room_create(req); };
        handlers["room_join"] = [this](const auto& req) { return handle_room_join(req); };
        handlers["room_ready"] = [this](const auto& req) { return handle_room_ready(req); };
        handlers["room_start_battle"] = [this](const auto& req) { return handle_room_start_battle(req); };
        handlers["room_leave"] = [this](const auto& req) { return handle_room_leave(req); };

        server_ = std::make_unique<v2::service::BackendServer>(port_, std::move(handlers));
        server_->start();
    }

    void stop() {
        if (server_) {
            server_->stop();
        }
    }

    std::uint16_t local_port() const {
        return server_ ? server_->local_port() : port_;
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    RoomStateManager room_manager_;

    v2::service::BackendEnvelope handle_room_create(
        const v2::service::BackendEnvelope& request) {
        auto decoded_envelope = v3::proto::decode_typed_envelope(request.payload);
        auto raw_payload = decoded_envelope.has_value() ? decoded_envelope->payload.dump() : request.payload;
        auto doc = nlohmann::json::parse(raw_payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();

        if (user_id.empty() || room_id.empty()) {
            return make_error(-1004, "empty_fields");
        }

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        if (room_manager_.find(room_id) != nullptr) {
            return make_error(-2002, "room_already_exists");
        }

        RoomState room;
        room.room_id = room_id;
        room.owner_user_id = user_id;
        room.members.push_back(RoomMember{.user_id = user_id, .ready = false});
        room_manager_.rooms_[room_id] = std::move(room);

        AUDIT_LOG("room_created", "room_id=" + room_id + " owner=" + user_id);
        auto resp = make_ok({{"room_id", room_id}, {"member_count", 1}});
        resp.payload = v3::proto::maybe_wrap_typed_response(
            decoded_envelope,
            v3::proto::EnvelopeMessageKind::kRoomCreateResponse,
            nlohmann::json::parse(resp.payload, nullptr, false));
        return resp;
    }

    v2::service::BackendEnvelope handle_room_join(
        const v2::service::BackendEnvelope& request) {
        auto decoded_envelope = v3::proto::decode_typed_envelope(request.payload);
        auto raw_payload = decoded_envelope.has_value() ? decoded_envelope->payload.dump() : request.payload;
        auto doc = nlohmann::json::parse(raw_payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(-2003, "room_not_found");
        }

        if (room_manager_.find_member(*room, user_id) != nullptr) {
            auto resp = make_ok({{"room_id", room_id}, {"member_count", room->members.size()}});
            resp.payload = v3::proto::maybe_wrap_typed_response(
                decoded_envelope,
                v3::proto::EnvelopeMessageKind::kRoomJoinResponse,
                nlohmann::json::parse(resp.payload, nullptr, false));
            return resp;
        }

        room->members.push_back(RoomMember{.user_id = user_id, .ready = false});

        auto resp = make_ok({{"room_id", room_id}, {"member_count", room->members.size()}});
        resp.payload = v3::proto::maybe_wrap_typed_response(
            decoded_envelope,
            v3::proto::EnvelopeMessageKind::kRoomJoinResponse,
            nlohmann::json::parse(resp.payload, nullptr, false));
        return resp;
    }

    v2::service::BackendEnvelope handle_room_ready(
        const v2::service::BackendEnvelope& request) {
        auto decoded_envelope = v3::proto::decode_typed_envelope(request.payload);
        auto raw_payload = decoded_envelope.has_value() ? decoded_envelope->payload.dump() : request.payload;
        auto doc = nlohmann::json::parse(raw_payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        bool ready = doc.value("ready", true);

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(-2003, "room_not_found");
        }

        auto* member = room_manager_.find_member(*room, user_id);
        if (member == nullptr) {
            return make_error(-2005, "not_in_room");
        }

        member->ready = ready;

        auto resp = make_ok({{"room_id", room_id}, {"all_ready", room_manager_.all_members_ready(*room)}});
        resp.payload = v3::proto::maybe_wrap_typed_response(
            decoded_envelope,
            v3::proto::EnvelopeMessageKind::kRoomReadyResponse,
            nlohmann::json::parse(resp.payload, nullptr, false));
        return resp;
    }

    v2::service::BackendEnvelope handle_room_start_battle(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(-2003, "room_not_found");
        }

        if (user_id != room->owner_user_id) {
            return make_error(-2006, "not_room_owner");
        }

        if (!room->active_battle_id.empty()) {
            return make_error(-2004, "battle_already_started");
        }

        if (room->members.size() < 2) {
            return make_error(-3001, "not_enough_players");
        }

        if (!room_manager_.all_members_ready(*room)) {
            return make_error(-2007, "not_all_ready");
        }

        // Generate battle_id and record it on the room
        std::string battle_id = "battle_" + std::to_string(room_manager_.next_battle_id_++);
        room->active_battle_id = battle_id;

        AUDIT_LOG("battle_created", "room_id=" + room_id + " battle_id=" + room->active_battle_id);

        // Collect player_ids for battle creation
        nlohmann::json player_ids = nlohmann::json::array();
        for (const auto& m : room->members) {
            player_ids.push_back(m.user_id);
        }

        // Build forward instruction for gateway to cascade to battle_backend
        nlohmann::json forward_payload{
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"player_ids", player_ids},
            {"max_frames", read_battle_max_frames_override(3)},
        };

        return make_ok({
            {"room_id", room_id},
            {"player_ids", player_ids},
            {"forward", nlohmann::json{
                {"target", "battle"},
                {"message_type", "battle_create"},
                {"payload", std::move(forward_payload)},
            }},
        });
    }

    v2::service::BackendEnvelope handle_room_leave(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(room_manager_.mutex_);

        auto* room = room_manager_.find(room_id);
        if (room == nullptr) {
            return make_error(-2003, "room_not_found");
        }

        auto& members = room->members;
        const bool was_owner = (room->owner_user_id == user_id);

        members.erase(std::remove_if(members.begin(), members.end(),
            [&](const RoomMember& m) { return m.user_id == user_id; }),
            members.end());

        if (members.empty()) {
            AUDIT_LOG("room_deleted", "room_id=" + room_id);
            room_manager_.rooms_.erase(room_id);
        } else if (was_owner) {
            room->owner_user_id = members.front().user_id;
        }

        return make_ok({{"room_id", room_id}});
    }
};

RoomBackendService::RoomBackendService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}

RoomBackendService::~RoomBackendService() = default;

void RoomBackendService::start() { impl_->start(); }
void RoomBackendService::stop() { impl_->stop(); }
std::uint16_t RoomBackendService::local_port() const { return impl_->local_port(); }

}  // namespace v2::room
