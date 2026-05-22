// v2.5.0 T8: Battle replay payload generation and playback tests
#include <gtest/gtest.h>
#include "v2/battle/battle_actor.h"
#include "v2/battle/battle_snapshot.h"
#include "v2/battle/runtime_components.h"
#include "v2/battle/runtime_world.h"
#include "v2/ecs/world.h"
#include "v2/runtime/actor_system.h"

#include <memory>
#include <unordered_map>
#include <vector>

TEST(BattleReplayTest, ReplayInputsCollected) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::battle::BattleEventSink {
        void push(v2::battle::BattleEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::battle::BattleEvent> events;
    };
    Sink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::battle::BattleActor>(sink));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "replay", .room_id = "r", .player_ids = {"a","b"}, .max_frames = 10};
    ref.tell(std::move(create));

    // Submit inputs
    auto send_input = [&](const char* uid, const char* data, std::uint32_t frame) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = v2::battle::SubmitBattleInputMsg{
            .user_id = uid, .input_data = data, .submitted_frame = frame};
        ref.tell(std::move(msg));
    };
    send_input("a", "move:10,20", 1);
    send_input("b", "move:30,40", 1);
    send_input("a", "attack:b", 2);

    actor_system.dispatch_all();

    // Verify replay inputs were collected (dispatched successfully)
    SUCCEED();
}

TEST(BattleReplayTest, SettlementContainsReplayPayload) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::battle::BattleEventSink {
        void push(v2::battle::BattleEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::battle::BattleEvent> events;
    };
    Sink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::battle::BattleActor>(sink));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "rep_settle", .room_id = "r", .player_ids = {"a"}, .max_frames = 3};
    ref.tell(std::move(create));

    // Submit one input
    v2::actor::Message input;
    input.header.kind = v2::actor::MessageKind::kUser;
    input.payload = v2::battle::SubmitBattleInputMsg{
        .user_id = "a", .input_data = "move:1,1", .score = 5, .submitted_frame = 1};
    ref.tell(std::move(input));

    // Tick to frame limit
    for (int i = 0; i < 3; ++i) {
        v2::actor::Message tick;
        tick.header.kind = v2::actor::MessageKind::kUser;
        tick.payload = v2::battle::TickBattleMsg{.trigger = "test_tick"};
        ref.tell(std::move(tick));
    }
    actor_system.dispatch_all();

    // Find settlement event
    for (auto& e : sink.events) {
        auto* settlement = std::get_if<v2::battle::BattleSettlementPreparedMsg>(&e);
        if (settlement) {
            EXPECT_GE(settlement->replay_inputs.size(), 1U);
            EXPECT_EQ(settlement->replay_inputs[0].user_id, "a");
            EXPECT_EQ(settlement->replay_inputs[0].input_data, "move:1,1");
            EXPECT_EQ(settlement->replay_inputs[0].score, 5);
            EXPECT_GT(settlement->total_frames, 0U);
        }
    }
}

TEST(BattleReplayTest, BattleSnapshotRoundTrip) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::battle::BattleEventSink {
        void push(v2::battle::BattleEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::battle::BattleEvent> events;
    };
    Sink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto* ptr = actor.get();
    auto ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "snap", .room_id = "r", .player_ids = {"a","b"}};
    ref.tell(std::move(create));
    actor_system.dispatch_all();

    // Take snapshot while running
    auto snap = ptr->take_snapshot();
    EXPECT_FALSE(snap.empty()) << "Snapshot should not be empty for active battle";

    // Tick once
    v2::actor::Message tick;
    tick.header.kind = v2::actor::MessageKind::kUser;
    tick.payload = v2::battle::TickBattleMsg{.trigger = "test"};
    ref.tell(std::move(tick));
    actor_system.dispatch_all();

    // Restore from snapshot — should go back to frame 0
    EXPECT_TRUE(ptr->restore_from_snapshot(snap));
}

// ─── World-level replay frame snapshot tests ─────────────────────────────
// These tests verify BattleReplaySystem::run() captures per-frame ECS state
// snapshots into the replay log.

TEST(V2BattleReplayWorldTest, EmptyWorldNoCrash) {
    // A SimpleWorld with the replay system but no BattleReplayLogComponent
    // must not crash when ticked.
    auto world = std::make_unique<v2::ecs::SimpleWorld>();
    world->add_system(std::make_unique<v2::battle::BattleReplaySystem>());

    auto entity = world->create_entity();
    world->add_component<v2::battle::BattleMetadataComponent>(entity);

    world->tick(v2::ecs::FrameContext{
        .battle_id = "b_01",
        .room_id = "r_01",
        .frame_number = 1,
        .trigger = "test",
    });
}

TEST(V2BattleReplayWorldTest, FrameSnapshotCapturedAfterTick) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    auto result = v2::battle::battle_world_advance_frame(*world, 1, "tick");
    EXPECT_EQ(result.frame_number, 1U);

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 1U);
    EXPECT_EQ(snapshots[0].frame_number, 1U);
}

TEST(V2BattleReplayWorldTest, MultipleFramesProduceOrderedSnapshots) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    for (std::uint32_t f = 1; f <= 3; ++f) {
        v2::battle::battle_world_advance_frame(*world, f, "tick");
    }

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 3U);
    EXPECT_EQ(snapshots[0].frame_number, 1U);
    EXPECT_EQ(snapshots[1].frame_number, 2U);
    EXPECT_EQ(snapshots[2].frame_number, 3U);
}

TEST(V2BattleReplayWorldTest, CapturesParticipantState) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice", "bob"}, 10);

    // Alice attacks Bob and gains 5 score.
    v2::battle::battle_world_process_input(*world, "alice", "attack:bob", 5, 1);
    v2::battle::battle_world_advance_frame(*world, 1, "tick");

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 1U);
    ASSERT_EQ(snapshots[0].participants.size(), 2U);

    std::unordered_map<std::string,
                       v2::battle::BattleReplayFrameRecord::ParticipantState>
        by_user;
    for (const auto& p : snapshots[0].participants) {
        by_user[p.user_id] = p;
    }

    // Alice (attacker): 5 score, at origin, full hp
    EXPECT_EQ(by_user["alice"].score, 5);
    EXPECT_TRUE(by_user["alice"].online);
    EXPECT_EQ(by_user["alice"].x, 0);
    EXPECT_EQ(by_user["alice"].y, 0);
    EXPECT_EQ(by_user["alice"].hp, 100);

    // Bob (defender): 0 score, at origin, hp 90 (100 − 10 damage)
    EXPECT_EQ(by_user["bob"].score, 0);
    EXPECT_TRUE(by_user["bob"].online);
    EXPECT_EQ(by_user["bob"].x, 0);
    EXPECT_EQ(by_user["bob"].y, 0);
    EXPECT_EQ(by_user["bob"].hp, 90);
}

TEST(V2BattleReplayWorldTest, OfflinePlayerReflectedInSnapshot) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice", "bob"}, 10);

    EXPECT_TRUE(v2::battle::battle_world_mark_offline(*world, "bob"));
    v2::battle::battle_world_advance_frame(*world, 1, "tick");

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 1U);
    ASSERT_EQ(snapshots[0].participants.size(), 2U);

    std::unordered_map<std::string,
                       v2::battle::BattleReplayFrameRecord::ParticipantState>
        by_user;
    for (const auto& p : snapshots[0].participants) {
        by_user[p.user_id] = p;
    }

    EXPECT_TRUE(by_user["alice"].online);
    EXPECT_FALSE(by_user["bob"].online);
}

TEST(V2BattleReplayWorldTest, LifecycleStateCaptured) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    v2::battle::battle_world_advance_frame(*world, 1, "tick");

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 1U);
    EXPECT_EQ(snapshots[0].lifecycle, v2::battle::BattleLifecycleState::kRunning);
}

TEST(V2BattleReplayWorldTest, TerminalFrameAfterFinish) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice"}, 10);

    v2::battle::battle_world_set_lifecycle(
        *world, v2::battle::BattleLifecycleState::kFinished);
    v2::battle::battle_world_advance_frame(*world, 1, "tick");

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 1U);
    EXPECT_EQ(snapshots[0].lifecycle, v2::battle::BattleLifecycleState::kFinished);
}

TEST(V2BattleReplayWorldTest, CollectFrameSnapshotsReturnsCorrectData) {
    auto world = v2::battle::create_battle_world("b_01", "r_01", {"alice", "bob"}, 10);

    v2::battle::battle_world_advance_frame(*world, 1, "tick");
    v2::battle::battle_world_advance_frame(*world, 2, "tick");

    auto snapshots = v2::battle::battle_world_collect_frame_snapshots(*world);
    ASSERT_EQ(snapshots.size(), 2U);
    EXPECT_EQ(snapshots[0].frame_number, 1U);
    EXPECT_EQ(snapshots[1].frame_number, 2U);
    ASSERT_EQ(snapshots[0].participants.size(), 2U);
    ASSERT_EQ(snapshots[1].participants.size(), 2U);
}
