#include "v2/battle/runtime_world.h"

#include "v2/battle/runtime_components.h"

#include <algorithm>
#include <memory>
#include <unordered_map>
#include <utility>

namespace v2::battle {

namespace {

v2::ecs::SimpleWorld* as_simple_world(v2::ecs::World& world) {
    return dynamic_cast<v2::ecs::SimpleWorld*>(&world);
}

}  // namespace

void AdvanceFrameSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) {
        return;
    }
    simple_world->for_each<BattleClockComponent>(
        [&](v2::ecs::EntityHandle, BattleClockComponent& clock) {
            clock.frame_number = ctx.frame_number;
            clock.last_trigger = ctx.trigger;
        });
}

std::unique_ptr<v2::ecs::World> create_battle_world(const std::string& battle_id,
                                                    const std::string& room_id,
                                                    const std::vector<std::string>& player_ids,
                                                    std::uint32_t max_frames) {
    auto world = std::make_unique<v2::ecs::SimpleWorld>();
    world->add_system(std::make_unique<AdvanceFrameSystem>());

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

    for (const auto& user_id : player_ids) {
        const auto entity = world->create_entity();
        auto& participant = world->add_component<BattleParticipantComponent>(entity);
        participant.user_id = user_id;
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
    simple_world->for_each<BattleMetadataComponent>(
        [&](v2::ecs::EntityHandle, BattleMetadataComponent& metadata) {
            metadata.current_frame_number = ctx.frame_number;
        });
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
            participants_with_ids.push_back({handle.id, BattleWorldParticipantState{
                .user_id = participant.user_id,
                .online = participant.online,
                .score = participant.score,
                .last_submitted_frame = participant.last_submitted_frame,
                .last_acked_frame = participant.last_acked_frame,
            }});
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

}  // namespace v2::battle
