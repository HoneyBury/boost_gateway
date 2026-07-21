#include "v2/battle/runtime_world.h"

#include "v2/aoi/aoi_system.h"
#include "v2/battle/game_systems.h"
#include "v2/battle/runtime_components.h"
#include "v2/ecs/parallel_system_executor.h"

#include <algorithm>
#include <cstdlib>
#include <memory>
#include <random>
#include <unordered_map>
#include <utility>

namespace v2::battle {

namespace {

v2::ecs::SimpleWorld* as_simple_world(v2::ecs::World& world) {
    return dynamic_cast<v2::ecs::SimpleWorld*>(&world);
}

std::pair<std::int32_t, std::int32_t> spawn_position_for(const std::string& battle_id,
                                                         std::size_t index) {
    std::seed_seq seed(battle_id.begin(), battle_id.end());
    std::mt19937 rng(seed);
    std::uniform_int_distribution<std::int32_t> x_dist(1, 18);
    std::uniform_int_distribution<std::int32_t> y_dist(1, 13);
    for (std::size_t i = 0; i <= index; ++i) {
        const auto x = x_dist(rng) * 50;
        const auto y = y_dist(rng) * 50;
        if (i == index) {
            return {x, y};
        }
    }
    return {50, 50};
}

std::pair<std::int32_t, std::int32_t> normalize_direction(std::int32_t dx,
                                                          std::int32_t dy) {
    if (std::abs(dx) >= std::abs(dy) && dx != 0) {
        return {dx > 0 ? 1 : -1, 0};
    }
    if (dy != 0) {
        return {0, dy > 0 ? 1 : -1};
    }
    return {1, 0};
}

}  // namespace

void BattleClockSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) {
        return;
    }
    simple_world->for_each<BattleClockComponent>(
        [&](v2::ecs::EntityHandle, BattleClockComponent& clock) {
            clock.frame_number = ctx.frame_number;
            clock.last_trigger = ctx.trigger;
        });
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            metadata.current_frame_number = ctx.frame_number;
        });
}

void BattleInputSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    (void)ctx;
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) return;

    // Apply pending input to game components during tick
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (!participant.online || participant.pending_input_data.empty()) return;

            const auto& input = participant.pending_input_data;

            // Parse "attack:<target>" → store target for CombatSystem
            if (input.starts_with("attack:")) {
                participant.pending_target_user_id = std::string(input.substr(7));
            }

            // Parse "move:<x>,<y>" → store intent for MovementSystem
            if (input.starts_with("move:")) {
                auto comma = input.find(',', 5);
                if (comma != std::string::npos) {
                    auto x_str = std::string(input.substr(5, comma - 5));
                    auto y_str = std::string(input.substr(comma + 1));
                    participant.pending_move_x = static_cast<std::int32_t>(
                        std::strtol(x_str.c_str(), nullptr, 10));
                    participant.pending_move_y = static_cast<std::int32_t>(
                        std::strtol(y_str.c_str(), nullptr, 10));
                    participant.has_pending_move = true;
                }
            }

            if (input.starts_with("fire:")) {
                auto comma = input.find(',', 5);
                if (comma != std::string::npos) {
                    auto dx_str = std::string(input.substr(5, comma - 5));
                    auto dy_str = std::string(input.substr(comma + 1));
                    auto [dx, dy] = normalize_direction(
                        static_cast<std::int32_t>(std::strtol(dx_str.c_str(), nullptr, 10)),
                        static_cast<std::int32_t>(std::strtol(dy_str.c_str(), nullptr, 10)));
                    participant.pending_fire_dx = dx;
                    participant.pending_fire_dy = dy;
                    participant.has_pending_fire = true;
                }
            }

            participant.pending_input_data.clear();
        });
}

BattleLifecycleSystem::BattleLifecycleSystem(
    std::uint32_t max_idle_frames,
    std::uint32_t max_offline_frames)
    : max_idle_frames_(max_idle_frames)
    , max_offline_frames_(max_offline_frames) {
}

void BattleLifecycleSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    (void)ctx;
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) {
        return;
    }

    BattleMetadataComponent* metadata = nullptr;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& m) {
            metadata = &m;
        });
    if (metadata == nullptr) {
        return;
    }

    // 1. Auto-transition from kCreated to kRunning on first tick
    if (metadata->lifecycle == BattleLifecycleState::kCreated) {
        metadata->lifecycle = BattleLifecycleState::kRunning;
        idle_frames_ = 0;
        offline_frames_ = 0;
        last_input_seq_ = metadata->next_input_seq;
        return;
    }

    // 2. If kFinished, clean up stale components
    if (metadata->lifecycle == BattleLifecycleState::kFinished) {
        std::vector<v2::ecs::EntityHandle> proj_handles;
        simple_world->for_each<ProjectileComponent>(
            [&](v2::ecs::EntityHandle handle, ProjectileComponent&) {
                proj_handles.push_back(handle);
            });
        for (const auto& h : proj_handles) {
            world.remove_component<ProjectileComponent>(h);
        }

        std::vector<v2::ecs::EntityHandle> dot_handles;
        simple_world->for_each<DamageOverlayComponent>(
            [&](v2::ecs::EntityHandle handle, DamageOverlayComponent&) {
                dot_handles.push_back(handle);
            });
        for (const auto& h : dot_handles) {
            world.remove_component<DamageOverlayComponent>(h);
        }
        return;
    }

    // Only process lifecycle transitions in kRunning state
    if (metadata->lifecycle != BattleLifecycleState::kRunning) {
        return;
    }

    // 3. Track idle frames via next_input_seq comparison
    const bool has_input = metadata->next_input_seq > last_input_seq_;
    last_input_seq_ = metadata->next_input_seq;

    if (has_input) {
        idle_frames_ = 0;
    } else {
        idle_frames_++;
    }

    // 4. Idle timeout → kFinished with kTimeout semantics
    if (idle_frames_ >= max_idle_frames_) {
        metadata->lifecycle = BattleLifecycleState::kFinished;
        return;
    }

    // 5. All-players-offline timeout → kFinished with kPlayerDisconnected semantics
    bool all_offline = true;
    bool any_participant = false;
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& p) {
            any_participant = true;
            if (p.online) {
                all_offline = false;
            }
        });

    if (!any_participant) {
        return;
    }

    if (all_offline) {
        offline_frames_++;
        if (offline_frames_ >= max_offline_frames_) {
            metadata->lifecycle = BattleLifecycleState::kFinished;
        }
    } else {
        offline_frames_ = 0;
    }
}

void BattleReplaySystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) {
        return;
    }

    BattleMetadataComponent* metadata = nullptr;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& m) {
            metadata = &m;
        });
    if (metadata == nullptr) return;

    BattleReplayLogComponent* replay_log = nullptr;
    simple_world->for_each<BattleReplayLogComponent>(
        [&](v2::ecs::EntityHandle, BattleReplayLogComponent& rl) {
            replay_log = &rl;
        });
    if (replay_log == nullptr) return;

    BattleReplayFrameRecord record;
    record.frame_number = ctx.frame_number;
    record.lifecycle = metadata->lifecycle;

    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& participant) {
            BattleReplayFrameRecord::ParticipantState ps;
            ps.user_id = participant.user_id;
            ps.score = participant.score;
            ps.online = participant.online;

            if (auto* pos = simple_world->get_component<PositionComponent>(handle)) {
                ps.x = pos->x;
                ps.y = pos->y;
            }
            if (auto* health = simple_world->get_component<HealthComponent>(handle)) {
                ps.hp = health->hp;
            }

            record.participants.push_back(std::move(ps));
        });

    replay_log->frame_snapshots.push_back(std::move(record));
}

std::unique_ptr<v2::ecs::World> create_battle_world(const std::string& battle_id,
                                                    const std::string& room_id,
                                                    const std::vector<std::string>& player_ids,
                                                    std::uint32_t max_frames) {
    auto world = std::make_unique<v2::ecs::SimpleWorld>();

    // Create executor and register all systems with stage dependencies for
    // parallel execution.  Systems in the same stage have no dependencies on
    // each other and can run concurrently.
    auto executor = std::make_unique<v2::ecs::ParallelSystemExecutor>();

    // Stage 0: no dependencies
    executor->add_system(std::make_unique<BattleClockSystem>(),
        v2::ecs::SystemMetadata{.name = "BattleClockSystem"});
    executor->add_system(std::make_unique<BattleInputSystem>(),
        v2::ecs::SystemMetadata{.name = "BattleInputSystem"});

    // Stage 1: depend on stage 0 systems
    executor->add_system(std::make_unique<MovementSystem>(),
        v2::ecs::SystemMetadata{.name = "MovementSystem", .dependencies = {"BattleInputSystem"}});
    executor->add_system(std::make_unique<BattleLifecycleSystem>(),
        v2::ecs::SystemMetadata{.name = "BattleLifecycleSystem", .dependencies = {"BattleClockSystem"}});

    // Stage 2: depend on stage 1 systems
    executor->add_system(std::make_unique<CombatSystem>(),
        v2::ecs::SystemMetadata{.name = "CombatSystem", .dependencies = {"MovementSystem", "BattleInputSystem"}});
    executor->add_system(std::make_unique<v2::aoi::AoiSystem>(),
        v2::ecs::SystemMetadata{
            .name = "AoiSystem",
            .dependencies = {"MovementSystem", "CombatSystem", "ProjectileSystem"},
        });
    executor->add_system(std::make_unique<ProjectileSystem>(),
        v2::ecs::SystemMetadata{.name = "ProjectileSystem", .dependencies = {"CombatSystem"}});

    // Stage 3: depends on stage 2 systems
    executor->add_system(std::make_unique<BattleReplaySystem>(),
        v2::ecs::SystemMetadata{.name = "BattleReplaySystem", .dependencies = {"BattleLifecycleSystem", "CombatSystem", "AoiSystem", "ProjectileSystem"}});

    world->set_executor(std::move(executor));

    const auto clock_entity = world->create_entity();
    world->add_component<BattleClockComponent>(clock_entity);
    auto& metadata = world->add_component<BattleMetadataComponent>(clock_entity);
    metadata.battle_id = battle_id;
    metadata.room_id = room_id;
    metadata.lifecycle = BattleLifecycleState::kRunning;
    metadata.max_frames = max_frames;
    metadata.current_frame_number = 0;

    const auto replay_entity = world->create_entity();
    world->add_component<BattleReplayLogComponent>(replay_entity);

    for (std::size_t index = 0; index < player_ids.size(); ++index) {
        const auto& user_id = player_ids[index];
        const auto entity = world->create_entity();
        auto& participant = world->add_component<BattleParticipantComponent>(entity);
        participant.user_id = user_id;
        auto& pos = world->add_component<PositionComponent>(entity);
        const auto [spawn_x, spawn_y] = spawn_position_for(battle_id, index);
        pos.x = spawn_x;
        pos.y = spawn_y;
        world->add_component<HealthComponent>(entity);
        world->add_component<AttackStateComponent>(entity);
        world->add_component<AttackCooldownComponent>(entity);
    }

    return world;
}

void battle_world_set_lifecycle(v2::ecs::World& world,
                                BattleLifecycleState lifecycle) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return;
    }

    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            metadata.lifecycle = lifecycle;
        });
}

std::string battle_world_battle_id(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return {};
    }

    std::string battle_id;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            battle_id = metadata.battle_id;
        });
    return battle_id;
}

std::string battle_world_room_id(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return {};
    }

    std::string room_id;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            room_id = metadata.room_id;
        });
    return room_id;
}

BattleLifecycleState battle_world_lifecycle(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return BattleLifecycleState::kCreated;
    }

    auto lifecycle = BattleLifecycleState::kCreated;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            lifecycle = metadata.lifecycle;
        });
    return lifecycle;
}

std::uint32_t battle_world_frame_number(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return 0;
    }

    std::uint32_t frame_number = 0;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            frame_number = metadata.current_frame_number;
        });
    return frame_number;
}

std::vector<BattleParticipantState> battle_world_participants(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return {};
    }

    std::vector<std::pair<v2::ecs::EntityId, BattleParticipantState>> participants_with_ids;
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& participant) {
            participants_with_ids.push_back({handle.id, BattleParticipantState{
                .user_id = participant.user_id,
                .online = participant.online,
            }});
        });
    std::sort(participants_with_ids.begin(),
              participants_with_ids.end(),
              [](const auto& lhs, const auto& rhs) {
                  return lhs.first < rhs.first;
              });

    std::vector<BattleParticipantState> participants;
    participants.reserve(participants_with_ids.size());
    for (auto& [entity_id, participant] : participants_with_ids) {
        (void)entity_id;
        participants.push_back(std::move(participant));
    }
    return participants;
}

void battle_world_apply_input_score(v2::ecs::World& world,
                                    const std::string& user_id,
                                    std::int64_t score) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return;
    }
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (participant.user_id == user_id) {
                participant.score += score;
            }
        });
}

bool battle_world_mark_offline(v2::ecs::World& world,
                               const std::string& user_id) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return false;
    }
    bool changed = false;
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (participant.user_id == user_id && participant.online) {
                participant.online = false;
                changed = true;
            }
        });
    return changed;
}

void battle_world_set_online(v2::ecs::World& world,
                             const std::string& user_id) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return;
    }
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (participant.user_id == user_id) {
                participant.online = true;
            }
        });
}

bool battle_world_should_accept_input(v2::ecs::World& world,
                                      const std::string& user_id,
                                      std::uint32_t submitted_frame) {
    if (submitted_frame == 0) {
        return true;
    }

    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return true;
    }

    bool accepted = true;
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (participant.user_id == user_id && participant.last_submitted_frame >= submitted_frame) {
                accepted = false;
            }
        });
    return accepted;
}

void battle_world_record_submitted_frame(v2::ecs::World& world,
                                         const std::string& user_id,
                                         std::uint32_t submitted_frame) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return;
    }

    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (participant.user_id == user_id) {
                participant.last_submitted_frame = submitted_frame;
            }
        });
}

void battle_world_record_frame_ack(v2::ecs::World& world,
                                   const std::string& user_id,
                                   std::uint32_t frame_number) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return;
    }

    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            if (participant.user_id == user_id) {
                participant.last_acked_frame = frame_number;
            }
        });
}

std::uint32_t battle_world_tick(v2::ecs::World& world,
                                const v2::ecs::FrameContext& ctx) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return 0;
    }
    world.tick(ctx);
    std::uint32_t frame_number = 0;
    simple_world->for_each<BattleClockComponent>(
        [&](v2::ecs::EntityHandle, BattleClockComponent& clock) {
            frame_number = clock.frame_number;
        });
    return frame_number;
}

std::uint64_t battle_world_append_replay_input(v2::ecs::World& world,
                                               std::uint32_t frame_number,
                                               const std::string& user_id,
                                               const std::string& input_data,
                                               std::int64_t score) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return 0;
    }

    std::uint64_t input_seq = 0;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            input_seq = metadata.next_input_seq++;
        });
    simple_world->for_each<BattleReplayLogComponent>(
        [&](v2::ecs::EntityHandle, BattleReplayLogComponent& replay_log) {
            replay_log.replay_inputs.push_back(BattleReplayInputRecord{
                .input_seq = input_seq,
                .frame_number = frame_number,
                .user_id = user_id,
                .input_data = input_data,
                .score = score,
                .trigger = {},
            });
        });
    return input_seq;
}

void battle_world_apply_trigger_to_frame(v2::ecs::World& world,
                                         std::uint32_t frame_number,
                                         const std::string& trigger) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return;
    }

    simple_world->for_each<BattleReplayLogComponent>(
        [&](v2::ecs::EntityHandle, BattleReplayLogComponent& replay_log) {
            for (auto& replay_input : replay_log.replay_inputs) {
                if (replay_input.frame_number == frame_number) {
                    replay_input.trigger = trigger;
                }
            }
        });
}

std::vector<BattleReplayInputRecord> battle_world_collect_replay_inputs(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return {};
    }

    std::vector<BattleReplayInputRecord> replay_inputs;
    simple_world->for_each<BattleReplayLogComponent>(
        [&](v2::ecs::EntityHandle, BattleReplayLogComponent& replay_log) {
            replay_inputs = replay_log.replay_inputs;
        });
    return replay_inputs;
}

std::vector<BattleReplayFrameRecord> battle_world_collect_frame_snapshots(v2::ecs::World& world) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return {};
    }

    std::vector<BattleReplayFrameRecord> snapshots;
    simple_world->for_each<BattleReplayLogComponent>(
        [&](v2::ecs::EntityHandle, BattleReplayLogComponent& replay_log) {
            snapshots = replay_log.frame_snapshots;
        });
    return snapshots;
}

std::vector<BattleScore> battle_world_collect_scores(
    v2::ecs::World& world,
    const std::vector<BattleParticipantState>& participants) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return {};
    }

    std::unordered_map<std::string, std::int64_t> scores_by_user_id;
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
            scores_by_user_id[participant.user_id] = participant.score;
        });

    std::vector<BattleScore> scores;
    scores.reserve(participants.size());
    for (const auto& participant : participants) {
        scores.push_back(BattleScore{
            .user_id = participant.user_id,
            .score = scores_by_user_id[participant.user_id],
        });
    }
    return scores;
}

BattleResultSummary battle_world_build_result_summary(
    v2::ecs::World& world,
    const std::string& battle_id,
    const std::string& room_id,
    const std::vector<BattleParticipantState>& participants,
    BattleFinishReason reason,
    std::uint32_t total_frames) {
    auto scores = battle_world_collect_scores(world, participants);

    std::optional<std::string> winner_user_id;
    std::int64_t high_score = 0;
    bool any_score_set = false;
    for (const auto& score : scores) {
        if (!any_score_set || score.score > high_score) {
            any_score_set = true;
            high_score = score.score;
            winner_user_id = score.user_id;
        }
    }

    return BattleResultSummary{
        .battle_id = battle_id,
        .room_id = room_id,
        .reason = reason,
        .winner_user_id = winner_user_id,
        .scores = std::move(scores),
        .total_frames = total_frames,
    };
}

bool battle_world_should_finish_for_frame_limit(v2::ecs::World& world,
                                                std::uint32_t frame_number) {
    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return false;
    }

    bool should_finish = false;
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            should_finish = metadata.max_frames > 0 && frame_number >= metadata.max_frames;
        });
    return should_finish;
}

BattleWorldSnapshot battle_world_snapshot(v2::ecs::World& world) {
    BattleWorldSnapshot snapshot;

    auto* simple_world = as_simple_world(world);
    if (simple_world == nullptr) {
        return snapshot;
    }

    simple_world->for_each<BattleClockComponent>(
        [&](v2::ecs::EntityHandle, BattleClockComponent& clock) {
            snapshot.clock.frame_number = clock.frame_number;
            snapshot.clock.last_trigger = clock.last_trigger;
        });

    std::vector<std::pair<v2::ecs::EntityId, BattleWorldParticipantState>> participants_with_ids;
    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& participant) {
            BattleWorldParticipantState state{
                .user_id = participant.user_id,
                .online = participant.online,
                .score = participant.score,
                .last_submitted_frame = participant.last_submitted_frame,
                .last_acked_frame = participant.last_acked_frame,
            };
            // Enrich with position and health from the same entity
            if (auto* pos = simple_world->get_component<PositionComponent>(handle)) {
                state.pos_x = pos->x;
                state.pos_y = pos->y;
            }
            state.facing_dx = participant.facing_dx;
            state.facing_dy = participant.facing_dy;
            if (auto* health = simple_world->get_component<HealthComponent>(handle)) {
                state.hp = health->hp;
                state.max_hp = health->max_hp;
            }
            if (auto* attack = simple_world->get_component<AttackStateComponent>(handle)) {
                state.damage = attack->damage;
            }
            participants_with_ids.push_back({handle.id, std::move(state)});
        });
    std::sort(participants_with_ids.begin(),
              participants_with_ids.end(),
              [](const auto& lhs, const auto& rhs) {
                  return lhs.first < rhs.first;
              });
    snapshot.participants.reserve(participants_with_ids.size());
    for (auto& [entity_id, participant] : participants_with_ids) {
        (void)entity_id;
        snapshot.participants.push_back(std::move(participant));
    }

    std::vector<std::pair<v2::ecs::EntityId, BattleWorldProjectileState>> projectiles_with_ids;
    simple_world->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle handle, ProjectileComponent& projectile) {
            if (!projectile.active) {
                return;
            }
            BattleWorldProjectileState state{
                .projectile_id = projectile.projectile_id,
                .owner_user_id = projectile.owner_user_id,
                .dir_x = projectile.dir_x,
                .dir_y = projectile.dir_y,
                .active = projectile.active,
            };
            if (auto* pos = simple_world->get_component<PositionComponent>(handle)) {
                state.pos_x = pos->x;
                state.pos_y = pos->y;
            }
            projectiles_with_ids.push_back({handle.id, std::move(state)});
        });
    std::sort(projectiles_with_ids.begin(),
              projectiles_with_ids.end(),
              [](const auto& lhs, const auto& rhs) {
                  return lhs.first < rhs.first;
              });
    snapshot.projectiles.reserve(projectiles_with_ids.size());
    for (auto& [entity_id, projectile] : projectiles_with_ids) {
        (void)entity_id;
        snapshot.projectiles.push_back(std::move(projectile));
    }

    return snapshot;
}

BattleRuntimeState battle_world_runtime_state(v2::ecs::World& world) {
    return BattleRuntimeState{
        .battle_id = battle_world_battle_id(world),
        .room_id = battle_world_room_id(world),
        .lifecycle = battle_world_lifecycle(world),
        .participants = battle_world_participants(world),
        .frame_number = battle_world_frame_number(world),
        .replay_inputs = battle_world_collect_replay_inputs(world),
    };
}

// ─── Authoritative Entry Points ───────────────────────────────

BattleWorldInputResult battle_world_process_input(
    v2::ecs::World& world,
    const std::string& user_id,
    const std::string& input_data,
    std::int64_t score,
    std::uint32_t submitted_frame) {
    BattleWorldInputResult result;

    if (battle_world_lifecycle(world) != BattleLifecycleState::kRunning) {
        result.reject_reason = "battle_not_running";
        return result;
    }

    if (!battle_world_should_accept_input(world, user_id, submitted_frame)) {
        result.reject_reason = "duplicate_frame";
        return result;
    }

    result.accepted = true;

    if (submitted_frame > 0) {
        battle_world_record_submitted_frame(world, user_id, submitted_frame);
    }

    battle_world_apply_input_score(world, user_id, score);

    // Store pending input on the participant for system processing during tick
    auto* simple_world = as_simple_world(world);
    if (simple_world != nullptr) {
        simple_world->for_each<BattleParticipantComponent>(
            [&](v2::ecs::EntityHandle, BattleParticipantComponent& participant) {
                if (participant.user_id == user_id) {
                    participant.pending_input_data = input_data;
                }
            });
    }

    const auto next_frame_number = battle_world_frame_number(world) + 1;
    result.input_seq = battle_world_append_replay_input(
        world, next_frame_number, user_id, input_data, score);

    return result;
}

BattleWorldFrameResult battle_world_advance_frame(
    v2::ecs::World& world,
    std::uint32_t next_frame,
    const std::string& trigger) {
    BattleWorldFrameResult result;

    const auto frame_number = battle_world_tick(world, v2::ecs::FrameContext{
        .battle_id = battle_world_battle_id(world),
        .room_id = battle_world_room_id(world),
        .frame_number = next_frame,
        .trigger = trigger,
    });

    result.frame_number = frame_number;
    result.trigger = trigger;

    battle_world_apply_trigger_to_frame(world, frame_number, trigger);

    if (battle_world_should_finish_for_frame_limit(world, frame_number)) {
        result.should_finish = true;
        result.finish_reason = BattleFinishReason::kFrameLimitReached;
    }

    return result;
}

BattleWorldDisconnectResult battle_world_handle_disconnect(
    v2::ecs::World& world,
    const std::string& user_id) {
    BattleWorldDisconnectResult result;

    if (battle_world_lifecycle(world) != BattleLifecycleState::kRunning) {
        return result;
    }

    result.participant_existed = battle_world_mark_offline(world, user_id);
    result.battle_should_finish = result.participant_existed;

    return result;
}

}  // namespace v2::battle
