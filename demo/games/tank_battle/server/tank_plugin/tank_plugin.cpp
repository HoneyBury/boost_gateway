#include "tank_plugin.h"
#include "app/audit_log.h"

#include <nlohmann/json.hpp>

#include <sstream>
#include <vector>

namespace tank {

TankPlugin::TankPlugin() = default;
TankPlugin::~TankPlugin() = default;

void TankPlugin::on_instance_created(v2::realtime::InstanceContext& ctx) {
    AUDIT_LOG("tank_instance_created", "instance_id=" + ctx.instance_id);

    // Initialise world immediately so inputs can arrive before first tick
    std::vector<std::string> player_ids;
    for (const auto& p : ctx.players) {
        player_ids.push_back(p.user_id);
    }
    world_.init(player_ids, ctx.max_frames);
    initialised_ = true;
}

void TankPlugin::on_player_join(v2::realtime::InstanceContext& ctx,
                                 const v2::realtime::PlayerContext& player) {
    AUDIT_LOG("tank_player_join", "instance_id=" + ctx.instance_id +
              " user_id=" + player.user_id);
}

void TankPlugin::on_player_leave(v2::realtime::InstanceContext& ctx,
                                  const v2::realtime::PlayerContext& player) {
    AUDIT_LOG("tank_player_leave", "instance_id=" + ctx.instance_id +
              " user_id=" + player.user_id);
}

v2::realtime::InputResult TankPlugin::on_input(
    v2::realtime::InstanceContext& ctx,
    const v2::realtime::InputEnvelope& input) {

    // Parse the tank input payload
    auto doc = nlohmann::json::parse(input.payload, nullptr, false);
    if (doc.is_discarded() || !doc.is_object()) {
        return {false, "invalid_json", 0};
    }

    // Check seq ordering
    auto& last_seq = last_seqs_[input.user_id];
    std::uint64_t in_seq = doc.value("seq", 0);
    if (in_seq > 0 && in_seq <= last_seq) {
        return {false, "duplicate_seq", 0};
    }
    last_seq = in_seq;

    // Parse actions
    if (!doc.contains("actions") || !doc["actions"].is_array()) {
        return {false, "missing_actions", 0};
    }

    std::vector<InputAction> actions;
    for (const auto& action_json : doc["actions"]) {
        InputAction action;
        std::string type_str = action_json.value("type", "");
        if (type_str == "move") {
            action.type = ActionType::kMove;
            action.dx = action_json.value("dx", 0);
            action.dy = action_json.value("dy", 0);

            // Anti-cheat: validate move deltas
            auto* tank = world_.find_tank(input.user_id);
            if (tank == nullptr) {
                AUDIT_LOG("tank_error", "user_id=" + input.user_id + " not_found");
                continue;
            }
            if (!world_.validate_move(*tank, action.dx, action.dy)) {
                AUDIT_LOG("tank_cheat_move",
                          "user_id=" + input.user_id +
                          " dx=" + std::to_string(action.dx) +
                          " dy=" + std::to_string(action.dy));
                continue;  // Skip invalid move, don't reject entire input
            }
        } else if (type_str == "fire") {
            action.type = ActionType::kFire;
            action.direction = action_json.value("direction", 0);

            // Anti-cheat: validate direction
            if (!is_valid_direction(action.direction)) {
                AUDIT_LOG("tank_cheat_fire_direction",
                          "user_id=" + input.user_id +
                          " direction=" + std::to_string(action.direction));
                continue;
            }
        } else if (type_str == "stop") {
            action.type = ActionType::kStop;
        } else {
            continue;  // Unknown action type
        }
        actions.push_back(std::move(action));
    }

    // Apply actions to the world immediately (tick will advance)
    PlayerInput player_input;
    player_input.user_id = input.user_id;
    player_input.seq = in_seq;
    player_input.actions = std::move(actions);

    // We apply to the world here, but the world only advances on tick
    for (const auto& action : player_input.actions) {
        world_.apply_action(input.user_id, action);
    }

    return {true, "", in_seq};
}

v2::realtime::TickStats TankPlugin::on_tick(
    v2::realtime::InstanceContext& ctx,
    const v2::realtime::FrameContext& frame_ctx) {

    // Advance the world simulation by one tick
    // Inputs have already been applied via on_input, so we pass empty
    world_.tick({});

    v2::realtime::TickStats stats;
    stats.frame_number = world_.frame();
    stats.tick_duration_ms = 0.5;  // rough estimate

    if (world_.is_finished()) {
        stats.should_finish = true;
        stats.finish_reason = v2::realtime::FinishReason::kNormal;
    }

    return stats;
}

v2::realtime::Snapshot TankPlugin::build_snapshot(
    v2::realtime::InstanceContext& ctx, bool is_resume) {

    v2::realtime::Snapshot snap;
    snap.frame_number = world_.frame();
    snap.payload_type = "tank.snapshot";
    snap.payload = world_.snapshot().to_json().dump();
    snap.is_full = true;
    snap.is_resume = is_resume;
    return snap;
}

std::string TankPlugin::build_settlement(
    v2::realtime::InstanceContext& ctx,
    const v2::realtime::SettlementContext& sctx) {

    auto settlement = world_.build_settlement();
    settlement["instance_id"] = ctx.instance_id;
    settlement["room_id"] = ctx.room_id;
    settlement["reason"] = v2::realtime::to_string(sctx.reason);
    return settlement.dump();
}

v2::realtime::Snapshot TankPlugin::build_resume_snapshot(
    v2::realtime::InstanceContext& ctx,
    const v2::realtime::PlayerContext& player) {

    return build_snapshot(ctx, true);
}

}  // namespace tank
