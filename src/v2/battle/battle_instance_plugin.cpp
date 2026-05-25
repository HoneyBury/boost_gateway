#include "v2/battle/battle_instance_plugin.h"

#include "v2/battle/runtime_world.h"
#include "v2/battle/game_systems.h"
#include "v2/ecs/parallel_system_executor.h"

#include <nlohmann/json.hpp>

#include <string>
#include <vector>

namespace {

// ─── Finish reason helpers ───────────────────────────────────────────────

v2::realtime::FinishReason map_to_realtime(v2::battle::BattleFinishReason r) {
    using BF = v2::battle::BattleFinishReason;
    using RF = v2::realtime::FinishReason;
    switch (r) {
        case BF::kFinished:           return RF::kNormal;
        case BF::kSurrender:          return RF::kAllPlayersDone;
        case BF::kTimeout:            return RF::kTimeout;
        case BF::kFrameLimitReached:  return RF::kFrameLimit;
        case BF::kPlayerDisconnected: return RF::kPlayerDisconnected;
        case BF::kUserRequested:      return RF::kUserRequested;
    }
    return RF::kNormal;
}

v2::battle::BattleFinishReason map_to_battle(v2::realtime::FinishReason r) {
    using BF = v2::battle::BattleFinishReason;
    using RF = v2::realtime::FinishReason;
    switch (r) {
        case RF::kNormal:              return BF::kFinished;
        case RF::kAllPlayersDone:      return BF::kFinished;
        case RF::kTimeout:             return BF::kTimeout;
        case RF::kFrameLimit:          return BF::kFrameLimitReached;
        case RF::kPlayerDisconnected:  return BF::kPlayerDisconnected;
        case RF::kUserRequested:       return BF::kUserRequested;
        case RF::kError:               return BF::kFinished;
    }
    return BF::kFinished;
}

}  // namespace

namespace v2::battle {

// ─── State accessors ─────────────────────────────────────────────────────

BattleInstancePlugin::State& BattleInstancePlugin::get_state(
    v2::realtime::InstanceContext& instance_ctx) {
    auto* state = static_cast<State*>(instance_ctx.plugin_state);
    return *state;
}

const BattleInstancePlugin::State& BattleInstancePlugin::get_state(
    const v2::realtime::InstanceContext& instance_ctx) {
    auto* state = static_cast<const State*>(instance_ctx.plugin_state);
    return *state;
}

// ─── Lifecycle hooks ────────────────────────────────────────────────────

void BattleInstancePlugin::on_instance_created(
    v2::realtime::InstanceContext& instance_ctx) {
    // Extract player_ids from the instance context
    std::vector<std::string> player_ids;
    for (const auto& p : instance_ctx.players) {
        player_ids.push_back(p.user_id);
    }

    auto state = std::make_unique<State>();

    // Create full battle world via the authoritative factory function.
    // This sets up all 7 systems (BattleClock, BattleInput, Movement,
    // Combat, Aoi, BattleLifecycle, BattleReplay) + ParallelSystemExecutor.
    state->world = create_battle_world(
        instance_ctx.instance_id,
        instance_ctx.room_id,
        player_ids,
        instance_ctx.max_frames);

    // Find entity handles for each player so we can look them up later
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(state->world.get());
    if (simple_world != nullptr) {
        for (const auto& user_id : player_ids) {
            simple_world->for_each<BattleParticipantComponent>(
                [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& p) {
                    if (p.user_id == user_id) {
                        state->player_entities[user_id] = handle;
                    }
                });
        }
    }

    instance_ctx.plugin_state = state.release();
}

void BattleInstancePlugin::on_player_join(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::PlayerContext& player) {
    auto& state = get_state(instance_ctx);

    // Do nothing if player already exists
    if (state.player_entities.find(player.user_id) !=
        state.player_entities.end()) {
        return;
    }

    // Create a new entity for the joining player with standard battle components
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(state.world.get());
    if (simple_world == nullptr) return;

    auto entity = simple_world->create_entity();
    auto& participant = simple_world->add_component<BattleParticipantComponent>(entity);
    participant.user_id = player.user_id;
    participant.online = true;

    simple_world->add_component<PositionComponent>(entity);
    simple_world->add_component<HealthComponent>(entity);
    simple_world->add_component<AttackStateComponent>(entity);
    simple_world->add_component<AttackCooldownComponent>(entity);

    state.player_entities[player.user_id] = entity;
}

void BattleInstancePlugin::on_player_leave(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::PlayerContext& player) {
    auto& state = get_state(instance_ctx);

    // Use the authoritative disconnect handler which marks the participant
    // offline and checks whether the battle should finish.
    // The return value is intentionally discarded; the InstanceRuntime
    // manages instance lifecycle, not the plugin.
    (void)battle_world_handle_disconnect(*state.world, player.user_id);
}

// ─── Input processing ───────────────────────────────────────────────────

v2::realtime::InputResult BattleInstancePlugin::on_input(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::InputEnvelope& input) {
    auto& state = get_state(instance_ctx);

    // Process the input authoritatively.  Score and submitted_frame default
    // to 0 since the InputEnvelope does not carry them; the service handler
    // may apply scores separately via battle_world_apply_input_score().
    auto result = battle_world_process_input(
        *state.world,
        input.user_id,
        input.payload,
        0,  // score (not passed via InputEnvelope)
        0   // submitted_frame (not passed via InputEnvelope)
    );

    if (!result.accepted) {
        return v2::realtime::InputResult{
            .accepted = false,
            .reject_reason = result.reject_reason,
        };
    }

    return v2::realtime::InputResult{
        .accepted = true,
        .ack_seq = result.input_seq,
    };
}

// ─── Tick / simulation ──────────────────────────────────────────────────

v2::realtime::TickStats BattleInstancePlugin::on_tick(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::FrameContext& frame_ctx) noexcept {
    auto& state = get_state(instance_ctx);

    try {
        // Build trigger from the first input processed this tick (if any)
        std::string trigger = "tick";
        if (!frame_ctx.inputs_this_tick.empty()) {
            trigger = "input:" + frame_ctx.inputs_this_tick[0].user_id;
        }

        // Advance the frame via the authoritative entry point.
        // Internally this calls world.tick() which runs all 7 systems
        // (BattleClockSystem, BattleInputSystem, MovementSystem,
        // CombatSystem, AoiSystem, BattleLifecycleSystem,
        // BattleReplaySystem) via the ParallelSystemExecutor.
        auto frame_result = battle_world_advance_frame(
            *state.world, frame_ctx.frame_number, trigger);

        // After the tick, check if the BattleLifecycleSystem has set the
        // lifecycle to kFinished (e.g. idle timeout, all players offline).
        auto lifecycle = battle_world_lifecycle(*state.world);
        bool lifecycle_finished = (lifecycle == BattleLifecycleState::kFinished);

        // Also check if a player explicitly requested finish via input
        bool player_requested_finish = state.finish_requested;
        state.finish_requested = false;

        v2::realtime::TickStats stats;
        stats.frame_number = frame_result.frame_number;
        stats.inputs_processed = static_cast<std::uint32_t>(
            frame_ctx.inputs_this_tick.size());
        stats.pushes_sent = 0;
        stats.tick_duration_ms = 0.5;

        if (frame_result.should_finish || lifecycle_finished || player_requested_finish) {
            stats.should_finish = true;

            if (player_requested_finish) {
                stats.finish_reason = v2::realtime::FinishReason::kUserRequested;
            } else if (frame_result.should_finish) {
                stats.finish_reason = map_to_realtime(frame_result.finish_reason);
            } else {
                // Lifecycle system triggered finish; map to a reasonable default
                stats.finish_reason = v2::realtime::FinishReason::kNormal;
            }

            // Ensure the underlying world lifecycle is set so subsequent
            // operations see a finished state
            if (!lifecycle_finished) {
                battle_world_set_lifecycle(
                    *state.world, BattleLifecycleState::kFinished);
            }
        }

        return stats;

    } catch (...) {
        // noexcept contract: must not throw
        v2::realtime::TickStats stats;
        stats.frame_number = frame_ctx.frame_number;
        stats.should_finish = true;
        stats.finish_reason = v2::realtime::FinishReason::kError;
        return stats;
    }
}

// ─── Snapshot / settlement ──────────────────────────────────────────────

v2::realtime::Snapshot BattleInstancePlugin::build_snapshot(
    v2::realtime::InstanceContext& instance_ctx,
    bool is_resume) noexcept {
    auto& state = get_state(instance_ctx);

    try {
        auto bw_snapshot = battle_world_snapshot(*state.world);

        nlohmann::json participants_json = nlohmann::json::array();
        for (const auto& p : bw_snapshot.participants) {
            participants_json.push_back({
                {"user_id", p.user_id},
                {"online", p.online},
                {"score", p.score},
                {"pos_x", p.pos_x},
                {"pos_y", p.pos_y},
                {"direction_x", p.facing_dx},
                {"direction_y", p.facing_dy},
                {"hp", p.hp},
                {"max_hp", p.max_hp},
            });
        }
        nlohmann::json projectiles_json = nlohmann::json::array();
        for (const auto& projectile : bw_snapshot.projectiles) {
            projectiles_json.push_back({
                {"id", projectile.projectile_id},
                {"owner", projectile.owner_user_id},
                {"x", projectile.pos_x},
                {"y", projectile.pos_y},
                {"dx", projectile.dir_x},
                {"dy", projectile.dir_y},
                {"active", projectile.active},
            });
        }

        nlohmann::json j;
        j["frame"] = bw_snapshot.clock.frame_number;
        j["trigger"] = bw_snapshot.clock.last_trigger;
        j["participants"] = std::move(participants_json);
        j["bullets"] = std::move(projectiles_json);

        v2::realtime::Snapshot snap;
        snap.payload_type = "battle.snapshot";
        snap.payload = j.dump();
        snap.is_full = true;
        snap.is_resume = is_resume;
        return snap;

    } catch (...) {
        v2::realtime::Snapshot snap;
        snap.payload_type = "battle.snapshot";
        snap.payload = R"({"error":"snapshot_failed"})";
        snap.is_full = true;
        snap.is_resume = is_resume;
        return snap;
    }
}

std::string BattleInstancePlugin::build_settlement(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::SettlementContext& settlement_ctx) noexcept {
    auto& state = get_state(instance_ctx);

    try {
        auto participants = battle_world_participants(*state.world);
        auto summary = battle_world_build_result_summary(
            *state.world,
            instance_ctx.instance_id,
            instance_ctx.room_id,
            participants,
            map_to_battle(settlement_ctx.reason),
            settlement_ctx.total_frames);

        nlohmann::json j;
        j["type"] = "battle.settlement";
        j["battle_id"] = summary.battle_id;
        j["room_id"] = summary.room_id;
        j["total_frames"] = summary.total_frames;
        j["reason"] = v2::battle::to_string(summary.reason);

        if (summary.winner_user_id.has_value()) {
            j["winner_user_id"] = *summary.winner_user_id;
        }

        nlohmann::json scores_json = nlohmann::json::array();
        for (const auto& s : summary.scores) {
            scores_json.push_back({{"user_id", s.user_id}, {"score", s.score}});
        }
        j["scores"] = std::move(scores_json);

        return j.dump();

    } catch (...) {
        return R"({"type":"battle.settlement","error":"settlement_failed"})";
    }
}

v2::realtime::Snapshot BattleInstancePlugin::build_resume_snapshot(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::PlayerContext& /*player*/) noexcept {
    // For a resume snapshot, delegate to build_snapshot with is_resume=true.
    // The full snapshot includes all participants; the reconnecting client
    // is responsible for filtering its own player state.
    return build_snapshot(instance_ctx, true);
}

}  // namespace v2::battle
