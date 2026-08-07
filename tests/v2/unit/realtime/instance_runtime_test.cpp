#include <gtest/gtest.h>

#include "v2/realtime/instance_runtime.h"
#include "v2/realtime/types.h"

#include <atomic>
#include <barrier>
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
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
                                     const v2::realtime::FrameContext& frame_ctx) noexcept override {
        tick_count_++;
        v2::realtime::TickStats stats;
        stats.frame_number = frame_ctx.frame_number;
        stats.inputs_processed = static_cast<std::uint32_t>(frame_ctx.inputs_this_tick.size());
        stats.tick_duration_ms = 0.1;
        return stats;
    }

    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext& ctx,
                                           bool is_resume) noexcept override {
        v2::realtime::Snapshot snap;
        snap.payload_type = "echo.snapshot";
        snap.payload = "tick:" + std::to_string(tick_count_);
        snap.is_resume = is_resume;
        return snap;
    }

    std::string build_settlement(v2::realtime::InstanceContext& ctx,
                                  const v2::realtime::SettlementContext& sctx) noexcept override {
        return R"({"status":"ok","total_frames":)" + std::to_string(sctx.total_frames) + "}";
    }

    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext& ctx,
                                                  const v2::realtime::PlayerContext& player) noexcept override {
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

struct ConcurrentTickState {
    std::mutex mutex;
    std::condition_variable cv;
    std::atomic<int> entered{0};
    std::atomic<int> peer_observations{0};
};

class ConcurrentTickPlugin final : public v2::realtime::InstancePlugin {
public:
    explicit ConcurrentTickPlugin(std::shared_ptr<ConcurrentTickState> state)
        : state_(std::move(state)) {}

    void on_instance_created(v2::realtime::InstanceContext&) override {}
    void on_player_join(v2::realtime::InstanceContext&, const v2::realtime::PlayerContext&) override {}
    void on_player_leave(v2::realtime::InstanceContext&, const v2::realtime::PlayerContext&) override {}
    v2::realtime::InputResult on_input(v2::realtime::InstanceContext&,
                                        const v2::realtime::InputEnvelope&) override {
        return {.accepted = true, .ack_seq = 1};
    }
    v2::realtime::TickStats on_tick(v2::realtime::InstanceContext&,
                                     const v2::realtime::FrameContext& frame_ctx) noexcept override {
        std::unique_lock<std::mutex> lock(state_->mutex);
        state_->entered.fetch_add(1, std::memory_order_release);
        state_->cv.notify_all();
        if (state_->cv.wait_for(lock, std::chrono::milliseconds(200), [this] {
                return state_->entered.load(std::memory_order_acquire) >= 2;
            })) {
            state_->peer_observations.fetch_add(1, std::memory_order_release);
        }
        return {.frame_number = frame_ctx.frame_number};
    }
    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext&, bool) noexcept override {
        return {};
    }
    std::string build_settlement(v2::realtime::InstanceContext&,
                                 const v2::realtime::SettlementContext&) noexcept override {
        return "{}";
    }
    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext&,
                                                  const v2::realtime::PlayerContext&) noexcept override {
        return {};
    }

private:
    std::shared_ptr<ConcurrentTickState> state_;
};

std::shared_ptr<ConcurrentTickState> concurrent_tick_state;

std::unique_ptr<v2::realtime::InstancePlugin> create_concurrent_tick_plugin() {
    return std::make_unique<ConcurrentTickPlugin>(concurrent_tick_state);
}

std::atomic<int> owning_plugin_allocations{0};
std::atomic<int> owning_plugin_destructions{0};
std::atomic<bool> owning_plugin_throw_on_create{false};

class OwningPlugin final : public v2::realtime::InstancePlugin {
public:
    void on_instance_created(v2::realtime::InstanceContext& ctx) override {
        ctx.plugin_state = new int(42);
        owning_plugin_allocations.fetch_add(1, std::memory_order_relaxed);
        if (owning_plugin_throw_on_create.load(std::memory_order_relaxed)) {
            throw std::runtime_error("creation failed after state allocation");
        }
    }

    void on_instance_destroyed(v2::realtime::InstanceContext& ctx) noexcept override {
        delete static_cast<int*>(ctx.plugin_state);
        ctx.plugin_state = nullptr;
        owning_plugin_destructions.fetch_add(1, std::memory_order_relaxed);
    }

    void on_player_join(v2::realtime::InstanceContext&,
                        const v2::realtime::PlayerContext&) override {}
    void on_player_leave(v2::realtime::InstanceContext&,
                         const v2::realtime::PlayerContext&) override {}
    v2::realtime::InputResult on_input(
        v2::realtime::InstanceContext&,
        const v2::realtime::InputEnvelope&) override { return {.accepted = true}; }
    v2::realtime::TickStats on_tick(
        v2::realtime::InstanceContext&,
        const v2::realtime::FrameContext&) noexcept override { return {}; }
    v2::realtime::Snapshot build_snapshot(
        v2::realtime::InstanceContext&, bool) noexcept override { return {}; }
    std::string build_settlement(
        v2::realtime::InstanceContext&,
        const v2::realtime::SettlementContext&) noexcept override { return "{}"; }
    v2::realtime::Snapshot build_resume_snapshot(
        v2::realtime::InstanceContext&,
        const v2::realtime::PlayerContext&) noexcept override { return {}; }
};

std::unique_ptr<v2::realtime::InstancePlugin> create_owning_plugin() {
    return std::make_unique<OwningPlugin>();
}

void reset_owning_plugin_counters(bool throw_on_create = false) {
    owning_plugin_allocations.store(0, std::memory_order_relaxed);
    owning_plugin_destructions.store(0, std::memory_order_relaxed);
    owning_plugin_throw_on_create.store(throw_on_create, std::memory_order_relaxed);
}

struct PlayerLifecycleState {
    std::atomic<int> joins{0};
    std::atomic<int> leaves{0};
    std::atomic<bool> join_saw_player_absent{false};
    std::atomic<bool> leave_saw_player_present{false};
    std::atomic<bool> throw_on_join{false};
    std::atomic<bool> throw_on_leave{false};
};

std::shared_ptr<PlayerLifecycleState> player_lifecycle_state;

class PlayerLifecyclePlugin final : public v2::realtime::InstancePlugin {
public:
    explicit PlayerLifecyclePlugin(std::shared_ptr<PlayerLifecycleState> state)
        : state_(std::move(state)) {}

    void on_instance_created(v2::realtime::InstanceContext&) override {}

    void on_player_join(v2::realtime::InstanceContext& ctx,
                        const v2::realtime::PlayerContext& player) override {
        state_->join_saw_player_absent.store(
            ctx.find_player(player.user_id) == nullptr, std::memory_order_relaxed);
        state_->joins.fetch_add(1, std::memory_order_relaxed);
        if (state_->throw_on_join.load(std::memory_order_relaxed)) {
            throw std::runtime_error("join failure");
        }
    }

    void on_player_leave(v2::realtime::InstanceContext& ctx,
                         const v2::realtime::PlayerContext& player) override {
        state_->leave_saw_player_present.store(
            ctx.find_player(player.user_id) != nullptr, std::memory_order_relaxed);
        state_->leaves.fetch_add(1, std::memory_order_relaxed);
        if (state_->throw_on_leave.load(std::memory_order_relaxed)) {
            throw std::runtime_error("leave failure");
        }
    }

    v2::realtime::InputResult on_input(
        v2::realtime::InstanceContext&,
        const v2::realtime::InputEnvelope& input) override {
        return {.accepted = true, .ack_seq = input.seq};
    }

    v2::realtime::TickStats on_tick(
        v2::realtime::InstanceContext&,
        const v2::realtime::FrameContext& frame_ctx) noexcept override {
        return {
            .frame_number = frame_ctx.frame_number,
            .inputs_processed = static_cast<std::uint32_t>(frame_ctx.inputs_this_tick.size()),
        };
    }

    v2::realtime::Snapshot build_snapshot(
        v2::realtime::InstanceContext&, bool) noexcept override {
        return {};
    }

    std::string build_settlement(
        v2::realtime::InstanceContext&,
        const v2::realtime::SettlementContext&) noexcept override {
        return "{}";
    }

    v2::realtime::Snapshot build_resume_snapshot(
        v2::realtime::InstanceContext&,
        const v2::realtime::PlayerContext& player) noexcept override {
        return {.payload = player.user_id, .is_resume = true};
    }

private:
    std::shared_ptr<PlayerLifecycleState> state_;
};

std::unique_ptr<v2::realtime::InstancePlugin> create_player_lifecycle_plugin() {
    return std::make_unique<PlayerLifecyclePlugin>(player_lifecycle_state);
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

TEST(InstanceRuntimeTest, DestroyedInstanceCannotBeTicked) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "test_user";
    ASSERT_FALSE(runtime.create_instance("inst_001", "room_001", "echo", {player}).empty());
    runtime.destroy_instance("inst_001");

    const auto stats = runtime.tick_instance("inst_001", 1, 1);
    EXPECT_TRUE(stats.should_finish);
    EXPECT_EQ(stats.finish_reason, v2::realtime::FinishReason::kError);
    EXPECT_TRUE(runtime.tick_all(1).empty());
}

TEST(InstanceRuntimeTest, DestroyInstanceReleasesPluginOwnedState) {
    reset_owning_plugin_counters();
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("owning", &create_owning_plugin);

    ASSERT_EQ(runtime.create_instance("owned", "room", "owning", {}), "owned");
    EXPECT_EQ(owning_plugin_allocations.load(std::memory_order_relaxed), 1);
    runtime.destroy_instance("owned");
    EXPECT_EQ(owning_plugin_destructions.load(std::memory_order_relaxed), 1);
}

TEST(InstanceRuntimeTest, RuntimeDestructionReleasesAllPluginOwnedState) {
    reset_owning_plugin_counters();
    {
        v2::realtime::InstanceRuntime runtime;
        runtime.register_plugin("owning", &create_owning_plugin);
        ASSERT_EQ(runtime.create_instance("one", "room", "owning", {}), "one");
        ASSERT_EQ(runtime.create_instance("two", "room", "owning", {}), "two");
    }
    EXPECT_EQ(owning_plugin_allocations.load(std::memory_order_relaxed), 2);
    EXPECT_EQ(owning_plugin_destructions.load(std::memory_order_relaxed), 2);
}

TEST(InstanceRuntimeTest, FailedCreationReleasesPluginOwnedState) {
    reset_owning_plugin_counters(true);
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("owning", &create_owning_plugin);

    EXPECT_TRUE(runtime.create_instance("failed", "room", "owning", {}).empty());
    EXPECT_EQ(runtime.instance_count(), 0);
    EXPECT_EQ(owning_plugin_allocations.load(std::memory_order_relaxed), 1);
    EXPECT_EQ(owning_plugin_destructions.load(std::memory_order_relaxed), 1);
    owning_plugin_throw_on_create.store(false, std::memory_order_relaxed);
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

TEST(InstanceRuntimeTest, ConcurrentDuplicateCreationKeepsSingleInstance) {
    reset_owning_plugin_counters();
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("owning", &create_owning_plugin);

    std::barrier start(3);
    std::string first_result;
    std::string second_result;
    std::thread first([&] {
        start.arrive_and_wait();
        first_result = runtime.create_instance("same", "room", "owning", {});
    });
    std::thread second([&] {
        start.arrive_and_wait();
        second_result = runtime.create_instance("same", "room", "owning", {});
    });
    start.arrive_and_wait();
    first.join();
    second.join();

    EXPECT_EQ(static_cast<int>(!first_result.empty()) +
                  static_cast<int>(!second_result.empty()),
              1);
    EXPECT_EQ(runtime.instance_count(), 1);
    const auto allocations =
        owning_plugin_allocations.load(std::memory_order_relaxed);
    EXPECT_GE(allocations, 1);
    EXPECT_LE(allocations, 2);
    EXPECT_EQ(owning_plugin_destructions.load(std::memory_order_relaxed),
              allocations - 1);
}

TEST(InstanceRuntimeTest, NullPluginFactoryFailsWithoutCreatingInstance) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("null", nullptr);

    EXPECT_TRUE(runtime.create_instance("inst", "room", "null", {}).empty());
    EXPECT_EQ(runtime.instance_count(), 0);
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

TEST(InstanceRuntimeTest, EventCallbacksMayReenterRuntimeQueries) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);
    std::atomic<int> callbacks{0};
    ASSERT_TRUE(runtime.set_event_callback([&](const v2::realtime::InstanceEvent& event) {
        EXPECT_TRUE(runtime.contains_instance(event.instance_id));
        EXPECT_FALSE(runtime.list_instances().empty());
        (void)runtime.get_instance_state(event.instance_id);
        callbacks.fetch_add(1, std::memory_order_relaxed);
    }));

    v2::realtime::PlayerContext player;
    player.user_id = "alice";
    ASSERT_FALSE(runtime.create_instance("inst", "room", "echo", {player}).empty());
    (void)runtime.tick_instance("inst", 1, 1);
    runtime.finish_instance("inst");

    EXPECT_EQ(callbacks.load(std::memory_order_relaxed), 3);
}

TEST(InstanceRuntimeTest, EventCallbacksMayMutatePlayerLifecycle) {
    player_lifecycle_state = std::make_shared<PlayerLifecycleState>();
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("lifecycle", &create_player_lifecycle_plugin);
    v2::realtime::PlayerContext alice;
    alice.user_id = "alice";
    v2::realtime::PlayerContext bob;
    bob.user_id = "bob";

    std::atomic<bool> attached{false};
    std::atomic<bool> detached{false};
    ASSERT_TRUE(runtime.set_event_callback([&](const v2::realtime::InstanceEvent& event) {
        if (event.type == v2::realtime::InstanceEvent::Type::kInstanceCreated) {
            attached.store(
                runtime.attach_player(event.instance_id, bob).applied,
                std::memory_order_relaxed);
        } else if (event.type ==
                   v2::realtime::InstanceEvent::Type::kSnapshotAvailable) {
            detached.store(
                runtime.detach_player(event.instance_id, "bob").applied,
                std::memory_order_relaxed);
        }
    }));

    ASSERT_FALSE(runtime.create_instance("inst", "room", "lifecycle", {alice}).empty());
    EXPECT_TRUE(attached.load(std::memory_order_relaxed));
    EXPECT_EQ(runtime.list_instances().front().player_count, 2U);
    (void)runtime.tick_instance("inst", 1, 1);
    EXPECT_TRUE(detached.load(std::memory_order_relaxed));
    EXPECT_EQ(runtime.list_instances().front().player_count, 1U);
    player_lifecycle_state.reset();
}

TEST(InstanceRuntimeTest, ThrowingEventCallbackIsIsolated) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);
    ASSERT_TRUE(runtime.set_event_callback([](const v2::realtime::InstanceEvent&) {
        throw std::runtime_error("callback failure");
    }));

    v2::realtime::PlayerContext player;
    player.user_id = "alice";
    EXPECT_EQ(runtime.create_instance("inst", "room", "echo", {player}), "inst");
    const auto stats = runtime.tick_instance("inst", 1, 1);
    EXPECT_EQ(stats.frame_number, 1U);
    EXPECT_EQ(runtime.get_instance_state("inst"),
              v2::realtime::InstanceState::kRunning);
}

TEST(InstanceRuntimeTest, EventCallbackConfigurationFreezesAfterFirstInstance) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("echo", &create_echo_plugin);
    std::atomic<int> callbacks{0};
    ASSERT_TRUE(runtime.set_event_callback(
        [&](const v2::realtime::InstanceEvent&) {
            callbacks.fetch_add(1, std::memory_order_relaxed);
        }));
    v2::realtime::PlayerContext player;
    player.user_id = "alice";
    ASSERT_FALSE(runtime.create_instance("inst", "room", "echo", {player}).empty());
    callbacks.store(0, std::memory_order_relaxed);

    std::atomic<int> rejected_updates{0};
    std::barrier start(3);
    std::thread setter([&] {
        start.arrive_and_wait();
        for (int i = 0; i < 1000; ++i) {
            if (!runtime.set_event_callback({})) {
                rejected_updates.fetch_add(1, std::memory_order_relaxed);
            }
        }
    });
    std::thread ticker([&] {
        start.arrive_and_wait();
        for (std::uint32_t frame = 1; frame <= 1000; ++frame) {
            (void)runtime.tick_instance("inst", frame, frame);
        }
    });
    start.arrive_and_wait();
    setter.join();
    ticker.join();

    EXPECT_EQ(rejected_updates.load(std::memory_order_relaxed), 1000);
    EXPECT_EQ(callbacks.load(std::memory_order_relaxed), 1000);
    EXPECT_EQ(runtime.get_instance_state("inst"),
              v2::realtime::InstanceState::kRunning);
}

TEST(InstanceRuntimeTest, AttachAndDetachPlayerRunHooksAndEmitEvents) {
    player_lifecycle_state = std::make_shared<PlayerLifecycleState>();
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("lifecycle", &create_player_lifecycle_plugin);
    v2::realtime::PlayerContext alice;
    alice.user_id = "alice";

    std::vector<v2::realtime::InstanceEvent::Type> events;
    ASSERT_TRUE(runtime.set_event_callback([&](const v2::realtime::InstanceEvent& event) {
        if (event.type == v2::realtime::InstanceEvent::Type::kPlayerJoined ||
            event.type == v2::realtime::InstanceEvent::Type::kPlayerLeft) {
            events.push_back(event.type);
        }
        EXPECT_TRUE(runtime.contains_instance(event.instance_id));
    }));
    ASSERT_FALSE(runtime.create_instance("inst", "room", "lifecycle", {alice}).empty());
    v2::realtime::PlayerContext bob;
    bob.user_id = "bob";
    bob.display_name = "Bob";

    EXPECT_TRUE(runtime.attach_player("inst", bob).applied);
    EXPECT_EQ(runtime.list_instances().front().player_count, 2U);
    EXPECT_EQ(runtime.get_resume_snapshot("inst", "bob").payload, "bob");
    EXPECT_TRUE(runtime.detach_player("inst", "bob").applied);
    EXPECT_EQ(runtime.list_instances().front().player_count, 1U);
    EXPECT_TRUE(runtime.get_resume_snapshot("inst", "bob").payload.empty());

    EXPECT_EQ(player_lifecycle_state->joins.load(std::memory_order_relaxed), 1);
    EXPECT_EQ(player_lifecycle_state->leaves.load(std::memory_order_relaxed), 1);
    EXPECT_TRUE(player_lifecycle_state->join_saw_player_absent.load(
        std::memory_order_relaxed));
    EXPECT_TRUE(player_lifecycle_state->leave_saw_player_present.load(
        std::memory_order_relaxed));
    ASSERT_EQ(events.size(), 2U);
    EXPECT_EQ(events[0], v2::realtime::InstanceEvent::Type::kPlayerJoined);
    EXPECT_EQ(events[1], v2::realtime::InstanceEvent::Type::kPlayerLeft);
    player_lifecycle_state.reset();
}

TEST(InstanceRuntimeTest, PlayerLifecycleRejectsInvalidTransitions) {
    player_lifecycle_state = std::make_shared<PlayerLifecycleState>();
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("lifecycle", &create_player_lifecycle_plugin);
    v2::realtime::PlayerContext alice;
    alice.user_id = "alice";
    ASSERT_FALSE(runtime.create_instance("inst", "room", "lifecycle", {alice}).empty());

    EXPECT_EQ(runtime.attach_player("missing", alice).reject_reason,
              "instance_not_found");
    EXPECT_EQ(runtime.attach_player("inst", {}).reject_reason, "empty_user_id");
    EXPECT_EQ(runtime.attach_player("inst", alice).reject_reason,
              "player_already_attached");
    EXPECT_EQ(runtime.detach_player("inst", "bob").reject_reason,
              "player_not_attached");

    runtime.finish_instance("inst");
    v2::realtime::PlayerContext bob;
    bob.user_id = "bob";
    EXPECT_EQ(runtime.attach_player("inst", bob).reject_reason,
              "instance_not_active");
    EXPECT_EQ(runtime.detach_player("inst", "alice").reject_reason,
              "instance_not_active");
    player_lifecycle_state.reset();
}

TEST(InstanceRuntimeTest, PlayerLifecycleRemainsAppliedWhenPluginHookThrows) {
    player_lifecycle_state = std::make_shared<PlayerLifecycleState>();
    player_lifecycle_state->throw_on_join.store(true, std::memory_order_relaxed);
    player_lifecycle_state->throw_on_leave.store(true, std::memory_order_relaxed);
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("lifecycle", &create_player_lifecycle_plugin);
    ASSERT_FALSE(runtime.create_instance("inst", "room", "lifecycle", {}).empty());

    v2::realtime::PlayerContext player;
    player.user_id = "alice";
    EXPECT_TRUE(runtime.attach_player("inst", player).applied);
    EXPECT_TRUE(runtime.detach_player("inst", "alice").applied);
    EXPECT_EQ(runtime.list_instances().front().player_count, 0U);
    player_lifecycle_state.reset();
}

TEST(InstanceRuntimeTest, DetachDropsOnlyThatPlayersQueuedInputs) {
    player_lifecycle_state = std::make_shared<PlayerLifecycleState>();
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("lifecycle", &create_player_lifecycle_plugin);
    v2::realtime::PlayerContext alice;
    alice.user_id = "alice";
    v2::realtime::PlayerContext bob;
    bob.user_id = "bob";
    ASSERT_FALSE(runtime.create_instance(
        "inst", "room", "lifecycle", {alice, bob}).empty());

    v2::realtime::InputEnvelope input;
    input.instance_id = "inst";
    input.user_id = "alice";
    input.seq = 1;
    ASSERT_TRUE(runtime.submit_input(input).accepted);
    input.user_id = "bob";
    ASSERT_TRUE(runtime.submit_input(input).accepted);
    ASSERT_TRUE(runtime.detach_player("inst", "alice").applied);

    const auto stats = runtime.tick_instance("inst", 1, 1);
    EXPECT_EQ(stats.inputs_processed, 1U);
    player_lifecycle_state.reset();
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

TEST(InstanceRuntimeTest, TicksDifferentInstancesConcurrently) {
    const auto state = std::make_shared<ConcurrentTickState>();
    concurrent_tick_state = state;
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("concurrent", &create_concurrent_tick_plugin);

    v2::realtime::PlayerContext player;
    player.user_id = "alice";
    ASSERT_FALSE(runtime.create_instance("a", "room_a", "concurrent", {player}).empty());
    ASSERT_FALSE(runtime.create_instance("b", "room_b", "concurrent", {player}).empty());

    const auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    std::barrier start(3);
    std::thread first([&] {
        start.arrive_and_wait();
        runtime.tick_instance("a", 1, now);
    });
    std::thread second([&] {
        start.arrive_and_wait();
        runtime.tick_instance("b", 1, now);
    });
    start.arrive_and_wait();
    first.join();
    second.join();

    EXPECT_EQ(state->entered.load(std::memory_order_acquire), 2);
    EXPECT_EQ(state->peer_observations.load(std::memory_order_acquire), 2);
    concurrent_tick_state.reset();
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
