#include <gtest/gtest.h>

#include "v2/battle/runtime_world.h"

#include <unordered_map>
#include <vector>

TEST(V2BattleRuntimeWorldTest, TracksFrameTriggerAndParticipantState) {
    auto world = v2::battle::create_battle_world("battle_01", "room_01", {"alice", "bob"}, 4);
    EXPECT_EQ(v2::battle::battle_world_lifecycle(*world), v2::battle::BattleLifecycleState::kRunning);
    EXPECT_EQ(v2::battle::battle_world_battle_id(*world), "battle_01");
    EXPECT_EQ(v2::battle::battle_world_room_id(*world), "room_01");
    EXPECT_EQ(v2::battle::battle_world_frame_number(*world), 0U);

    v2::battle::battle_world_apply_input_score(*world, "alice", 7);
    v2::battle::battle_world_apply_input_score(*world, "bob", 3);
    EXPECT_TRUE(v2::battle::battle_world_mark_offline(*world, "bob"));
    EXPECT_FALSE(v2::battle::battle_world_mark_offline(*world, "bob"));
    EXPECT_TRUE(v2::battle::battle_world_should_accept_input(*world, "alice", 2));
    v2::battle::battle_world_record_submitted_frame(*world, "alice", 2);
    v2::battle::battle_world_record_frame_ack(*world, "alice", 4);
    const auto input_seq =
        v2::battle::battle_world_append_replay_input(*world, 4, "alice", "move:1,2", 7);
    EXPECT_EQ(input_seq, 1U);
    EXPECT_FALSE(v2::battle::battle_world_should_accept_input(*world, "alice", 2));

    EXPECT_EQ(v2::battle::battle_world_tick(*world,
                                            v2::ecs::FrameContext{
                                                .battle_id = "battle_01",
                                                .room_id = "room_01",
                                                .frame_number = 4,
                                                .trigger = "scheduler",
                                            }),
              4U);
    EXPECT_EQ(v2::battle::battle_world_frame_number(*world), 4U);
    v2::battle::battle_world_apply_trigger_to_frame(*world, 4, "scheduler");

    const auto snapshot = v2::battle::battle_world_snapshot(*world);
    EXPECT_EQ(snapshot.clock.frame_number, 4U);
    EXPECT_EQ(snapshot.clock.last_trigger, "scheduler");
    EXPECT_TRUE(v2::battle::battle_world_should_finish_for_frame_limit(*world, 4));

    const auto replay_inputs = v2::battle::battle_world_collect_replay_inputs(*world);
    ASSERT_EQ(replay_inputs.size(), 1U);
    EXPECT_EQ(replay_inputs[0].trigger, "scheduler");

    const auto result = v2::battle::battle_world_build_result_summary(
        *world,
        "battle_01",
        "room_01",
        std::vector<v2::battle::BattleParticipantState>{
            {.user_id = "alice", .online = true},
            {.user_id = "bob", .online = false},
        },
        v2::battle::BattleFinishReason::kFrameLimitReached,
        4);
    ASSERT_TRUE(result.winner_user_id.has_value());
    EXPECT_EQ(*result.winner_user_id, "alice");

    const auto participants = v2::battle::battle_world_participants(*world);
    ASSERT_EQ(participants.size(), 2U);
    EXPECT_EQ(participants[0].user_id, "alice");
    EXPECT_TRUE(participants[0].online);
    EXPECT_EQ(participants[1].user_id, "bob");
    EXPECT_FALSE(participants[1].online);

    v2::battle::battle_world_set_lifecycle(*world, v2::battle::BattleLifecycleState::kFinished);
    EXPECT_EQ(v2::battle::battle_world_lifecycle(*world), v2::battle::BattleLifecycleState::kFinished);

    std::unordered_map<std::string, v2::battle::BattleWorldParticipantState> by_user_id;
    for (const auto& participant : snapshot.participants) {
        by_user_id.emplace(participant.user_id, participant);
    }

    ASSERT_EQ(by_user_id.size(), 2U);
    EXPECT_EQ(by_user_id.at("alice").score, 7);
    EXPECT_TRUE(by_user_id.at("alice").online);
    EXPECT_EQ(by_user_id.at("alice").last_submitted_frame, 2U);
    EXPECT_EQ(by_user_id.at("alice").last_acked_frame, 4U);
    EXPECT_EQ(by_user_id.at("bob").score, 3);
    EXPECT_FALSE(by_user_id.at("bob").online);
}
