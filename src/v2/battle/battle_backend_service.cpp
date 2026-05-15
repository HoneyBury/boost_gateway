#include "v2/battle/battle_backend_service.h"
#include "v2/battle/message_types.h"
#include "v2/battle/runtime_world.h"
#include "v2/ecs/world.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v3/proto/envelope_codec.h"

#include <nlohmann/json.hpp>

#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

struct BattleManager {
    std::unordered_map<std::string, std::unique_ptr<v2::ecs::World>> battles_;
    std::mutex mutex_;

    v2::ecs::World* find(const std::string& battle_id) {
        auto it = battles_.find(battle_id);
        return it != battles_.end() ? it->second.get() : nullptr;
    }

    void insert(const std::string& battle_id, std::unique_ptr<v2::ecs::World> world) {
        battles_[battle_id] = std::move(world);
    }

    void erase(const std::string& battle_id) {
        battles_.erase(battle_id);
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

namespace v2::battle {

class BattleBackendService::Impl {
public:
    explicit Impl(std::uint16_t port) : port_(port) {}

    void start() {
        v2::service::BackendServer::HandlerMap handlers;
        handlers["battle_create"] = [this](const auto& req) { return handle_battle_create(req); };
        handlers["battle_input"] = [this](const auto& req) { return handle_battle_input(req); };
        handlers["battle_finish"] = [this](const auto& req) { return handle_battle_finish(req); };

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
    BattleManager battle_manager_;

    v2::service::BackendEnvelope handle_battle_create(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("battle_id") || !doc.contains("room_id") ||
            !doc.contains("player_ids")) {
            return make_error(-1004, "invalid_json");
        }

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

        std::vector<std::string> player_ids;
        for (const auto& pid : player_ids_json) {
            player_ids.push_back(pid.get<std::string>());
        }

        std::lock_guard<std::mutex> lock(battle_manager_.mutex_);

        if (battle_manager_.find(battle_id) != nullptr) {
            return make_error(-2004, "battle_already_exists");
        }

        auto world = create_battle_world(battle_id, room_id, player_ids, max_frames);
        battle_manager_.insert(battle_id, std::move(world));

        nlohmann::json push{
            {"kind", "battle_started"},
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"player_ids", player_ids_json},
        };

        return make_ok({
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"player_ids", player_ids_json},
            {"push_to_sessions", nlohmann::json::array({std::move(push)})},
        });
    }

    v2::service::BackendEnvelope handle_battle_input(
        const v2::service::BackendEnvelope& request) {
        auto decoded_envelope = v3::proto::decode_typed_envelope(request.payload);
        auto raw_payload = decoded_envelope.has_value() ? decoded_envelope->payload.dump() : request.payload;
        auto doc = nlohmann::json::parse(raw_payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("battle_id") ||
            !doc.contains("input_data")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string battle_id = doc["battle_id"].get<std::string>();
        std::string input_data = doc["input_data"].get<std::string>();
        std::int64_t score = doc.value("score", 0);
        std::uint32_t submitted_frame = doc.value("submitted_frame", 0);

        std::lock_guard<std::mutex> lock(battle_manager_.mutex_);

        auto* world = battle_manager_.find(battle_id);
        if (world == nullptr) {
            return make_error(-2003, "battle_not_found");
        }

        // Process input authoritatively
        auto input_result = battle_world_process_input(
            *world, user_id, input_data, score, submitted_frame);

        if (!input_result.accepted) {
            return make_error(-3002, input_result.reject_reason);
        }

        // Advance one frame
        auto current_frame = battle_world_frame_number(*world);
        auto next_frame = current_frame + 1;
        auto frame_result = battle_world_advance_frame(
            *world, next_frame, "input:" + user_id + ":" + std::to_string(input_result.input_seq));

        // Build push events
        nlohmann::json pushes = nlohmann::json::array();
        auto snapshot = battle_world_snapshot(*world);

        nlohmann::json frame_push{
            {"kind", "frame_advanced"},
            {"battle_id", battle_id},
            {"frame_number", frame_result.frame_number},
            {"trigger", frame_result.trigger},
        };

        // Enrich with participant state
        nlohmann::json participants_json = nlohmann::json::array();
        for (const auto& p : snapshot.participants) {
            participants_json.push_back({
                {"user_id", p.user_id},
                {"online", p.online},
                {"score", p.score},
                {"pos_x", p.pos_x},
                {"pos_y", p.pos_y},
                {"hp", p.hp},
                {"max_hp", p.max_hp},
            });
        }
        frame_push["participants"] = std::move(participants_json);
        pushes.push_back(std::move(frame_push));

        if (frame_result.should_finish) {
            auto participants = battle_world_participants(*world);
            auto summary = battle_world_build_result_summary(
                *world, battle_id,
                battle_world_room_id(*world),
                participants,
                frame_result.finish_reason,
                frame_result.frame_number);

            battle_world_set_lifecycle(
                *world, v2::battle::BattleLifecycleState::kFinished);

            nlohmann::json finish_push{
                {"kind", "battle_finished"},
                {"battle_id", battle_id},
                {"reason", v2::battle::to_string(frame_result.finish_reason)},
                {"total_frames", snapshot.clock.frame_number},
            };
            if (summary.winner_user_id.has_value()) {
                finish_push["winner_user_id"] = *summary.winner_user_id;
            }
            nlohmann::json scores_json = nlohmann::json::array();
            for (const auto& s : summary.scores) {
                scores_json.push_back({{"user_id", s.user_id}, {"score", s.score}});
            }
            finish_push["scores"] = std::move(scores_json);
            pushes.push_back(std::move(finish_push));
        }

        auto resp = make_ok({
            {"battle_id", battle_id},
            {"input_seq", input_result.input_seq},
            {"frame_number", frame_result.frame_number},
            {"should_finish", frame_result.should_finish},
            {"push_to_sessions", std::move(pushes)},
        });
        resp.payload = v3::proto::maybe_wrap_typed_response(
            decoded_envelope,
            v3::proto::EnvelopeMessageKind::kBattleInputResponse,
            nlohmann::json::parse(resp.payload, nullptr, false));
        return resp;
    }

    v2::service::BackendEnvelope handle_battle_finish(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("battle_id")) {
            return make_error(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string battle_id = doc["battle_id"].get<std::string>();
        auto reason = doc.contains("reason")
            ? v2::battle::BattleFinishReason::kUserRequested
            : v2::battle::BattleFinishReason::kFinished;

        std::lock_guard<std::mutex> lock(battle_manager_.mutex_);

        auto* world = battle_manager_.find(battle_id);
        if (world == nullptr) {
            return make_error(-2003, "battle_not_found");
        }

        auto participants = battle_world_participants(*world);
        auto frame_number = battle_world_frame_number(*world);
        auto summary = battle_world_build_result_summary(
            *world, battle_id, battle_world_room_id(*world),
            participants, reason, frame_number);

        battle_world_set_lifecycle(
            *world, v2::battle::BattleLifecycleState::kFinished);

        nlohmann::json push{
            {"kind", "battle_finished"},
            {"battle_id", battle_id},
            {"reason", v2::battle::to_string(reason)},
            {"total_frames", frame_number},
        };
        if (summary.winner_user_id.has_value()) {
            push["winner_user_id"] = *summary.winner_user_id;
        }
        nlohmann::json scores_json = nlohmann::json::array();
        for (const auto& s : summary.scores) {
            scores_json.push_back({{"user_id", s.user_id}, {"score", s.score}});
        }
        push["scores"] = std::move(scores_json);

        return make_ok({
            {"battle_id", battle_id},
            {"reason", v2::battle::to_string(reason)},
            {"total_frames", frame_number},
            {"push_to_sessions", nlohmann::json::array({std::move(push)})},
        });
    }
};

BattleBackendService::BattleBackendService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}

BattleBackendService::~BattleBackendService() = default;

void BattleBackendService::start() { impl_->start(); }
void BattleBackendService::stop() { impl_->stop(); }
std::uint16_t BattleBackendService::local_port() const { return impl_->local_port(); }

}  // namespace v2::battle
