#pragma once

#include "v2/battle/message_types.h"
#include "v2/ecs/world.h"

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace v2::battle {

struct BattleWorldClockState {
    std::uint32_t frame_number = 0;
    std::string last_trigger;
};

struct BattleWorldParticipantState {
    std::string user_id;
    bool online = true;
    std::int64_t score = 0;
    std::uint32_t last_submitted_frame = 0;
    std::uint32_t last_acked_frame = 0;
};

struct BattleWorldSnapshot {
    BattleWorldClockState clock;
    std::vector<BattleWorldParticipantState> participants;
};

[[nodiscard]] std::unique_ptr<v2::ecs::World> create_battle_world(
    const std::string& battle_id,
    const std::string& room_id,
    const std::vector<std::string>& player_ids,
    std::uint32_t max_frames = 0);

void battle_world_set_lifecycle(v2::ecs::World& world,
                                BattleLifecycleState lifecycle);

[[nodiscard]] std::string battle_world_battle_id(v2::ecs::World& world);
[[nodiscard]] std::string battle_world_room_id(v2::ecs::World& world);
[[nodiscard]] BattleLifecycleState battle_world_lifecycle(v2::ecs::World& world);
[[nodiscard]] std::uint32_t battle_world_frame_number(v2::ecs::World& world);

[[nodiscard]] std::vector<BattleParticipantState> battle_world_participants(
    v2::ecs::World& world);

void battle_world_apply_input_score(v2::ecs::World& world,
                                    const std::string& user_id,
                                    std::int64_t score);

[[nodiscard]] bool battle_world_mark_offline(v2::ecs::World& world,
                                             const std::string& user_id);

[[nodiscard]] bool battle_world_should_accept_input(v2::ecs::World& world,
                                                    const std::string& user_id,
                                                    std::uint32_t submitted_frame);

void battle_world_record_submitted_frame(v2::ecs::World& world,
                                         const std::string& user_id,
                                         std::uint32_t submitted_frame);

void battle_world_record_frame_ack(v2::ecs::World& world,
                                   const std::string& user_id,
                                   std::uint32_t frame_number);

[[nodiscard]] std::uint32_t battle_world_tick(v2::ecs::World& world,
                                              const v2::ecs::FrameContext& ctx);

[[nodiscard]] std::uint64_t battle_world_append_replay_input(v2::ecs::World& world,
                                                             std::uint32_t frame_number,
                                                             const std::string& user_id,
                                                             const std::string& input_data,
                                                             std::int64_t score);

void battle_world_apply_trigger_to_frame(v2::ecs::World& world,
                                         std::uint32_t frame_number,
                                         const std::string& trigger);

[[nodiscard]] std::vector<BattleReplayInputRecord> battle_world_collect_replay_inputs(
    v2::ecs::World& world);

[[nodiscard]] std::vector<BattleScore> battle_world_collect_scores(
    v2::ecs::World& world,
    const std::vector<BattleParticipantState>& participants);

[[nodiscard]] BattleResultSummary battle_world_build_result_summary(
    v2::ecs::World& world,
    const std::string& battle_id,
    const std::string& room_id,
    const std::vector<BattleParticipantState>& participants,
    BattleFinishReason reason,
    std::uint32_t total_frames);

[[nodiscard]] bool battle_world_should_finish_for_frame_limit(v2::ecs::World& world,
                                                              std::uint32_t frame_number);

[[nodiscard]] BattleWorldSnapshot battle_world_snapshot(v2::ecs::World& world);
[[nodiscard]] BattleRuntimeState battle_world_runtime_state(v2::ecs::World& world);

}  // namespace v2::battle
