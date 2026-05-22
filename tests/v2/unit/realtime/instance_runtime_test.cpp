#include <gtest/gtest.h>

#include "v2/realtime/instance_runtime.h"
#include "v2/realtime/types.h"

#include <atomic>
#include <chrono>
#include <thread>
#include <vector>

namespace {

// ─── Echo Plugin: echoes input back as snapshot ─────────────────────
// A minimal plugin for testing the runtime lifecycle.

class EchoPlugin : public v2::realtime::InstancePlugin {
public:
    void on_instance_created(v2::realtime::InstanceContext& ctx) override {
        // Store a simple counter as plugin state
        ctx.plugin_state = this;
    }

    void on_player_join(v2::realtime::InstanceContext& /*ctx*/,
                        const v2::realtime::PlayerContext& /*player*/) override {}

    void on_player_leave(v2::realtime::InstanceContext& /*ctx*/,
                         const v2::realtime::PlayerContext& /*player*/) override {}

    v2::realtime::InputResult on_input(v2::realtime::InstanceContext& ctx,
                                        const v2::realtime::InputEnvelope& input) override {
        last_input_ = input.payload;
        return v2::realtime::InputResult{.accepted = true, .ack_seq = static_cast<std::uint64_t>(++ack_counter_)};
    }

    v2::realtime::TickStats on_tick(v2::realtime::InstanceContext& ctx,
                                     const v2::realtime::FrameContext& frame_ctx) override {
        tick_count_++;
        v2::realtime::TickStats stats;
        stats.frame_number = frame_ctx.frame_number;
        stats.inputs_processed = static_cast<std::uint32_t>(frame_ctx.inputs_this_tick.size());
        stats.tick_duration_ms = 0.1;
        return stats;
    }

    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext& ctx,
                                           bool is_resume) override {
        v2::realtime::Snapshot snap;
        snap.payload_type = "echo.snapshot";
        snap.payload = "tick:" + std::to_string(tick_count_);
        snap.is_resume = is_resume;
        return snap;
    }

    std::string build_settlement(v2::realtime::InstanceContext& ctx,
                                  const v2::realtime::SettlementContext& sctx) override {
        return R"({"status":"ok","total_frames":)" + std::to_string(sctx.total_frames) + "}";
    }

    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext& ctx,
                                                  const v2::realtime::PlayerContext& player) override {
        v2::realtime::Snapshot snap;
        snap.payload_type = "echo.resume";
        snap.payload = "resume:" + player.user_id;
        snap.frame_number = tick_count_;
        snap.is_resume = true;
        return snap;
    }

    int tick_count_ = 0;
    std::string last_input_;
    int ack_counter_ = 0;
};

std::unique_ptr<v2::realtime::InstancePlugin> create_echo_plugin() {
    return std::make_unique<EchoPlugin>();
}

}  // namespace

// ─── Tests ──────────────────────────────────────────────────────────

TEST(InstanceRuntimeTest, CreateAndDestroyInstance) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "test_user";

    auto id = runtime.create_instance("inst_001", "room_001", "echo", {player});
    EXPECT_EQ(id, "inst_001");
    EXPECT_EQ(runtime.instance_count(), 1);
    EXPECT_EQ(runtime.get_instance_state("inst_001"),
              v2::realtime::InstanceState::kWaitingPlayers);

    runtime.destroy_instance("inst_001");
    EXPECT_EQ(runtime.instance_count(), 0);
}

TEST(InstanceRuntimeTest, CreateDuplicateInstanceFails) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    auto id1 = runtime.create_instance("inst_001", "room_001", "echo", {player});
    EXPECT_EQ(id1, "inst_001");

    auto id2 = runtime.create_instance("inst_001", "room_002", "echo", {player});
    EXPECT_TRUE(id2.empty());  // duplicate
}

TEST(InstanceRuntimeTest, UnknownPluginTypeFails) {
    v2::realtime::InstanceRuntime runtime;
    // Don't register any plugin

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    auto id = runtime.create_instance("inst_001", "room_001", "unknown", {player});
    EXPECT_TRUE(id.empty());
}

TEST(InstanceRuntimeTest, SubmitAndProcessInput) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    // Submit input
    v2::realtime::InputEnvelope input;
    input.instance_id = "inst_001";
    input.user_id = "alice";
    input.seq = 1;
    input.payload_type = "echo.input";
    input.payload = R"({"action":"hello"})";

    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick the instance
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    auto stats = runtime.tick_instance("inst_001", 1, now);

    EXPECT_EQ(stats.frame_number, 1);
    EXPECT_EQ(stats.inputs_processed, 1);
}

TEST(InstanceRuntimeTest, InputRejectedForUnknownInstance) {
    v2::realtime::InstanceRuntime runtime;

    v2::realtime::InputEnvelope input;
    input.instance_id = "nonexistent";
    input.user_id = "alice";
    input.seq = 1;

    auto result = runtime.submit_input(input);
    EXPECT_FALSE(result.accepted);
    EXPECT_EQ(result.reject_reason, "instance_not_found");
}

TEST(InstanceRuntimeTest, InstanceLifecycleStateTransitions) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    auto id = runtime.create_instance("inst_001", "room_001", "echo", {player});
    EXPECT_EQ(runtime.get_instance_state(id),
              v2::realtime::InstanceState::kWaitingPlayers);

    // First tick transitions to running
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    runtime.tick_instance("inst_001", 1, now);
    EXPECT_EQ(runtime.get_instance_state(id),
              v2::realtime::InstanceState::kRunning);

    // Finish transitions to finished
    runtime.finish_instance("inst_001");
    EXPECT_EQ(runtime.get_instance_state(id),
              v2::realtime::InstanceState::kFinished);
}

TEST(InstanceRuntimeTest, ResumeSnapshot) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    auto snap = runtime.get_resume_snapshot("inst_001", "alice");
    EXPECT_TRUE(snap.is_resume);
    EXPECT_EQ(snap.payload_type, "echo.resume");
    EXPECT_EQ(snap.payload, "resume:alice");
}

TEST(InstanceRuntimeTest, ListInstances) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("a", "room_1", "echo", {player});
    runtime.create_instance("b", "room_2", "echo", {player});

    auto instances = runtime.list_instances();
    EXPECT_EQ(instances.size(), 2);
}

TEST(InstanceRuntimeTest, InputQueueLimit) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    // Submit inputs up to the limit (64 by default)
    for (int i = 0; i < 64; i++) {
        v2::realtime::InputEnvelope input;
        input.instance_id = "inst_001";
        input.user_id = "alice";
        input.seq = i + 1;
        input.payload_type = "echo.input";
        auto result = runtime.submit_input(input);
        EXPECT_TRUE(result.accepted) << "input " << i;
    }

    // The 65th should be rejected (queue full)
    v2::realtime::InputEnvelope overflow;
    overflow.instance_id = "inst_001";
    overflow.user_id = "alice";
    overflow.seq = 65;
    auto result = runtime.submit_input(overflow);
    EXPECT_FALSE(result.accepted);
    EXPECT_EQ(result.reject_reason, "input_queue_full");

    // Tick to drain the queue
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    auto stats = runtime.tick_instance("inst_001", 1, now);

    // After tick, queue is drained and new inputs should be accepted
    v2::realtime::InputEnvelope after_tick;
    after_tick.instance_id = "inst_001";
    after_tick.user_id = "alice";
    after_tick.seq = 66;
    after_tick.payload_type = "echo.input";
    auto result2 = runtime.submit_input(after_tick);
    EXPECT_TRUE(result2.accepted);
}

// Duplicate seq is rejected
TEST(InstanceRuntimeTest, DuplicateSeqRejected) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    v2::realtime::InputEnvelope input;
    input.instance_id = "inst_001";
    input.user_id = "alice";
    input.seq = 1;
    input.payload_type = "echo.input";

    auto r1 = runtime.submit_input(input);
    EXPECT_TRUE(r1.accepted);

    // Same seq should be rejected
    auto r2 = runtime.submit_input(input);
    EXPECT_FALSE(r2.accepted);
    EXPECT_EQ(r2.reject_reason, "duplicate_seq");
}

// ─── P6: Reliability / Recovery ───────────────────────────────────

TEST(InstanceRuntimeTest, ResumeAfterDisconnect) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    // Tick to advance state
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    runtime.tick_instance("inst_001", 1, now);

    // Get resume snapshot (simulating reconnect)
    auto snap = runtime.get_resume_snapshot("inst_001", "alice");
    EXPECT_GT(snap.frame_number, 0);
    EXPECT_TRUE(snap.is_resume);
}

TEST(InstanceRuntimeTest, ResumeFailsForNonexistentPlayer) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    // Non-existent user should get empty snapshot
    auto snap = runtime.get_resume_snapshot("inst_001", "nonexistent");
    EXPECT_TRUE(snap.payload.empty());
}

TEST(InstanceRuntimeTest, ResumeFailsForNonexistentInstance) {
    v2::realtime::InstanceRuntime runtime;

    auto snap = runtime.get_resume_snapshot("no_such_inst", "alice");
    EXPECT_TRUE(snap.payload.empty());
}

TEST(InstanceRuntimeTest, CanSubmitInputAfterResume) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    // Tick
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    runtime.tick_instance("inst_001", 1, now);

    // "Disconnect" happens here — in the real system the session drops

    // "Reconnect" — get resume snapshot
    auto snap = runtime.get_resume_snapshot("inst_001", "alice");
    EXPECT_TRUE(snap.is_resume);

    // Submit new input after resume
    v2::realtime::InputEnvelope input;
    input.instance_id = "inst_001";
    input.user_id = "alice";
    input.seq = 10;  // new seq after resume
    input.payload_type = "echo.input";
    input.payload = R"({"action":"move"})";

    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);
}

TEST(InstanceRuntimeTest, MaxInstancesLimit) {
    v2::realtime::RuntimeConfig config;
    config.max_instances = 1;

    v2::realtime::InstanceRuntime runtime(config);
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    auto id1 = runtime.create_instance("a", "room_1", "echo", {player});
    EXPECT_EQ(id1, "a");
    EXPECT_EQ(runtime.instance_count(), 1);

    // Second instance should fail
    auto id2 = runtime.create_instance("b", "room_2", "echo", {player});
    EXPECT_TRUE(id2.empty());
}

TEST(InstanceRuntimeTest, MultipleInstancesDontInterfere) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("a", "room_1", "echo", {player});
    runtime.create_instance("b", "room_2", "echo", {player});

    // Submit input to instance a
    v2::realtime::InputEnvelope input;
    input.instance_id = "a";
    input.user_id = "alice";
    input.seq = 1;
    auto r1 = runtime.submit_input(input);
    EXPECT_TRUE(r1.accepted);

    // Submit input to instance b
    input.instance_id = "b";
    input.seq = 1;
    auto r2 = runtime.submit_input(input);
    EXPECT_TRUE(r2.accepted);

    // Tick both
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    runtime.tick_all(now);

    EXPECT_EQ(runtime.instance_count(), 2);
}

// Instance list includes state info
TEST(InstanceRuntimeTest, InstanceSnapshotHasStateInfo) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";

    runtime.create_instance("inst_001", "room_001", "echo", {player});

    auto snapshots = runtime.list_instances();
    ASSERT_GE(snapshots.size(), 1);
    EXPECT_EQ(snapshots[0].instance_id, "inst_001");
    EXPECT_GT(snapshots[0].created_at_ms, 0);
}
