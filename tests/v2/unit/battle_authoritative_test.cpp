#include <gtest/gtest.h>

#include "v2/battle/runtime_world.h"

TEST(V2BattleAuthoritativeTest, ProcessInputRejectsWhenBattleNotRunning) {
    auto world = v2::battle::create_battle_world("auth_01", "r1", {"alice", "bob"}, 3);
    v2::battle::battle_world_set_lifecycle(*world, v2::battle::BattleLifecycleState::kFinished);

    auto result = v2::battle::battle_world_process_input(
        *world, "alice", "move:1,2", 0, 1);
    EXPECT_FALSE(result.accepted);
    EXPECT_EQ(result.reject_reason, "battle_not_running");
}

TEST(V2BattleAuthoritativeTest, ProcessInputRejectsDuplicateFrame) {
    auto world = v2::battle::create_battle_world("auth_02", "r2", {"alice"}, 3);

    auto r1 = v2::battle::battle_world_process_input(
        *world, "alice", "move:1,2", 0, 1);
    EXPECT_TRUE(r1.accepted);

    auto r2 = v2::battle::battle_world_process_input(
        *world, "alice", "move:3,4", 0, 1);  // same frame
    EXPECT_FALSE(r2.accepted);
    EXPECT_EQ(r2.reject_reason, "duplicate_frame");
}

TEST(V2BattleAuthoritativeTest, ProcessInputAcceptsNewerFrame) {
    auto world = v2::battle::create_battle_world("auth_03", "r3", {"alice"}, 5);

    auto r1 = v2::battle::battle_world_process_input(
        *world, "alice", "move:1,2", 10, 1);
    EXPECT_TRUE(r1.accepted);

    auto r2 = v2::battle::battle_world_process_input(
        *world, "alice", "move:3,4", 20, 2);
    EXPECT_TRUE(r2.accepted);
}

TEST(V2BattleAuthoritativeTest, FrameLimitTriggersBattleFinish) {
    auto world = v2::battle::create_battle_world("auth_04", "r4", {"alice", "bob"}, 3);

    (void)v2::battle::battle_world_process_input(*world, "alice", "move:1,2", 10, 1);
    (void)v2::battle::battle_world_advance_frame(*world, 1, "input:alice");

    (void)v2::battle::battle_world_process_input(*world, "alice", "move:3,4", 5, 2);
    auto result = v2::battle::battle_world_advance_frame(*world, 2, "input:alice");
    EXPECT_FALSE(result.should_finish);

    (void)v2::battle::battle_world_process_input(*world, "alice", "move:5,6", 0, 3);
    auto final_result = v2::battle::battle_world_advance_frame(*world, 3, "input:alice");
    EXPECT_TRUE(final_result.should_finish);
    EXPECT_EQ(final_result.finish_reason, v2::battle::BattleFinishReason::kFrameLimitReached);
    EXPECT_EQ(final_result.frame_number, 3U);
}

TEST(V2BattleAuthoritativeTest, BuildResultSummaryFindsWinner) {
    auto world = v2::battle::create_battle_world("auth_05", "r5", {"alice", "bob"}, 3);

    v2::battle::battle_world_apply_input_score(*world, "alice", 100);
    v2::battle::battle_world_apply_input_score(*world, "bob", 42);

    auto participants = v2::battle::battle_world_participants(*world);
    auto summary = v2::battle::battle_world_build_result_summary(
        *world, "auth_05", "r5", participants,
        v2::battle::BattleFinishReason::kFrameLimitReached, 3);

    EXPECT_EQ(summary.battle_id, "auth_05");
    EXPECT_EQ(summary.total_frames, 3U);
    EXPECT_TRUE(summary.winner_user_id.has_value());
    EXPECT_EQ(*summary.winner_user_id, "alice");
    ASSERT_EQ(summary.scores.size(), 2U);
    EXPECT_EQ(summary.scores[0].user_id, "alice");
    EXPECT_EQ(summary.scores[0].score, 100);
    EXPECT_EQ(summary.scores[1].user_id, "bob");
    EXPECT_EQ(summary.scores[1].score, 42);
}

TEST(V2BattleAuthoritativeTest, MarkOfflineReturnsCorrectStatus) {
    auto world = v2::battle::create_battle_world("auth_06", "r6", {"alice", "bob"}, 5);

    EXPECT_TRUE(v2::battle::battle_world_mark_offline(*world, "alice"));
    EXPECT_FALSE(v2::battle::battle_world_mark_offline(*world, "alice"));  // already offline
    EXPECT_FALSE(v2::battle::battle_world_mark_offline(*world, "nobody"));
}

TEST(V2BattleAuthoritativeTest, HandleDisconnectMarksOfflineAndSuggestsFinish) {
    auto world = v2::battle::create_battle_world("auth_07", "r7", {"alice", "bob"}, 5);

    auto result = v2::battle::battle_world_handle_disconnect(*world, "alice");
    EXPECT_TRUE(result.participant_existed);
    EXPECT_TRUE(result.battle_should_finish);

    // Second disconnect should not suggest finish (already offline)
    auto result2 = v2::battle::battle_world_handle_disconnect(*world, "alice");
    EXPECT_FALSE(result2.participant_existed);
    EXPECT_FALSE(result2.battle_should_finish);
}

TEST(V2BattleAuthoritativeTest, InputFromOfflinePlayerNotProcessed) {
    auto world = v2::battle::create_battle_world("auth_08", "r8", {"alice"}, 5);

    // Reset position to origin so the position assertion is deterministic
    auto* sw = dynamic_cast<v2::ecs::SimpleWorld*>(world.get());
    ASSERT_NE(sw, nullptr);
    sw->for_each<v2::battle::PositionComponent>(
        [](v2::ecs::EntityHandle, v2::battle::PositionComponent& pos) {
            pos.x = 0;
            pos.y = 0;
        });

    (void)v2::battle::battle_world_mark_offline(*world, "alice");

    auto result = v2::battle::battle_world_process_input(
        *world, "alice", "move:1,2", 0, 1);
    EXPECT_TRUE(result.accepted);  // frame validation passes

    (void)v2::battle::battle_world_advance_frame(*world, 1, "tick");

    // Offline player's pending input is not applied during tick
    auto snapshot = v2::battle::battle_world_snapshot(*world);
    ASSERT_GE(snapshot.participants.size(), 1U);
    EXPECT_EQ(snapshot.participants[0].score, 0);
    EXPECT_EQ(snapshot.participants[0].pos_x, 0);
    EXPECT_EQ(snapshot.participants[0].pos_y, 0);
}

TEST(V2BattleAuthoritativeTest, DisconnectWhenNotRunningDoesNothing) {
    auto world = v2::battle::create_battle_world("auth_09", "r9", {"alice"}, 5);
    v2::battle::battle_world_set_lifecycle(*world, v2::battle::BattleLifecycleState::kFinished);

    auto result = v2::battle::battle_world_handle_disconnect(*world, "alice");
    EXPECT_FALSE(result.participant_existed);
    EXPECT_FALSE(result.battle_should_finish);
}

TEST(V2BattleAuthoritativeTest, SnapshotIncludesAllParticipantsWithHealthAndPosition) {
    auto world = v2::battle::create_battle_world("auth_10", "r10", {"alice", "bob", "charlie"}, 5);

    // Reset positions to origin for deterministic assertions
    auto* sw = dynamic_cast<v2::ecs::SimpleWorld*>(world.get());
    ASSERT_NE(sw, nullptr);
    sw->for_each<v2::battle::PositionComponent>(
        [](v2::ecs::EntityHandle, v2::battle::PositionComponent& pos) {
            pos.x = 0;
            pos.y = 0;
        });

    auto snapshot = v2::battle::battle_world_snapshot(*world);
    ASSERT_EQ(snapshot.participants.size(), 3U);

    for (const auto& p : snapshot.participants) {
        EXPECT_EQ(p.hp, 100);
        EXPECT_EQ(p.max_hp, 100);
        EXPECT_EQ(p.pos_x, 0);
        EXPECT_EQ(p.pos_y, 0);
        EXPECT_EQ(p.damage, 10);
        EXPECT_TRUE(p.online);
    }
}
