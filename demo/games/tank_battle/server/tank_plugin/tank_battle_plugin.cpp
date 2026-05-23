// TankBattlePlugin — ECS-based tank battle plugin (moved from src/v2/battle/).
// This plugin implements InstancePlugin SPI for tank battle simulation.
// It is part of the tank demo, not the default framework build.
// Build with: cmake -B build -DBOOST_BUILD_TANK_DEMO=ON

#include "v2/battle/tank_battle_plugin.h"

#include "v2/battle/game_systems.h"
#include "v2/battle/runtime_components.h"
#include "v2/ecs/parallel_system_executor.h"

#include <nlohmann/json.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <cstdlib>
#include <random>
#include <string>
#include <utility>

namespace {

std::string generate_uuid_v4() {
    static thread_local std::mt19937 gen(std::random_device{}());
    static thread_local std::uniform_int_distribution<int> dist(0, 15);
    std::array<char, 36> uuid;
    const char* hex = "0123456789abcdef";
    for (int i = 0; i < 36; i++) {
        if (i == 8 || i == 13 || i == 18 || i == 23) { uuid[i] = '-'; continue; }
        int v = dist(gen);
        if (i == 14) v = 4;  // version 4
        if (i == 19) v = 8 | (v & 3);  // variant
        uuid[i] = hex[v];
    }
    return std::string(uuid.data(), 36);
}

}  // namespace

namespace v2::battle {

// ─── Internal helpers ─────────────────────────────────────────────────

using nlohmann::json;

TankBattlePlugin::State& TankBattlePlugin::get_state(
    v2::realtime::InstanceContext& instance_ctx) {
    auto* state = static_cast<State*>(instance_ctx.plugin_state);
    return *state;
}

const TankBattlePlugin::State& TankBattlePlugin::get_state(
    const v2::realtime::InstanceContext& instance_ctx) const {
    auto* state = static_cast<const State*>(instance_ctx.plugin_state);
    return *state;
}

void TankBattlePlugin::create_player_entity(
    State& state, const std::string& user_id) {
    auto entity = state.world->create_entity();

    {
        BattleParticipantComponent c;
        c.user_id = user_id;
        c.online = true;
        state.world->add_component<BattleParticipantComponent>(entity, std::move(c));
    }

    state.world->add_component<PositionComponent>(entity);
    state.world->add_component<HealthComponent>(entity);
    state.world->add_component<AttackStateComponent>(entity);

    {
        AttackCooldownComponent c;
        c.cooldown_frames = 0;  // No cooldown between attacks
        state.world->add_component<AttackCooldownComponent>(entity, std::move(c));
    }

    state.world->add_component<BattleClockComponent>(entity);

    state.player_entities[user_id] = entity;
}

// ─── Lifecycle hooks ─────────────────────────────────────────────────

void TankBattlePlugin::on_instance_created(
    v2::realtime::InstanceContext& instance_ctx) {
    auto state = std::make_unique<State>();
    state->world = std::make_unique<v2::ecs::SimpleWorld>();

    // Create executor and register ECS systems with stage dependencies
    auto executor = std::make_unique<v2::ecs::ParallelSystemExecutor>();

    // Stage 0: no dependencies
    executor->add_system(std::make_unique<MovementSystem>(),
        v2::ecs::SystemMetadata{.name = "MovementSystem"});
    executor->add_system(std::make_unique<BattleClockSystem>(),
        v2::ecs::SystemMetadata{.name = "BattleClockSystem"});

    // Stage 1: depends on MovementSystem
    executor->add_system(std::make_unique<CombatSystem>(),
        v2::ecs::SystemMetadata{.name = "CombatSystem", .dependencies = {"MovementSystem"}});

    // Stage 2: depends on CombatSystem
    executor->add_system(std::make_unique<ProjectileSystem>(),
        v2::ecs::SystemMetadata{.name = "ProjectileSystem", .dependencies = {"CombatSystem"}});

    state->world->set_executor(std::move(executor));

    // Create entities for each player
    for (const auto& player : instance_ctx.players) {
        create_player_entity(*state, player.user_id);
    }

    instance_ctx.plugin_state = state.release();
}

void TankBattlePlugin::on_player_join(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::PlayerContext& player) {
    auto& state = get_state(instance_ctx);

    // Do nothing if player already exists
    if (state.player_entities.find(player.user_id) !=
        state.player_entities.end()) {
        return;
    }

    create_player_entity(state, player.user_id);
}

void TankBattlePlugin::on_player_leave(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::PlayerContext& player) {
    auto& state = get_state(instance_ctx);

    auto it = state.player_entities.find(player.user_id);
    if (it == state.player_entities.end()) return;

    auto* participant = state.world->get_component<BattleParticipantComponent>(
        it->second);
    if (participant != nullptr) {
        participant->online = false;
    }
}

// ─── Input processing ────────────────────────────────────────────────

v2::realtime::InputResult TankBattlePlugin::on_input(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::InputEnvelope& input) {
    auto& state = get_state(instance_ctx);

    // Parse JSON payload
    json payload;
    try {
        payload = json::parse(input.payload);
    } catch (const json::parse_error&) {
        return v2::realtime::InputResult{
            .accepted = true,   // Accept structurally but ECS will ignore
        };
    }

    // Validate action field
    auto action_it = payload.find("action");
    if (action_it == payload.end() || !action_it->is_string()) {
        return v2::realtime::InputResult{
            .accepted = true,
        };
    }

    std::string action = action_it->get<std::string>();

    // Look up the player's entity
    auto entity_it = state.player_entities.find(input.user_id);
    if (entity_it == state.player_entities.end()) {
        // Player not in this instance
        return v2::realtime::InputResult{
            .accepted = false,
            .reject_reason = "player_not_found",
        };
    }

    auto* participant = state.world->get_component<BattleParticipantComponent>(
        entity_it->second);
    if (participant == nullptr || !participant->online) {
        return v2::realtime::InputResult{
            .accepted = false,
            .reject_reason = "player_not_available",
        };
    }

    if (action == "move") {
        // {"action":"move","x":10,"y":20}
        auto x_it = payload.find("x");
        auto y_it = payload.find("y");
        if (x_it == payload.end() || y_it == payload.end() ||
            !x_it->is_number_integer() || !y_it->is_number_integer()) {
            return v2::realtime::InputResult{
                .accepted = true,   // Accept; ECS MovementSystem will validate bounds
            };
        }

        participant->pending_move_x = x_it->get<std::int32_t>();
        participant->pending_move_y = y_it->get<std::int32_t>();
        participant->has_pending_move = true;

        return v2::realtime::InputResult{.accepted = true};
    }

    if (action == "attack") {
        // {"action":"attack","target_user_id":"bob"}
        auto target_it = payload.find("target_user_id");
        if (target_it == payload.end() || !target_it->is_string()) {
            return v2::realtime::InputResult{
                .accepted = true,
            };
        }

        participant->pending_target_user_id = target_it->get<std::string>();

        return v2::realtime::InputResult{.accepted = true};
    }

    if (action == "shoot") {
        // {"action":"shoot","target_user_id":"bob","aoe_radius":0,"duration_frames":0,"speed":50}
        auto target_it = payload.find("target_user_id");
        if (target_it == payload.end() || !target_it->is_string()) {
            return v2::realtime::InputResult{
                .accepted = true,
            };
        }
        std::string target_user_id = target_it->get<std::string>();

        // Get shooter's position for spawn coordinates
        auto* shooter_pos = state.world->get_component<PositionComponent>(
            entity_it->second);
        if (shooter_pos == nullptr) {
            return v2::realtime::InputResult{.accepted = true};
        }

        // Find target entity and get its position
        auto target_entity_it = state.player_entities.find(target_user_id);
        if (target_entity_it == state.player_entities.end()) {
            return v2::realtime::InputResult{.accepted = true};
        }
        auto* target_pos = state.world->get_component<PositionComponent>(
            target_entity_it->second);
        if (target_pos == nullptr) {
            return v2::realtime::InputResult{.accepted = true};
        }

        // Get damage from shooter's attack state
        auto* attack_state = state.world->get_component<AttackStateComponent>(
            entity_it->second);
        std::int32_t damage = (attack_state != nullptr)
            ? attack_state->damage : 10;

        // Parse optional fields
        std::int32_t aoe_radius = 0;
        auto aoe_it = payload.find("aoe_radius");
        if (aoe_it != payload.end() && aoe_it->is_number_integer()) {
            aoe_radius = aoe_it->get<std::int32_t>();
        }

        std::uint32_t duration_frames = 0;
        auto dur_it = payload.find("duration_frames");
        if (dur_it != payload.end() && dur_it->is_number_integer()) {
            duration_frames = dur_it->get<std::uint32_t>();
        }

        std::int32_t speed = 50;
        auto speed_it = payload.find("speed");
        if (speed_it != payload.end() && speed_it->is_number_integer()) {
            speed = speed_it->get<std::int32_t>();
        }

        // Generate unique projectile ID
        auto proj_id = generate_uuid_v4();

        // Spawn the projectile
        ProjectileSystem::spawn_projectile(
            *state.world,
            entity_it->second,
            proj_id,
            input.user_id,
            target_user_id,
            shooter_pos->x, shooter_pos->y,
            target_pos->x, target_pos->y,
            damage,
            speed,
            aoe_radius,
            duration_frames);

        return v2::realtime::InputResult{.accepted = true};
    }

    if (action == "finish") {
        // {"action":"finish","reason":"surrender"}
        state.finish_requested = true;
        return v2::realtime::InputResult{.accepted = true};
    }

    // Unknown action — accept but no-op
    return v2::realtime::InputResult{.accepted = true};
}

// ─── Tick / simulation ───────────────────────────────────────────────

v2::realtime::TickStats TankBattlePlugin::on_tick(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::FrameContext& frame_ctx) noexcept {
    auto& state = get_state(instance_ctx);

    try {
        // Reset per-frame attack counts so each frame allows one attack per entity
        state.world->for_each<AttackCooldownComponent>(
            [](v2::ecs::EntityHandle, AttackCooldownComponent& cd) {
                cd.attacks_this_frame = 0;
            });

        // Build ECS FrameContext from the realtime FrameContext
        v2::ecs::FrameContext ecs_ctx;
        ecs_ctx.battle_id = instance_ctx.instance_id;
        ecs_ctx.room_id = instance_ctx.room_id;
        ecs_ctx.frame_number = frame_ctx.frame_number;
        ecs_ctx.trigger = "tick";
        ecs_ctx.tick_interval = std::chrono::milliseconds(
            instance_ctx.tick_interval_ms);

        // Advance the ECS world through all registered systems
        state.world->tick(ecs_ctx);

        // Build tick result
        v2::realtime::TickStats stats;
        stats.frame_number = frame_ctx.frame_number;
        stats.inputs_processed = static_cast<std::uint32_t>(
            frame_ctx.inputs_this_tick.size());
        stats.pushes_sent = 0;
        stats.tick_duration_ms = 0.5;

        // ── Finish conditions ────────────────────────────────────

        // Check if all players are dead (HP <= 0)
        bool all_dead = true;
        bool has_players = false;

        state.world->for_each<BattleParticipantComponent>(
            [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& p) {
                has_players = true;
                if (!p.online) return;
                auto* health = state.world->get_component<HealthComponent>(handle);
                if (health != nullptr && health->hp > 0) {
                    all_dead = false;
                }
            });

        if (has_players && all_dead) {
            stats.should_finish = true;
            stats.finish_reason = v2::realtime::FinishReason::kAllPlayersDone;
            return stats;
        }

        // Check if a player requested finish
        if (state.finish_requested) {
            stats.should_finish = true;
            stats.finish_reason = v2::realtime::FinishReason::kUserRequested;
            state.finish_requested = false;
            return stats;
        }

        // Check frame limit (as a safety net; the runtime also checks)
        if (instance_ctx.max_frames > 0 &&
            frame_ctx.frame_number >= instance_ctx.max_frames) {
            stats.should_finish = true;
            stats.finish_reason = v2::realtime::FinishReason::kFrameLimit;
            return stats;
        }

        return stats;

    } catch (...) {
        // All errors caught — noexcept contract must not throw
        v2::realtime::TickStats stats;
        stats.frame_number = frame_ctx.frame_number;
        stats.should_finish = true;
        stats.finish_reason = v2::realtime::FinishReason::kError;
        return stats;
    }
}

// ─── Snapshot / settlement ───────────────────────────────────────────

v2::realtime::Snapshot TankBattlePlugin::build_snapshot(
    v2::realtime::InstanceContext& instance_ctx,
    bool is_resume) noexcept {
    const auto& state = get_state(instance_ctx);

    try {
        json players = json::array();

        state.world->for_each<BattleParticipantComponent>(
            [&](v2::ecs::EntityHandle handle, const BattleParticipantComponent& p) {
                json player;
                player["user_id"] = p.user_id;
                player["score"] = p.score;
                player["online"] = p.online;

                auto* pos = state.world->get_component<PositionComponent>(handle);
                if (pos != nullptr) {
                    player["x"] = pos->x;
                    player["y"] = pos->y;
                } else {
                    player["x"] = 0;
                    player["y"] = 0;
                }

                auto* health = state.world->get_component<HealthComponent>(handle);
                if (health != nullptr) {
                    player["hp"] = health->hp;
                    player["max_hp"] = health->max_hp;
                } else {
                    player["hp"] = 0;
                    player["max_hp"] = 0;
                }

                players.push_back(std::move(player));
            });

        json snapshot;
        snapshot["type"] = "tank.snapshot";
        snapshot["players"] = std::move(players);

        // Determine frame from the clock component
        std::uint32_t frame = 0;
        state.world->for_each<BattleClockComponent>(
            [&](v2::ecs::EntityHandle, const BattleClockComponent& clock) {
                frame = clock.frame_number;
            });
        snapshot["frame"] = frame;

        v2::realtime::Snapshot snap;
        snap.payload_type = "tank.snapshot";
        snap.payload = snapshot.dump();
        snap.is_full = true;
        snap.is_resume = is_resume;
        return snap;

    } catch (...) {
        v2::realtime::Snapshot snap;
        snap.payload_type = "tank.snapshot";
        snap.payload = R"({"type":"tank.snapshot","error":"snapshot_failed"})";
        snap.is_full = true;
        snap.is_resume = is_resume;
        return snap;
    }
}

std::string TankBattlePlugin::build_settlement(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::SettlementContext& settlement_ctx) noexcept {
    const auto& state = get_state(instance_ctx);

    try {
        json players = json::array();

        // Collect all players with their final scores
        // Determine the winner: highest score wins; if tied, first found
        std::int64_t highest_score = -1;
        std::string winner_id;

        state.world->for_each<BattleParticipantComponent>(
            [&](v2::ecs::EntityHandle, const BattleParticipantComponent& p) {
                json player;
                player["user_id"] = p.user_id;
                player["score"] = p.score;

                // Winner is the player with the highest score
                if (p.score > highest_score) {
                    highest_score = p.score;
                    winner_id = p.user_id;
                }

                players.push_back(std::move(player));
            });

        // Mark the winner
        for (auto& player : players) {
            if (player["user_id"].get<std::string>() == winner_id &&
                highest_score > 0) {
                player["winner"] = true;
            } else {
                player["winner"] = false;
            }
        }

        json settlement;
        settlement["type"] = "tank.settlement";
        settlement["total_frames"] = settlement_ctx.total_frames;
        settlement["players"] = std::move(players);

        return settlement.dump();

    } catch (...) {
        return R"({"type":"tank.settlement","error":"settlement_failed"})";
    }
}

v2::realtime::Snapshot TankBattlePlugin::build_resume_snapshot(
    v2::realtime::InstanceContext& instance_ctx,
    const v2::realtime::PlayerContext& player) noexcept {
    const auto& state = get_state(instance_ctx);

    try {
        // Build a minimal snapshot for the reconnecting player
        json players = json::array();

        state.world->for_each<BattleParticipantComponent>(
            [&](v2::ecs::EntityHandle handle, const BattleParticipantComponent& p) {
                // Include all players so the reconnecting player sees the full state
                json player_entry;
                player_entry["user_id"] = p.user_id;
                player_entry["online"] = p.online;
                player_entry["score"] = p.score;

                auto* pos = state.world->get_component<PositionComponent>(handle);
                if (pos != nullptr) {
                    player_entry["x"] = pos->x;
                    player_entry["y"] = pos->y;
                } else {
                    player_entry["x"] = 0;
                    player_entry["y"] = 0;
                }

                auto* health = state.world->get_component<HealthComponent>(handle);
                if (health != nullptr) {
                    player_entry["hp"] = health->hp;
                    player_entry["max_hp"] = health->max_hp;
                } else {
                    player_entry["hp"] = 0;
                    player_entry["max_hp"] = 0;
                }

                players.push_back(std::move(player_entry));
            });

        // Get frame number from the clock component
        std::uint32_t frame = 0;
        state.world->for_each<BattleClockComponent>(
            [&](v2::ecs::EntityHandle, const BattleClockComponent& clock) {
                frame = clock.frame_number;
            });

        json snapshot;
        snapshot["type"] = "tank.snapshot";
        snapshot["players"] = std::move(players);
        snapshot["frame"] = frame;

        v2::realtime::Snapshot snap;
        snap.payload_type = "tank.snapshot";
        snap.payload = snapshot.dump();
        snap.is_full = true;
        snap.is_resume = true;
        snap.frame_number = frame;
        return snap;

    } catch (...) {
        v2::realtime::Snapshot snap;
        snap.payload_type = "tank.snapshot";
        snap.payload = R"({"type":"tank.snapshot","error":"resume_failed"})";
        snap.is_resume = true;
        return snap;
    }
}

}  // namespace v2::battle
