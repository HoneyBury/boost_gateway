#include <gtest/gtest.h>

#include "v2/battle/runtime_components.h"
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

    const auto runtime_state = v2::battle::battle_world_runtime_state(*world);
    EXPECT_EQ(runtime_state.battle_id, "battle_01");
    EXPECT_EQ(runtime_state.room_id, "room_01");
    EXPECT_EQ(runtime_state.lifecycle, v2::battle::BattleLifecycleState::kRunning);
    EXPECT_EQ(runtime_state.frame_number, 4U);
    ASSERT_EQ(runtime_state.participants.size(), 2U);
    ASSERT_EQ(runtime_state.replay_inputs.size(), 1U);

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

// ─── Authoritative Entry Points ───────────────────────────────

TEST(V2BattleRuntimeWorldTest, ProcessInputAcceptsFirstFrame) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    const auto result = v2::battle::battle_world_process_input(
        *world, "alice", "move:1,2", 5, 1);

    EXPECT_TRUE(result.accepted);
    EXPECT_GE(result.input_seq, 1U);
    EXPECT_TRUE(result.reject_reason.empty());
}

TEST(V2BattleRuntimeWorldTest, ProcessInputRejectsDuplicateFrame) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    auto first = v2::battle::battle_world_process_input(*world, "alice", "first", 3, 2);
    EXPECT_TRUE(first.accepted);

    auto second = v2::battle::battle_world_process_input(*world, "alice", "second", 3, 2);
    EXPECT_FALSE(second.accepted);
    EXPECT_EQ(second.reject_reason, "duplicate_frame");
}

TEST(V2BattleRuntimeWorldTest, ProcessInputRejectsWhenFinished) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);
    v2::battle::battle_world_set_lifecycle(*world, v2::battle::BattleLifecycleState::kFinished);

    const auto result = v2::battle::battle_world_process_input(
        *world, "alice", "move", 1, 1);

    EXPECT_FALSE(result.accepted);
    EXPECT_EQ(result.reject_reason, "battle_not_running");
}

TEST(V2BattleRuntimeWorldTest, AdvanceFrameReturnsFinishWhenLimitReached) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 3);

    // Advance to frame 3 (should trigger finish).
    const auto result = v2::battle::battle_world_advance_frame(*world, 3, "tick");

    EXPECT_EQ(result.frame_number, 3U);
    EXPECT_EQ(result.trigger, "tick");
    EXPECT_TRUE(result.should_finish);
    EXPECT_EQ(result.finish_reason, v2::battle::BattleFinishReason::kFrameLimitReached);
}

TEST(V2BattleRuntimeWorldTest, AdvanceFrameDoesNotFinishBeforeLimit) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    const auto result = v2::battle::battle_world_advance_frame(*world, 1, "tick");

    EXPECT_EQ(result.frame_number, 1U);
    EXPECT_FALSE(result.should_finish);
}

TEST(V2BattleRuntimeWorldTest, HandleDisconnectReturnsBattleShouldFinish) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice", "bob"}, 10);

    const auto result = v2::battle::battle_world_handle_disconnect(*world, "alice");

    EXPECT_TRUE(result.participant_existed);
    EXPECT_TRUE(result.battle_should_finish);
}

TEST(V2BattleRuntimeWorldTest, HandleDisconnectForUnknownUserReturnsNoFinish) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    const auto result = v2::battle::battle_world_handle_disconnect(*world, "nonexistent");

    EXPECT_FALSE(result.participant_existed);
    EXPECT_FALSE(result.battle_should_finish);
}

TEST(V2BattleRuntimeWorldTest, HandleDisconnectWhenFinishedReturnsNoFinish) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);
    v2::battle::battle_world_set_lifecycle(*world, v2::battle::BattleLifecycleState::kFinished);

    const auto result = v2::battle::battle_world_handle_disconnect(*world, "alice");

    EXPECT_FALSE(result.participant_existed);
    EXPECT_FALSE(result.battle_should_finish);
}

TEST(V2BattleRuntimeWorldTest, SystemsAreCreatedAndTickable) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    // Advance a few frames — all systems should run without error.
    for (std::uint32_t f = 1; f <= 5; ++f) {
        const auto result = v2::battle::battle_world_advance_frame(
            *world, f, "clock_tick");
        EXPECT_EQ(result.frame_number, f);
    }

    EXPECT_EQ(v2::battle::battle_world_frame_number(*world), 5U);
}

TEST(V2BattleRuntimeWorldTest, ProcessInputAccumulatesScore) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice", "bob"}, 100);

    (void)v2::battle::battle_world_process_input(*world, "alice", "a1", 10, 1);
    (void)v2::battle::battle_world_process_input(*world, "bob", "b1", 7, 1);
    (void)v2::battle::battle_world_process_input(*world, "alice", "a2", 5, 2);

    const auto snapshot = v2::battle::battle_world_snapshot(*world);
    ASSERT_EQ(snapshot.participants.size(), 2U);

    std::unordered_map<std::string, v2::battle::BattleWorldParticipantState> by_user;
    for (const auto& p : snapshot.participants) {
        by_user[p.user_id] = p;
    }
    EXPECT_EQ(by_user["alice"].score, 15);
    EXPECT_EQ(by_user["bob"].score, 7);
}
