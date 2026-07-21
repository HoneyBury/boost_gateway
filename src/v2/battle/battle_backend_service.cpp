#include "v2/battle/battle_backend_service.h"
#include "v2/battle/battle_instance_plugin.h"
#include "v2/battle/message_types.h"
#include "v2/battle/runtime_world.h"

#include "v2/ecs/world.h"
#include "v2/gateway/battle_data_store.h"
#include "v2/persistence/replay_storage.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

// ─── Helpers ────────────────────────────────────────────────────────────

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

// ─── SyncCapture ────────────────────────────────────────────────────────
//
// Captures InstanceEvent snapshots and settlements synchronously so that
// request-response handlers can consume them after calling tick or finish.

class SyncCapture {
public:
    void store_snapshot(const std::string& instance_id,
                        v2::realtime::Snapshot snapshot) {
        std::lock_guard<std::mutex> lock(mutex_);
        snapshots_[instance_id] = std::move(snapshot);
    }

    v2::realtime::Snapshot consume_snapshot(const std::string& instance_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = snapshots_.find(instance_id);
        if (it == snapshots_.end()) return {};
        auto s = std::move(it->second);
        snapshots_.erase(it);
        return s;
    }

    void store_settlement(const std::string& instance_id,
                          std::string settlement) {
        std::lock_guard<std::mutex> lock(mutex_);
        settlements_[instance_id] = std::move(settlement);
    }

    std::string consume_settlement(const std::string& instance_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = settlements_.find(instance_id);
        if (it == settlements_.end()) return {};
        auto s = std::move(it->second);
        settlements_.erase(it);
        return s;
    }

private:
    std::mutex mutex_;
    std::unordered_map<std::string, v2::realtime::Snapshot> snapshots_;
    std::unordered_map<std::string, std::string> settlements_;
};

struct CachedBattleSnapshot {
    std::uint32_t frame_number = 0;
    std::string payload;
};

struct ReplayFrameCacheEntry {
    std::uint32_t frame_number = 0;
    nlohmann::json frame;
};

struct BattleItemState {
    bool picked = false;
    std::string picked_by;
};

std::optional<std::string> speed_buff_user_from_snapshot(const nlohmann::json& snapshot) {
    if (!snapshot.contains("buffs") || !snapshot["buffs"].is_array()) {
        return std::nullopt;
    }
    for (const auto& buff : snapshot["buffs"]) {
        if (buff.value("type", "") == "speed") {
            const auto user_id = buff.value("user_id", "");
            if (!user_id.empty()) {
                return user_id;
            }
        }
    }
    return std::nullopt;
}

// ─── Snapshot payload helpers ───────────────────────────────────────────

// Extract participant data from a snapshot payload, handling both
// BattleInstancePlugin format ("participants") and TankBattlePlugin
// format ("players").
nlohmann::json extract_participants_from_snapshot(const std::string& payload) {
    if (payload.empty()) return nlohmann::json::array();

    auto j = nlohmann::json::parse(payload, nullptr, false);
    if (j.is_discarded()) return nlohmann::json::array();

    // BattleInstancePlugin format
    if (j.contains("participants")) return j["participants"];

    // TankBattlePlugin format: map "players" fields to the common shape
    if (j.contains("players")) {
        nlohmann::json result = nlohmann::json::array();
        for (const auto& p : j["players"]) {
            result.push_back({
                {"user_id", p.value("user_id", "")},
                {"online", p.value("online", true)},
                {"score", p.value("score", 0)},
                {"pos_x", p.value("x", 0)},
                {"pos_y", p.value("y", 0)},
                {"hp", p.value("hp", 0)},
                {"max_hp", p.value("max_hp", 0)},
                {"direction_x", p.value("direction_x", 1)},
                {"direction_y", p.value("direction_y", 0)},
            });
        }
        return result;
    }

    return nlohmann::json::array();
}

std::optional<std::string> parse_pickup_input(const std::string& input_data) {
    constexpr std::string_view prefix = "pickup:";
    if (input_data.rfind(std::string(prefix), 0) != 0) {
        return std::nullopt;
    }
    auto item_id = input_data.substr(prefix.size());
    return item_id.empty() ? std::nullopt : std::optional<std::string>(std::move(item_id));
}

}  // namespace

namespace v2::battle {

class BattleBackendService::Impl {
public:
    explicit Impl(std::uint16_t port) : port_(port) {}

    void start() {
        // Register plugin factories
        runtime_.register_plugin("battle",
            []() -> std::unique_ptr<v2::realtime::InstancePlugin> {
                return std::make_unique<BattleInstancePlugin>();
            });
        // "tank_battle" plugin is now registered by demo/games/tank_battle/
        // when BOOST_BUILD_TANK_DEMO=ON. See demo tank_battle_main.cpp.

        // Capture events for synchronous consumption
        runtime_.set_event_callback(
            [this](const v2::realtime::InstanceEvent& event) {
                switch (event.type) {
                    case v2::realtime::InstanceEvent::Type::kSnapshotAvailable:
                        sync_capture_.store_snapshot(event.instance_id,
                                                     event.snapshot);
                        break;
                    case v2::realtime::InstanceEvent::Type::kInstanceFinished:
                        sync_capture_.store_settlement(
                            event.instance_id,
                            event.settlement.result_payload);
                        break;
                    default:
                        break;
                }
            });

        v2::service::BackendServer::HandlerMap handlers;
        handlers["battle_create"] = [this](const auto& req) { return handle_battle_create(req); };
        handlers["battle_input"] = [this](const auto& req) { return handle_battle_input(req); };
        handlers["battle_state"] = [this](const auto& req) { return handle_battle_state(req); };
        handlers["replay_load"] = [this](const auto& req) { return handle_replay_load(req); };
        handlers["battle_finish"] = [this](const auto& req) { return handle_battle_finish(req); };

        server_ = std::make_unique<v2::service::BackendServer>(
            v2::service::BackendServerOptions{.port = port_, .tls_config = tls_config_},
            std::move(handlers));
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

    void set_tls_config(std::optional<v3::cluster::TlsSessionConfig> tls_config) {
        tls_config_ = std::move(tls_config);
    }

    void set_instance_type(const std::string& type) {
        instance_type_ = type;
    }

    void set_archive_store(std::unique_ptr<v2::data::CachedBattleDataStore> store) {
        archive_store_ = std::move(store);
    }

    void set_archive_path(const std::string& path) {
        auto delegate = std::make_shared<v2::gateway::JsonFileBattleDataStore>(path);
        archive_store_ = std::make_unique<v2::data::CachedBattleDataStore>(std::move(delegate), 100);
    }

    void set_replay_storage_dir(const std::string& path) {
        replay_storage_ = std::make_unique<v2::persistence::ReplayStorage>(path + "/replays");
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    std::optional<v3::cluster::TlsSessionConfig> tls_config_;

    v2::realtime::InstanceRuntime runtime_;
    SyncCapture sync_capture_;
    std::string instance_type_ = "battle";
    std::unique_ptr<v2::data::CachedBattleDataStore> archive_store_;
    std::unique_ptr<v2::persistence::ReplayStorage> replay_storage_;
    std::mutex battle_input_mutex_;

    // Track per-instance frame numbers.  This is needed because
    // InstanceContext does not expose the current frame; the handler
    // requires it to pass the correct next_frame into tick_instance().
    std::unordered_map<std::string, std::uint32_t> instance_frames_;
    std::unordered_map<std::string, std::int64_t> instance_last_tick_ms_;
    std::unordered_map<std::string, CachedBattleSnapshot> latest_snapshots_;
    std::unordered_map<std::string, std::vector<ReplayFrameCacheEntry>> replay_frames_;
    std::unordered_map<std::string, BattleItemState> battle_items_;
    std::mutex frames_mutex_;

    std::uint32_t get_instance_frame(const std::string& battle_id) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        auto it = instance_frames_.find(battle_id);
        return it != instance_frames_.end() ? it->second : 0;
    }

    void set_instance_frame(const std::string& battle_id, std::uint32_t frame) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        instance_frames_[battle_id] = frame;
    }

    bool should_tick_instance(const std::string& battle_id, std::int64_t now_ms) {
        constexpr std::int64_t kQueryDrivenTickIntervalMs = 50;
        std::lock_guard<std::mutex> lock(frames_mutex_);
        auto& last_tick_ms = instance_last_tick_ms_[battle_id];
        if (last_tick_ms > 0 && now_ms - last_tick_ms < kQueryDrivenTickIntervalMs) {
            return false;
        }
        last_tick_ms = now_ms;
        return true;
    }

    void set_latest_snapshot(const std::string& battle_id,
                             std::uint32_t frame,
                             std::string payload) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        latest_snapshots_[battle_id] = CachedBattleSnapshot{
            .frame_number = frame,
            .payload = std::move(payload),
        };
    }

    std::optional<CachedBattleSnapshot> latest_snapshot(const std::string& battle_id) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        auto it = latest_snapshots_.find(battle_id);
        if (it == latest_snapshots_.end()) {
            return std::nullopt;
        }
        return it->second;
    }

    void append_replay_frame(const std::string& battle_id,
                             std::uint32_t frame,
                             nlohmann::json replay_entry) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        replay_frames_[battle_id].push_back(ReplayFrameCacheEntry{
            .frame_number = frame,
            .frame = std::move(replay_entry),
        });
    }

    std::optional<nlohmann::json> replay_doc(const std::string& battle_id) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        auto it = replay_frames_.find(battle_id);
        if (it == replay_frames_.end()) {
            return std::nullopt;
        }
        nlohmann::json frames = nlohmann::json::array();
        for (const auto& entry : it->second) {
            frames.push_back(entry.frame);
        }
        return nlohmann::json{
            {"battle_id", battle_id},
            {"frame_count", it->second.size()},
            {"frames", std::move(frames)},
        };
    }

    void erase_instance_frame(const std::string& battle_id) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        instance_frames_.erase(battle_id);
        instance_last_tick_ms_.erase(battle_id);
        latest_snapshots_.erase(battle_id);
        battle_items_.erase(battle_id);
    }

    nlohmann::json item_id_for(const std::string& battle_id) const {
        return "boost_" + battle_id;
    }

    nlohmann::json item_snapshot_for(const std::string& battle_id,
                                     std::optional<std::string> pickup_user_id = std::nullopt) {
        std::lock_guard<std::mutex> lock(frames_mutex_);
        auto& state = battle_items_[battle_id];
        const auto item_id = item_id_for(battle_id).get<std::string>();
        nlohmann::json items = nlohmann::json::array();
        nlohmann::json events = nlohmann::json::array();
        nlohmann::json buffs = nlohmann::json::array();

        if (pickup_user_id.has_value() && !state.picked) {
            state.picked = true;
            state.picked_by = *pickup_user_id;
            events.push_back({
                {"type", "item_pickup"},
                {"actor", *pickup_user_id},
                {"item_id", item_id},
                {"item_type", "speed"},
                {"buff_type", "speed"},
                {"remaining_ticks", 90},
            });
        }

        if (!state.picked) {
            items.push_back({
                {"id", item_id},
                {"type", "speed"},
                {"x", 3},
                {"y", 2},
                {"remaining_ticks", 600},
            });
        } else {
            buffs.push_back({
                {"user_id", state.picked_by},
                {"type", "speed"},
                {"remaining_ticks", 90},
            });
        }

        return nlohmann::json{
            {"items", std::move(items)},
            {"events", std::move(events)},
            {"buffs", std::move(buffs)},
        };
    }

    void apply_speed_buff_if_present(nlohmann::json& snapshot) {
        const auto buff_user = speed_buff_user_from_snapshot(snapshot);
        if (!buff_user.has_value() || !snapshot.contains("participants") ||
            !snapshot["participants"].is_array()) {
            return;
        }
        for (auto& participant : snapshot["participants"]) {
            if (participant.value("user_id", "") == *buff_user) {
                participant["speed_multiplier"] = 2;
            }
        }
    }

    void enrich_snapshot_with_items(nlohmann::json& snapshot,
                                    const std::string& battle_id,
                                    std::optional<std::string> pickup_user_id = std::nullopt) {
        auto item_state = item_snapshot_for(battle_id, std::move(pickup_user_id));
        snapshot["items"] = std::move(item_state["items"]);
        snapshot["buffs"] = std::move(item_state["buffs"]);
        if (snapshot.contains("events") && snapshot["events"].is_array()) {
            for (auto& event : item_state["events"]) {
                snapshot["events"].push_back(std::move(event));
            }
        } else {
            snapshot["events"] = std::move(item_state["events"]);
        }
    }

    // ─── Handler: battle_create ─────────────────────────────────────

    v2::service::BackendEnvelope handle_battle_create(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() ||
            !decoded->payload.contains("battle_id") ||
            !decoded->payload.contains("room_id") || !decoded->payload.contains("player_ids")) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        std::string battle_id = doc["battle_id"].get<std::string>();
        std::string room_id = doc["room_id"].get<std::string>();
        auto player_ids_json = doc["player_ids"];
        std::uint32_t max_frames = doc.value("max_frames", 0);

        if (battle_id.empty() || room_id.empty()) {
            return make_error(-1004, "empty_fields");
        }
        if (!player_ids_json.is_array() || player_ids_json.size() < 2) {
            return make_error(-1004, "need_at_least_two_players");
        }

        std::vector<v2::realtime::PlayerContext> players;
        for (const auto& pid : player_ids_json) {
            v2::realtime::PlayerContext player;
            player.user_id = pid.get<std::string>();
            players.push_back(std::move(player));
        }

        // Create the instance via InstanceRuntime
        auto instance_id = runtime_.create_instance(
            battle_id, room_id, instance_type_, players,
            33,               // tick_interval_ms (~30 Hz)
            max_frames,
            30000);           // resume_window_ms

        if (instance_id.empty()) {
            return make_error(-2004, "battle_already_exists");
        }

        set_instance_frame(battle_id, 0);

        // Try to load archive snapshot if archive store is configured
        if (archive_store_) {
            try {
                auto snapshot = archive_store_->load_snapshot(battle_id);
                if (snapshot.has_value() && !snapshot->empty()) {
                    std::cout << "v2_battle_backend: restored snapshot for battle "
                              << battle_id << std::endl;
                }
            } catch (const std::exception& e) {
                std::cout << "v2_battle_backend: failed to load archive for battle "
                          << battle_id << ": " << e.what() << std::endl;
            }
        }

        nlohmann::json push{
            {"kind", "battle_started"},
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"player_ids", player_ids_json},
        };

        auto resp = make_ok({
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"player_ids", player_ids_json},
            {"push_to_sessions", nlohmann::json::array({std::move(push)})},
        });
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kBattleCreateResponse);
    }

    // ─── Handler: battle_input ──────────────────────────────────────

    v2::service::BackendEnvelope handle_battle_input(
        const v2::service::BackendEnvelope& request) {
        std::scoped_lock input_lock(battle_input_mutex_);
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() ||
            !decoded->payload.contains("user_id") ||
            !decoded->payload.contains("battle_id") ||
            !decoded->payload.contains("input_data")) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        std::string user_id = doc["user_id"].get<std::string>();
        std::string battle_id = doc["battle_id"].get<std::string>();
        std::string input_data = doc["input_data"].get<std::string>();
        const auto score = doc.value("score", std::int64_t{0});
        const auto submitted_frame = doc.value("submitted_frame", std::uint32_t{0});
        const auto pickup_item_id = parse_pickup_input(input_data);

        // Find instance first to check existence
        auto* instance_ctx = runtime_.find_instance(battle_id);
        if (instance_ctx == nullptr) {
            return make_error(-2003, "battle_not_found");
        }

        // Build and submit input
        v2::realtime::InputEnvelope input;
        input.instance_id = battle_id;
        input.user_id = user_id;
        input.payload = input_data;
        input.score = score;
        input.submitted_frame = submitted_frame;

        auto input_result = runtime_.process_input_immediate(input);
        if (!input_result.accepted) {
            return make_error(-3002, input_result.reject_reason);
        }

        // Tick the instance by one frame
        auto current_frame = get_instance_frame(battle_id);
        auto next_frame = current_frame + 1;

        auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();

        auto tick_stats = runtime_.tick_instance(
            battle_id, next_frame, now_ms);

        set_instance_frame(battle_id, tick_stats.frame_number);

        // Get snapshot from the event callback
        auto snapshot = sync_capture_.consume_snapshot(battle_id);
        auto snapshot_json = nlohmann::json::parse(snapshot.payload, nullptr, false);
        if (!snapshot_json.is_discarded()) {
            const auto expected_item_id = item_id_for(battle_id).get<std::string>();
            enrich_snapshot_with_items(snapshot_json,
                                       battle_id,
                                       pickup_item_id == expected_item_id
                                           ? std::optional<std::string>(user_id)
                                           : std::nullopt);
            apply_speed_buff_if_present(snapshot_json);
            snapshot.payload = snapshot_json.dump();
            set_latest_snapshot(battle_id, tick_stats.frame_number, snapshot.payload);
        } else if (!snapshot.payload.empty()) {
            set_latest_snapshot(battle_id, tick_stats.frame_number, snapshot.payload);
        }
        nlohmann::json replay_entry = {
            {"battle_id", battle_id},
            {"frame_number", tick_stats.frame_number},
            {"timestamp", now_ms},
        };
        if (!snapshot_json.is_discarded()) {
            replay_entry["snapshot"] = snapshot_json;
        }
        append_replay_frame(battle_id, tick_stats.frame_number, replay_entry);

        // Build push events
        nlohmann::json pushes = nlohmann::json::array();

        nlohmann::json frame_push{
            {"kind", "frame_advanced"},
            {"battle_id", battle_id},
            {"frame_number", tick_stats.frame_number},
            {"trigger",
             "input:" + user_id + ":" + std::to_string(input_result.ack_seq)},
        };

        // Enrich with participant state from the snapshot payload
        auto participants = extract_participants_from_snapshot(snapshot.payload);
        if (!participants.empty()) {
            frame_push["participants"] = std::move(participants);
        }
        if (!snapshot_json.is_discarded()) {
            if (snapshot_json.contains("items")) {
                frame_push["items"] = snapshot_json["items"];
            }
            if (snapshot_json.contains("events")) {
                frame_push["events"] = snapshot_json["events"];
            }
            if (snapshot_json.contains("buffs")) {
                frame_push["buffs"] = snapshot_json["buffs"];
            }
            if (snapshot_json.contains("bullets")) {
                frame_push["bullets"] = snapshot_json["bullets"];
            }
        }
        pushes.push_back(std::move(frame_push));

        // If the tick triggered a finish, include the battle_finished push
        if (tick_stats.should_finish) {
            auto settlement_str = sync_capture_.consume_settlement(battle_id);

            nlohmann::json finish_push{
                {"kind", "battle_finished"},
                {"battle_id", battle_id},
                {"reason", "finished"},
                {"total_frames", tick_stats.frame_number},
            };

            if (!settlement_str.empty()) {
                auto s = nlohmann::json::parse(settlement_str, nullptr, false);
                if (!s.is_discarded()) {
                    if (s.contains("winner_user_id") &&
                        !s["winner_user_id"].is_null()) {
                        finish_push["winner_user_id"] = s["winner_user_id"];
                    }
                    if (s.contains("scores")) {
                        finish_push["scores"] = s["scores"];
                    }
                    if (s.contains("reason") && s["reason"].is_string()) {
                        finish_push["reason"] = s["reason"];
                    }
                }
            }

            pushes.push_back(std::move(finish_push));

            // Archive final state if archive store is configured
            if (archive_store_) {
                try {
                    archive_store_->save_snapshot(battle_id, snapshot.payload);
                    if (!settlement_str.empty()) {
                        archive_store_->save_result(battle_id, settlement_str);
                    }
                    std::cout << "v2_battle_backend: archived battle "
                              << battle_id << " (" << tick_stats.frame_number
                              << " frames)" << std::endl;
                } catch (const std::exception& e) {
                    std::cout << "v2_battle_backend: failed to archive battle "
                              << battle_id << ": " << e.what() << std::endl;
                }
            }

            // Store replay frame if replay storage is configured
            if (replay_storage_) {
                try {
                    replay_storage_->store_replay(battle_id, replay_entry);
                } catch (const std::exception& e) {
                    std::cout << "v2_battle_backend: failed to store replay for "
                              << battle_id << ": " << e.what() << std::endl;
                }
            }
        }

        auto resp = make_ok({
            {"battle_id", battle_id},
            {"input_seq", input_result.ack_seq},
            {"frame_number", tick_stats.frame_number},
            {"should_finish", tick_stats.should_finish},
            {"push_to_sessions", std::move(pushes)},
        });

        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kBattleInputResponse);
    }

    // ─── Handler: battle_state ──────────────────────────────────────

    v2::service::BackendEnvelope handle_battle_state(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() || !decoded->payload.contains("battle_id")) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        const auto battle_id = doc["battle_id"].get<std::string>();
        if (battle_id.empty()) {
            return make_error(-1004, "empty_battle_id");
        }

        auto* instance_ctx = runtime_.find_instance(battle_id);
        auto frame_number = get_instance_frame(battle_id);
        std::optional<CachedBattleSnapshot> cached;
        if (instance_ctx != nullptr) {
            const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch()).count();
            if (should_tick_instance(battle_id, now_ms)) {
                const auto next_frame = frame_number + 1;
                const auto tick_stats = runtime_.tick_instance(battle_id, next_frame, now_ms);
                frame_number = tick_stats.frame_number;
                set_instance_frame(battle_id, frame_number);

                auto snapshot = sync_capture_.consume_snapshot(battle_id);
                auto payload = nlohmann::json::parse(snapshot.payload, nullptr, false);
                if (!payload.is_discarded()) {
                    enrich_snapshot_with_items(payload, battle_id);
                    apply_speed_buff_if_present(payload);
                    snapshot.payload = payload.dump();
                    set_latest_snapshot(battle_id, frame_number, snapshot.payload);
                } else if (!snapshot.payload.empty()) {
                    set_latest_snapshot(battle_id, frame_number, snapshot.payload);
                }
            }
        }
        cached = latest_snapshot(battle_id);
        if (instance_ctx == nullptr && !cached.has_value()) {
            return make_error(-2003, "battle_not_found");
        }

        nlohmann::json state{
            {"kind", "frame_advanced"},
            {"battle_id", battle_id},
            {"frame_number", frame_number},
            {"is_resume", true},
        };

        if (cached.has_value() && !cached->payload.empty()) {
            auto payload = nlohmann::json::parse(cached->payload, nullptr, false);
            if (!payload.is_discarded()) {
                enrich_snapshot_with_items(payload, battle_id);
                apply_speed_buff_if_present(payload);
                state["snapshot"] = std::move(payload);
            }
            auto participants = extract_participants_from_snapshot(cached->payload);
            if (!participants.empty()) {
                state["participants"] = std::move(participants);
            }
        }

        auto resp = make_ok({
            {"battle_id", battle_id},
            {"frame_number", frame_number},
            {"snapshot", std::move(state)},
        });
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kBattleStateResponse);
    }

    // ─── Handler: battle_finish ─────────────────────────────────────

    v2::service::BackendEnvelope handle_battle_finish(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() ||
            !decoded->payload.contains("user_id") ||
            !decoded->payload.contains("battle_id")) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        std::string user_id = doc["user_id"].get<std::string>();
        std::string battle_id = doc["battle_id"].get<std::string>();
        bool has_reason = doc.contains("reason");

        // Request the runtime to finish the instance
        // finish_instance() calls build_settlement() and emits the
        // kInstanceFinished event synchronously, so the settlement
        // will be available for consumption immediately after.
        runtime_.finish_instance(
            battle_id,
            v2::realtime::FinishReason::kUserRequested);

        // Consume the settlement captured by the event callback
        auto settlement_str = sync_capture_.consume_settlement(battle_id);

        auto total_frames = get_instance_frame(battle_id);
        erase_instance_frame(battle_id);

        // Archive final state if archive store is configured
        if (archive_store_) {
            try {
                if (!settlement_str.empty()) {
                    archive_store_->save_result(battle_id, settlement_str);
                }
                std::cout << "v2_battle_backend: archived battle "
                          << battle_id << " from finish ("
                          << total_frames << " frames)" << std::endl;
            } catch (const std::exception& e) {
                std::cout << "v2_battle_backend: failed to archive battle "
                          << battle_id << ": " << e.what() << std::endl;
            }
        }

        nlohmann::json push{
            {"kind", "battle_finished"},
            {"battle_id", battle_id},
            {"reason", has_reason ? "user_requested" : "finished"},
            {"total_frames", total_frames},
        };

        if (!settlement_str.empty()) {
            auto s = nlohmann::json::parse(settlement_str, nullptr, false);
            if (!s.is_discarded()) {
                if (s.contains("winner_user_id") &&
                    !s["winner_user_id"].is_null()) {
                    push["winner_user_id"] = s["winner_user_id"];
                }
                if (s.contains("scores")) {
                    push["scores"] = s["scores"];
                }
                if (s.contains("reason") && s["reason"].is_string()) {
                    push["reason"] = s["reason"];
                }
            }
        }

        auto resp = make_ok({
            {"battle_id", battle_id},
            {"reason", push["reason"]},
            {"total_frames", total_frames},
            {"push_to_sessions", nlohmann::json::array({std::move(push)})},
        });
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kBattleFinishResponse);
    }

    // ─── Handler: replay_load ───────────────────────────────────────

    v2::service::BackendEnvelope handle_replay_load(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value() || !decoded->payload.is_object() || !decoded->payload.contains("battle_id")) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        const auto battle_id = doc["battle_id"].get<std::string>();
        if (battle_id.empty()) {
            return make_error(-1004, "empty_battle_id");
        }

        if (replay_storage_) {
            replay_storage_->flush();
            if (auto stored = replay_storage_->get_replay(battle_id)) {
                auto resp = make_ok({
                    {"battle_id", battle_id},
                    {"replay", std::move(*stored)},
                });
                return v2::service::wrap_typed_response_if_needed(
                    decoded->typed_request,
                    std::move(resp),
                    v3::proto::EnvelopeMessageKind::kReplayLoadResponse);
            }
        }

        if (auto cached = replay_doc(battle_id)) {
            auto resp = make_ok({
                {"battle_id", battle_id},
                {"replay", std::move(*cached)},
            });
            return v2::service::wrap_typed_response_if_needed(
                decoded->typed_request,
                std::move(resp),
                v3::proto::EnvelopeMessageKind::kReplayLoadResponse);
        }

        return make_error(-2003, "replay_not_found");
    }
};

BattleBackendService::BattleBackendService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}

BattleBackendService::~BattleBackendService() = default;

void BattleBackendService::start() { impl_->start(); }
void BattleBackendService::stop() { impl_->stop(); }
std::uint16_t BattleBackendService::local_port() const { return impl_->local_port(); }

void BattleBackendService::set_tls_config(
    std::optional<v3::cluster::TlsSessionConfig> tls_config) {
    impl_->set_tls_config(std::move(tls_config));
}

void BattleBackendService::set_instance_type(const std::string& type) {
    impl_->set_instance_type(type);
}

void BattleBackendService::set_archive_store(
    std::unique_ptr<v2::data::CachedBattleDataStore> store) {
    impl_->set_archive_store(std::move(store));
}

void BattleBackendService::set_archive_path(const std::string& path) {
    impl_->set_archive_path(path);
}

void BattleBackendService::set_replay_storage_dir(const std::string& path) {
    impl_->set_replay_storage_dir(path);
}

}  // namespace v2::battle
