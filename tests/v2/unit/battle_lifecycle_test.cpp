// v2.5.0: BattleLifecycleSystem unit tests
//
// Tests for lifecycle state transitions, idle/offline timeouts, and
// cleanup on finished battles.

#include <gtest/gtest.h>

#include "v2/battle/runtime_components.h"
#include "v2/battle/runtime_world.h"

#include <memory>

namespace v2_battle_lifecycle_test {

using namespace v2::battle;
using v2::ecs::FrameContext;
using v2::ecs::SimpleWorld;

// ─── Auto-transition ───────────────────────────────────────────────

TEST(V2BattleLifecycleTest, AutoTransitionFromCreatedToRunning) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    const auto entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(entity);
    meta.lifecycle = BattleLifecycleState::kCreated;

    world->tick(FrameContext{
        .battle_id = "b1",
        .room_id = "r1",
        .frame_number = 1,
    });

    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);
}

// ─── Stays running during normal operation ─────────────────────────

TEST(V2BattleLifecycleTest, StaysRunningDuringNormalOperation) {
    auto world = create_battle_world("b1", "r1", {"alice"}, 100);

    for (std::uint32_t f = 1; f <= 10; ++f) {
        battle_world_process_input(*world, "alice", "move:1,1", 0, f);
        battle_world_advance_frame(*world, f, "tick");
    }

    EXPECT_EQ(battle_world_lifecycle(*world), BattleLifecycleState::kRunning);
}

// ─── Idle timeout ──────────────────────────────────────────────────

TEST(V2BattleLifecycleTest, DetectsIdleTimeout) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>(5, 60));

    const auto entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(entity);
    meta.lifecycle = BattleLifecycleState::kRunning;
    meta.next_input_seq = 1;

    // Tick 5 times: idle_frames_ = 4 still < 5
    for (std::uint32_t f = 1; f <= 5; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // 6th tick: idle_frames_ = 5 >= 5 -> kFinished
    world->tick(FrameContext{.frame_number = 6});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kFinished);
}

// ─── All-players-offline timeout ──────────────────────────────────

TEST(V2BattleLifecycleTest, DetectsAllPlayersOffline) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>(300, 3));

    const auto meta_entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(meta_entity);
    meta.lifecycle = BattleLifecycleState::kRunning;
    meta.next_input_seq = 1;

    const auto p1 = world->create_entity();
    auto& p1c = world->add_component<BattleParticipantComponent>(p1);
    p1c.online = false;

    const auto p2 = world->create_entity();
    auto& p2c = world->add_component<BattleParticipantComponent>(p2);
    p2c.online = false;

    // Tick 2 times: offline_frames_ = 2 < 3
    for (std::uint32_t f = 1; f <= 2; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // 3rd tick: offline_frames_ = 3 >= 3 -> kFinished
    world->tick(FrameContext{.frame_number = 3});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kFinished);
}

// ─── Cleanup on finished ──────────────────────────────────────────

TEST(V2BattleLifecycleTest, CleanupOnFinished) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    const auto meta_entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(meta_entity);
    meta.lifecycle = BattleLifecycleState::kFinished;

    const auto proj_entity = world->create_entity();
    auto& proj = world->add_component<ProjectileComponent>(proj_entity);
    proj.projectile_id = "proj_1";

    const auto dot_entity = world->create_entity();
    world->add_component<DamageOverlayComponent>(dot_entity);

    // Verify components exist before tick
    EXPECT_NE(world->get_component<ProjectileComponent>(proj_entity), nullptr);
    EXPECT_NE(world->get_component<DamageOverlayComponent>(dot_entity), nullptr);

    // Tick -> cleanup
    world->tick(FrameContext{});

    // Verify components are removed
    EXPECT_EQ(world->get_component<ProjectileComponent>(proj_entity), nullptr);
    EXPECT_EQ(world->get_component<DamageOverlayComponent>(dot_entity), nullptr);
}

// ─── No crash with empty world ────────────────────────────────────

TEST(V2BattleLifecycleTest, EmptyWorldNoCrash) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    // No entities or components at all
    world->tick(FrameContext{});
    // Should not crash
    SUCCEED();
}

// ─── No crash when world has no BattleMetadataComponent ───────────

TEST(V2BattleLifecycleTest, NoMetadataNoCrash) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    // Add an entity with a non-metadata component
    const auto entity = world->create_entity();
    world->add_component<BattleParticipantComponent>(entity);

    world->tick(FrameContext{});
    SUCCEED();
}

// ─── Idle counter resets when inputs arrive ───────────────────────

TEST(V2BattleLifecycleTest, IdleCounterResetsOnInput) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>(5, 60));

    const auto entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(entity);
    meta.lifecycle = BattleLifecycleState::kRunning;
    meta.next_input_seq = 1;

    // Tick 3 times without input: idle_frames_ becomes 2 (tick 1 has
    // has_input=true from initial seq 0->1, ticks 2-3 accumulate)
    for (std::uint32_t f = 1; f <= 3; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // Simulate input arriving
    meta.next_input_seq = 2;

    // Tick: input detected, idle_frames_ resets to 0
    world->tick(FrameContext{.frame_number = 4});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // Tick 4 more times without input: idle_frames_ = 4 < 5
    for (std::uint32_t f = 5; f <= 8; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // One more tick: idle_frames_ = 5 >= 5 -> kFinished
    world->tick(FrameContext{.frame_number = 9});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kFinished);
}

}  // namespace v2_battle_lifecycle_test

// =========================================================================
// Extended lifecycle tests using InstanceRuntime
//
// Tests cross-instance concurrency, lifecycle edge cases (concurrent
// finish, disconnect during finish, reconnect after finish, player
// activity after start), and a 500x500 soak stress test.
// =========================================================================

#include "v2/realtime/instance_runtime.h"
#include "v2/realtime/types.h"
#include "v2/battle/tank_battle_plugin.h"

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

namespace {

// ─── Echo Plugin (local definition for this TU) ─────────────────────────
// Minimal plugin that echoes input back and tracks tick count.

class LifecycleEchoPlugin : public v2::realtime::InstancePlugin {
public:
    void on_instance_created(v2::realtime::InstanceContext& ctx) override {
        ctx.plugin_state = this;
    }

    void on_player_join(v2::realtime::InstanceContext&,
                        const v2::realtime::PlayerContext&) override {}

    void on_player_leave(v2::realtime::InstanceContext&,
                         const v2::realtime::PlayerContext&) override {}

    v2::realtime::InputResult on_input(
        v2::realtime::InstanceContext&,
        const v2::realtime::InputEnvelope& input) override {
        last_input_ = input.payload;
        return v2::realtime::InputResult{
            .accepted = true,
            .ack_seq = static_cast<std::uint64_t>(++ack_counter_),
        };
    }

    v2::realtime::TickStats on_tick(
        v2::realtime::InstanceContext&,
        const v2::realtime::FrameContext& frame_ctx) noexcept override {
        tick_count_++;
        return v2::realtime::TickStats{
            .frame_number = frame_ctx.frame_number,
            .inputs_processed =
                static_cast<std::uint32_t>(frame_ctx.inputs_this_tick.size()),
            .tick_duration_ms = 0.1,
        };
    }

    v2::realtime::Snapshot build_snapshot(
        v2::realtime::InstanceContext&,
        bool is_resume) noexcept override {
        v2::realtime::Snapshot snap;
        snap.payload_type = "echo.snapshot";
        snap.payload = "tick:" + std::to_string(tick_count_);
        snap.is_resume = is_resume;
        return snap;
    }

    std::string build_settlement(
        v2::realtime::InstanceContext&,
        const v2::realtime::SettlementContext& sctx) noexcept override {
        return R"({"status":"ok","total_frames":)" +
               std::to_string(sctx.total_frames) + "}";
    }

    v2::realtime::Snapshot build_resume_snapshot(
        v2::realtime::InstanceContext&,
        const v2::realtime::PlayerContext& player) noexcept override {
        v2::realtime::Snapshot snap;
        snap.payload_type = "echo.resume";
        snap.payload = "resume:" + player.user_id;
        snap.frame_number = static_cast<std::uint32_t>(tick_count_);
        snap.is_resume = true;
        return snap;
    }

    int tick_count_ = 0;
    std::string last_input_;
    int ack_counter_ = 0;
};

std::unique_ptr<v2::realtime::InstancePlugin> create_echo_plugin() {
    return std::make_unique<LifecycleEchoPlugin>();
}

// ─── Fake Plugin: absolutely minimal overhead for the soak test ─────────

class FakePlugin : public v2::realtime::InstancePlugin {
public:
    void on_instance_created(v2::realtime::InstanceContext& ctx) override {
        ctx.plugin_state = this;
    }

    void on_player_join(v2::realtime::InstanceContext&,
                        const v2::realtime::PlayerContext&) override {}

    void on_player_leave(v2::realtime::InstanceContext&,
                         const v2::realtime::PlayerContext&) override {}

    v2::realtime::InputResult on_input(
        v2::realtime::InstanceContext&,
        const v2::realtime::InputEnvelope&) override {
        return v2::realtime::InputResult{.accepted = true};
    }

    v2::realtime::TickStats on_tick(
        v2::realtime::InstanceContext&,
        const v2::realtime::FrameContext&) noexcept override {
        return v2::realtime::TickStats{};
    }

    v2::realtime::Snapshot build_snapshot(
        v2::realtime::InstanceContext&,
        bool) noexcept override {
        return v2::realtime::Snapshot{};
    }

    std::string build_settlement(
        v2::realtime::InstanceContext&,
        const v2::realtime::SettlementContext&) noexcept override {
        return R"({"status":"ok"})";
    }

    v2::realtime::Snapshot build_resume_snapshot(
        v2::realtime::InstanceContext&,
        const v2::realtime::PlayerContext&) noexcept override {
        return v2::realtime::Snapshot{};
    }
};

std::unique_ptr<v2::realtime::InstancePlugin> create_fake_plugin() {
    return std::make_unique<FakePlugin>();
}

// ─── Tank Battle factory (local) ───────────────────────────────────────

std::unique_ptr<v2::realtime::InstancePlugin> create_tank_battle_plugin() {
    return std::make_unique<v2::battle::TankBattlePlugin>();
}

// ─── Helpers ───────────────────────────────────────────────────────────

std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

v2::realtime::InputEnvelope make_tank_input(
    const std::string& instance_id,
    const std::string& user_id,
    std::uint64_t seq,
    const std::string& payload) {
    v2::realtime::InputEnvelope input;
    input.instance_id = instance_id;
    input.user_id = user_id;
    input.seq = seq;
    input.payload_type = "tank.input";
    input.payload = payload;
    return input;
}

}  // namespace

// ═══════════════════════════════════════════════════════════════════════
// 1. Cross-instance concurrency
// ═══════════════════════════════════════════════════════════════════════

TEST(BattleLifecycleExtendedTest, CrossInstanceConcurrency) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);
    runtime.register_plugin("fake", &create_fake_plugin);

    v2::realtime::PlayerContext p;
    p.user_id = "alice";

    // Create three instances — two echo, one fake (different plugin types).
    // Each `create_instance` calls the factory, producing an independent
    // plugin instance with its own tick counter.
    ASSERT_FALSE(runtime.create_instance("a", "room_1", "echo", {p}).empty());
    ASSERT_FALSE(runtime.create_instance("b", "room_2", "echo", {p}).empty());
    ASSERT_FALSE(runtime.create_instance("c", "room_3", "fake", {p}).empty());
    ASSERT_EQ(runtime.instance_count(), 3);

    // ── Phase 1: tick all 25 times, verify independent frame advance ──
    auto ts = now_ms();
    for (std::uint32_t f = 1; f <= 25; ++f) {
        auto results = runtime.tick_all(ts);
        ASSERT_EQ(results.size(), 3);
    }

    auto snaps = runtime.list_instances();
    for (const auto& s : snaps) {
        EXPECT_EQ(s.frame_number, 25)
            << "instance " << s.instance_id
            << " should have frame 25 after 25 ticks";
    }

    // ── Phase 2: input to one instance must NOT leak into others ──
    v2::realtime::InputEnvelope in;
    in.instance_id = "a";
    in.user_id = "alice";
    in.seq = 1;
    in.payload_type = "echo.input";
    in.payload = R"({"action":"hello"})";
    ASSERT_TRUE(runtime.submit_input(in).accepted);

    snaps = runtime.list_instances();
    for (const auto& s : snaps) {
        if (s.instance_id == "a") {
            EXPECT_EQ(s.input_queue_size, 1);
        } else {
            EXPECT_EQ(s.input_queue_size, 0)
                << "instance " << s.instance_id
                << " should have empty input queue";
        }
    }

    // ── Phase 3: tick all — all advance, only 'a' had input ──
    runtime.tick_all(ts);
    snaps = runtime.list_instances();
    for (const auto& s : snaps) {
        EXPECT_EQ(s.frame_number, 26) << s.instance_id;
    }

    // ── Phase 4: destroy one, verify others are unaffected ──
    runtime.destroy_instance("b");
    EXPECT_EQ(runtime.instance_count(), 2);
    EXPECT_EQ(runtime.get_instance_state("a"),
              v2::realtime::InstanceState::kRunning);
    EXPECT_EQ(runtime.get_instance_state("c"),
              v2::realtime::InstanceState::kRunning);
    EXPECT_EQ(runtime.get_instance_state("b"),
              v2::realtime::InstanceState::kClosed);

    // Remaining instances still tick normally
    runtime.tick_all(ts);
    EXPECT_TRUE(runtime.contains_instance("a"));
    EXPECT_TRUE(runtime.contains_instance("c"));
}

// ═══════════════════════════════════════════════════════════════════════
// 2. Lifecycle edge cases
// ═══════════════════════════════════════════════════════════════════════

// ─── 2a. Concurrent finish ─────────────────────────────────────────────
//
// Both players submit finish on the same frame. The runtime must drain
// both inputs and process them without crashing or producing duplicate
// settlement events.

TEST(BattleLifecycleExtendedTest, ConcurrentFinish) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    // Capture settlement events to verify exactly one is emitted
    int finish_event_count = 0;
    (void)runtime.set_event_callback(
        [&](const v2::realtime::InstanceEvent& event) {
            if (event.type == v2::realtime::InstanceEvent::Type::kInstanceFinished) {
                ++finish_event_count;
            }
        });

    v2::realtime::PlayerContext p1, p2;
    p1.user_id = "alice";
    p2.user_id = "bob";

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {p1, p2}, 33, 100, 30000);

    // Both players submit finish in the same frame
    auto in1 = make_tank_input("battle_001", "alice", 1,
                                R"({"action":"finish","reason":"surrender"})");
    auto in2 = make_tank_input("battle_001", "bob", 1,
                                R"({"action":"finish","reason":"surrender"})");
    EXPECT_TRUE(runtime.submit_input(in1).accepted);
    EXPECT_TRUE(runtime.submit_input(in2).accepted);

    // Single tick drains both inputs and triggers finish
    auto stats = runtime.tick_instance("battle_001", 1, now_ms());
    EXPECT_TRUE(stats.should_finish);
    EXPECT_EQ(runtime.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kFinished);

    // Exactly one finish event should have been emitted
    EXPECT_EQ(finish_event_count, 1);
}

// ─── 2b. Disconnect during finish ──────────────────────────────────────
//
// A player submits a finish action. Before the tick processes it, the
// backend detects another player's disconnection and calls
// finish_instance() externally. This tests the race between plugin-driven
// finish and external-trigger finish.

TEST(BattleLifecycleExtendedTest, DisconnectDuringFinish) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    int finish_event_count = 0;
    (void)runtime.set_event_callback(
        [&](const v2::realtime::InstanceEvent& event) {
            if (event.type == v2::realtime::InstanceEvent::Type::kInstanceFinished) {
                ++finish_event_count;
            }
        });

    v2::realtime::PlayerContext p1, p2;
    p1.user_id = "alice";
    p2.user_id = "bob";

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {p1, p2}, 33, 100, 30000);

    // Start the instance
    runtime.tick_instance("battle_001", 1, now_ms());

    // Alice submits finish
    auto input = make_tank_input("battle_001", "alice", 2,
                                  R"({"action":"finish","reason":"surrender"})");
    EXPECT_TRUE(runtime.submit_input(input).accepted);

    // Bob "disconnects" — backend calls finish_instance externally while
    // the finish input is still in the queue
    runtime.finish_instance("battle_001",
                            v2::realtime::FinishReason::kPlayerDisconnected);

    // Tick — the runtime has already finished the instance, so this tick
    // should see the finished state and return early without error.
    auto stats = runtime.tick_instance("battle_001", 2, now_ms());

    // Instance must be finished by one of the two finish paths
    EXPECT_EQ(runtime.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kFinished);

    // Exactly one finish event should be emitted (the second finish
    // call becomes a no-op since the state is already finished).
    EXPECT_EQ(finish_event_count, 1);
}

// ─── 2c. Reconnect after finish ────────────────────────────────────────
//
// After finish_instance(), the instance is still in memory. A reconnect
// attempt should return the final game state snapshot. After
// destroy_instance(), the same attempt returns empty.

TEST(BattleLifecycleExtendedTest, ReconnectAfterFinish) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    v2::realtime::PlayerContext p1, p2;
    p1.user_id = "alice";
    p2.user_id = "bob";

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {p1, p2}, 33, 100, 30000);

    // Tick to start the instance
    runtime.tick_instance("battle_001", 1, now_ms());

    // Finish normally
    runtime.finish_instance("battle_001");
    EXPECT_EQ(runtime.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kFinished);

    // Reconnect while finished (instance still in memory) returns the
    // final game state — the plugin state is not destroyed on finish.
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    EXPECT_FALSE(snap.payload.empty())
        << "resume snapshot should contain final state after finish";
    EXPECT_TRUE(snap.is_resume);
    EXPECT_EQ(snap.payload_type, "tank.snapshot");

    // Non-existent player still returns empty
    auto no_such_player = runtime.get_resume_snapshot("battle_001", "nobody");
    EXPECT_TRUE(no_such_player.payload.empty());

    // After destroy, reconnect returns empty
    runtime.destroy_instance("battle_001");
    auto after_destroy = runtime.get_resume_snapshot("battle_001", "alice");
    EXPECT_TRUE(after_destroy.payload.empty());
}

// ─── 2d. Player activity after start ───────────────────────────────────
//
// Verifies that a known player can interact after the instance starts,
// and that input from an unknown player (not in the initial player list)
// is handled without crashing.

TEST(BattleLifecycleExtendedTest, PlayerActivityAfterStart) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    v2::realtime::PlayerContext p1;
    p1.user_id = "alice";

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {p1}, 33, 100, 30000);

    // First tick transitions from kWaitingPlayers to kRunning
    auto ts = now_ms();
    runtime.tick_instance("battle_001", 1, ts);
    EXPECT_EQ(runtime.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kRunning);

    // Alice (known player) submits input after the instance is running
    auto input = make_tank_input("battle_001", "alice", 1,
                                  R"({"action":"move","x":25,"y":25})");
    EXPECT_TRUE(runtime.submit_input(input).accepted);

    // Tick processes Alice's input
    auto stats = runtime.tick_instance("battle_001", 2, ts);
    EXPECT_EQ(stats.inputs_processed, 1);
    EXPECT_EQ(stats.frame_number, 2);

    // A non-existent player's input is accepted at the queue level
    // but rejected by the plugin during on_input (player not found)
    auto bad = make_tank_input("battle_001", "stranger", 1,
                                R"({"action":"move","x":99,"y":99})");
    EXPECT_TRUE(runtime.submit_input(bad).accepted)
        << "runtime accepts at queue level even for unknown player";

    stats = runtime.tick_instance("battle_001", 3, ts);
    // The unknown player's input was dequeued but the plugin rejected
    // it because "stranger" is not a known player entity.
    // Therefore inputs_processed should be 0 for this tick.
    EXPECT_EQ(stats.inputs_processed, 0);

    // Instance continues running normally
    EXPECT_EQ(runtime.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kRunning);
}

// ═══════════════════════════════════════════════════════════════════════
// 3. 500-instance x 500-tick soak test
// ═══════════════════════════════════════════════════════════════════════
//
// Stress test: create 500 instances with the minimal FakePlugin, then
// run 500 tick_all cycles. Verifies no crashes and that every instance
// survives all ticks.
//
// Disabled by default. Run with:
//   ctest --output-on-failure -C Debug -R "battle_lifecycle_test" -- --gtest_also_run_disabled_tests

TEST(BattleLifecycleExtendedTest, DISABLED_Soak500x500) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("fake", &create_fake_plugin);

    // ── Create 500 instances ──────────────────────────────────────
    for (int i = 0; i < 500; ++i) {
        v2::realtime::PlayerContext player;
        player.user_id = "player_" + std::to_string(i);

        auto id = runtime.create_instance(
            "inst_" + std::to_string(i),
            "soak_room",
            "fake",
            {player},
            33,     // tick_interval_ms
            0,      // max_frames (unlimited)
            30000); // resume_window_ms
        ASSERT_FALSE(id.empty()) << "failed at instance " << i;
    }
    ASSERT_EQ(runtime.instance_count(), 500);

    // ── Run 500 ticks on all instances ────────────────────────────
    auto start = now_ms();
    for (std::uint32_t f = 1; f <= 500; ++f) {
        auto results = runtime.tick_all(start);
        ASSERT_EQ(results.size(), 500)
            << "tick_all returned " << results.size()
            << " stats at cycle " << f;
    }
    auto elapsed = now_ms() - start;

    // ── Verify no crashes and all instances are at correct frame ──
    ASSERT_EQ(runtime.instance_count(), 500);

    auto snapshots = runtime.list_instances();
    ASSERT_EQ(snapshots.size(), 500);
    for (const auto& snap : snapshots) {
        EXPECT_EQ(snap.frame_number, 500)
            << "instance " << snap.instance_id
            << " should have frame 500 after 500 ticks";
        EXPECT_EQ(snap.state, v2::realtime::InstanceState::kRunning)
            << snap.instance_id;
    }

    (void)elapsed; // elapsed time is visible in test output
}
