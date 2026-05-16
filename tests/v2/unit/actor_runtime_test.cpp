#include <gtest/gtest.h>

#include <chrono>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "v2/io/io_engine.h"
#include "v2/runtime/actor_system.h"

namespace {

class LifecycleProbeActor final : public v2::actor::Actor {
public:
    LifecycleProbeActor(bool& started, bool& stopped)
        : started_(started), stopped_(stopped) {}

    void on_start() override { started_ = true; }
    void on_stop() override { stopped_ = true; }
    void on_message(v2::actor::Message&&) override {}

private:
    bool& started_;
    bool& stopped_;
};

class RecordingActor final : public v2::actor::Actor {
public:
    explicit RecordingActor(std::vector<std::string>& received)
        : received_(received) {}

    void on_message(v2::actor::Message&& message) override {
        if (const auto* text = std::get_if<std::string>(&message.payload)) {
            received_.push_back(*text);
        }
    }

private:
    std::vector<std::string>& received_;
};

class ForwardingActor final : public v2::actor::Actor {
public:
    explicit ForwardingActor(v2::actor::ActorRef target)
        : target_(target) {}

    void on_message(v2::actor::Message&& message) override {
        tell(target_, std::move(message));
    }

private:
    v2::actor::ActorRef target_;
};

class DelayedForwardingActor final : public v2::actor::Actor {
public:
    explicit DelayedForwardingActor(v2::actor::ActorRef target)
        : target_(target) {}

    void on_message(v2::actor::Message&& message) override {
        tell_after(target_, std::move(message), 1);
    }

private:
    v2::actor::ActorRef target_;
};

class TimedForwardingActor final : public v2::actor::Actor {
public:
    explicit TimedForwardingActor(v2::actor::ActorRef target)
        : target_(target) {}

    void on_message(v2::actor::Message&& message) override {
        tell_after(target_, std::move(message), std::chrono::milliseconds(20));
    }

private:
    v2::actor::ActorRef target_;
};

class CountingActor final : public v2::actor::Actor {
public:
    void on_message(v2::actor::Message&&) override { ++count_; }
    int count() const noexcept { return count_; }

private:
    int count_ = 0;
};

class SelfSchedulingActor final : public v2::actor::Actor {
public:
    void on_message(v2::actor::Message&&) override {
        ++count_;
        if (count_ == 1) {
            v2::actor::Message msg;
            msg.header.kind = v2::actor::MessageKind::kUser;
            msg.payload = std::string("tick");
            handle_ = schedule_owned(self(), std::move(msg), std::chrono::milliseconds(10));
        }
    }
    int count() const noexcept { return count_; }

private:
    int count_ = 0;
    v2::runtime::ScheduleHandle handle_;
};

class InspectingIoEngine final : public v2::io::IoEngine {
public:
    [[nodiscard]] std::uint32_t num_io_cores() const noexcept override { return 2; }
    void dispatch_to_core(std::uint32_t, std::function<void()>) override {}
    void dispatch_to_all_cores(std::function<void(std::uint32_t)>) override {}
    [[nodiscard]] std::optional<std::uint32_t> current_core_id() const noexcept override { return current_core_id_; }
    std::unique_ptr<v2::io::IoAcceptor> listen(const char*, std::uint16_t, net::SessionOptions, v2::io::IoListenOptions) override {
        return {};
    }
    void run() override {}
    void stop() override {}
    void register_session(std::uint32_t) override {}
    void unregister_session(std::uint32_t) override {}
    [[nodiscard]] std::uint32_t session_count(std::uint32_t) const noexcept override { return 0; }
    [[nodiscard]] std::uint32_t total_session_count() const noexcept override { return 0; }
    bool post_mailbox(std::uint32_t, v2::actor::Message) override { return true; }
    [[nodiscard]] std::vector<v2::actor::Message> drain_mailbox(std::uint32_t) override { return {}; }
    void set_actor_system(v2::runtime::ActorSystem*) override {}

    std::optional<std::uint32_t> current_core_id_;
};

}  // namespace

TEST(V2ActorRuntimeTest, CreateActorStartsAndShutdownStops) {
    bool started = false;
    bool stopped = false;

    {
        v2::runtime::ActorSystem actor_system;
        auto actor = std::make_unique<LifecycleProbeActor>(started, stopped);
        auto actor_ref = actor_system.create_actor(std::move(actor));
        EXPECT_TRUE(actor_ref.is_valid());
        EXPECT_TRUE(started);
        EXPECT_FALSE(stopped);
    }

    EXPECT_TRUE(stopped);
}

TEST(V2ActorRuntimeTest, DispatchAllDeliversMessagesBetweenActors) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(
        std::make_unique<RecordingActor>(received));
    auto forwarder = actor_system.create_actor(
        std::make_unique<ForwardingActor>(receiver));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::string("hello-v2");
    forwarder.tell(std::move(message));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "hello-v2");
}

TEST(V2ActorRuntimeTest, DispatchAllPromotesDelayedMessagesOnLaterRounds) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));
    auto delayed = actor_system.create_actor(std::make_unique<DelayedForwardingActor>(receiver));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::string("delayed-v2");
    delayed.tell(std::move(message));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_TRUE(received.empty());
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "delayed-v2");
}

TEST(V2ActorRuntimeTest, DispatchAllPromotesWallClockDelayedMessagesWhenDue) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));
    auto delayed = actor_system.create_actor(std::make_unique<TimedForwardingActor>(receiver));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::string("timed-v2");
    delayed.tell(std::move(message));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_TRUE(received.empty());

    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "timed-v2");
}

TEST(V2ActorRuntimeTest, CancelSchedulePreventsWallClockDelivery) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("cancelled-v2");

    const auto schedule_id = actor_system.schedule_after(std::move(message), std::chrono::milliseconds(20));
    ASSERT_NE(schedule_id, 0U);
    EXPECT_TRUE(actor_system.cancel_schedule(schedule_id));

    std::this_thread::sleep_for(std::chrono::milliseconds(30));
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_TRUE(received.empty());
}

TEST(V2ActorRuntimeTest, RepeatingScheduleDeliversUntilCancelled) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("repeat-v2");

    const auto schedule_id = actor_system.schedule_every(
        std::move(message), std::chrono::milliseconds(10), std::chrono::milliseconds(10));
    ASSERT_NE(schedule_id, 0U);

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 2U);
    EXPECT_EQ(received[0], "repeat-v2");
    EXPECT_EQ(received[1], "repeat-v2");

    EXPECT_TRUE(actor_system.cancel_schedule(schedule_id));
    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    ASSERT_EQ(received.size(), 2U);
}

TEST(V2ActorRuntimeTest, ScheduleAtAbsoluteTimePointDeliversWhenDue) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("at-time-v2");

    const auto ready_at = v2::runtime::ActorSystem::Clock::now() + std::chrono::milliseconds(10);
    const auto schedule_id = actor_system.schedule_after(std::move(message), ready_at);
    ASSERT_NE(schedule_id, 0U);

    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "at-time-v2");
}

TEST(V2ActorRuntimeTest, ScheduleEveryWithMaxRepetitionsStopsAfterLimit) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("max-rep");

    const auto schedule_id = actor_system.schedule_every(
        std::move(message), std::chrono::milliseconds(10), std::chrono::milliseconds(10), 2);
    ASSERT_NE(schedule_id, 0U);

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 2U);

    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    ASSERT_EQ(received.size(), 2U);
}

TEST(V2ActorRuntimeTest, ScheduleHandleCancelsOnDestruction) {
    v2::runtime::ActorSystem actor_system;
    auto actor = actor_system.create_actor(std::make_unique<CountingActor>());

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = actor.actor_id();
    message.payload = std::string("raii");

    {
        const auto schedule_id = actor_system.schedule_every(
            std::move(message), std::chrono::milliseconds(5), std::chrono::milliseconds(5));
        v2::runtime::ScheduleHandle handle(&actor_system, schedule_id);
        ASSERT_TRUE(handle);
        EXPECT_EQ(handle.id(), schedule_id);
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
}

TEST(V2ActorRuntimeTest, ScheduleHandleReleaseDisarmsAutoCancel) {
    v2::runtime::ActorSystem actor_system;
    auto actor = actor_system.create_actor(std::make_unique<CountingActor>());

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = actor.actor_id();
    message.payload = std::string("released");

    const auto schedule_id = actor_system.schedule_after(
        std::move(message), std::chrono::milliseconds(10));
    ASSERT_NE(schedule_id, 0U);

    {
        v2::runtime::ScheduleHandle handle(&actor_system, schedule_id);
        ASSERT_TRUE(handle);
        EXPECT_TRUE(handle.release());
        EXPECT_FALSE(handle);
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
}

TEST(V2ActorRuntimeTest, ScheduleHandleMoveSemanticsPreserveCancellation) {
    v2::runtime::ActorSystem actor_system;
    auto actor = actor_system.create_actor(std::make_unique<CountingActor>());

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = actor.actor_id();
    message.payload = std::string("moved");

    const auto schedule_id = actor_system.schedule_every(
        std::move(message), std::chrono::milliseconds(5), std::chrono::milliseconds(5));
    ASSERT_NE(schedule_id, 0U);

    v2::runtime::ScheduleHandle handle1(&actor_system, schedule_id);
    auto handle2 = std::move(handle1);
    EXPECT_FALSE(handle1);
    EXPECT_TRUE(handle2);
}

TEST(V2ActorRuntimeTest, SelfSchedulingActorOwnsRepeatLifecycle) {
    v2::runtime::ActorSystem actor_system;
    auto actor = actor_system.create_actor(std::make_unique<SelfSchedulingActor>());

    v2::actor::Message msg;
    msg.header.kind = v2::actor::MessageKind::kUser;
    msg.payload = std::string("kickoff");
    actor.tell(std::move(msg));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
}

TEST(V2ActorRuntimeTest, SendAfterZeroDispatchDelaySendsImmediately) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("immediate");

    actor_system.send_after(std::move(message), std::size_t{0});

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "immediate");
}

TEST(V2ActorRuntimeTest, SendAfterZeroWallClockDurationSendsImmediately) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("immediate-wall");

    actor_system.send_after(std::move(message), v2::runtime::ActorSystem::Duration::zero());

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "immediate-wall");
}

TEST(V2ActorRuntimeTest, ScheduleAfterZeroDurationReturnsZeroAndSendsImmediately) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("zero-sched");

    const auto schedule_id = actor_system.schedule_after(
        std::move(message), v2::runtime::ActorSystem::Duration::zero());
    EXPECT_EQ(schedule_id, 0U);
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "zero-sched");
}

TEST(V2ActorRuntimeTest, ScheduleAtNowTimePointSendsImmediately) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("at-now");

    const auto schedule_id = actor_system.schedule_after(
        std::move(message), v2::runtime::ActorSystem::Clock::now());
    EXPECT_EQ(schedule_id, 0U);
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "at-now");
}

TEST(V2ActorRuntimeTest, ScheduleEveryWithZeroInitialDelayUsesInterval) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("zero-init");

    const auto schedule_id = actor_system.schedule_every(
        std::move(message), v2::runtime::ActorSystem::Duration::zero(), std::chrono::milliseconds(10));
    ASSERT_NE(schedule_id, 0U);

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_GE(received.size(), 1U);

    EXPECT_TRUE(actor_system.cancel_schedule(schedule_id));
}

TEST(V2ActorRuntimeTest, ScheduleEveryWithZeroIntervalReturnsZero) {
    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<CountingActor>());

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("zero-int");

    const auto schedule_id = actor_system.schedule_every(
        std::move(message), std::chrono::milliseconds(10), v2::runtime::ActorSystem::Duration::zero());
    EXPECT_EQ(schedule_id, 0U);
}

TEST(V2ActorRuntimeTest, ScheduleEveryWithZeroMaxRepetitionsReturnsZero) {
    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<CountingActor>());

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("zero-rep");

    const auto schedule_id = actor_system.schedule_every(
        std::move(message), std::chrono::milliseconds(10), std::chrono::milliseconds(10), 0);
    EXPECT_EQ(schedule_id, 0U);
}

TEST(V2ActorRuntimeTest, CancelScheduleOnNonExistentIdReturnsFalse) {
    v2::runtime::ActorSystem actor_system;
    EXPECT_FALSE(actor_system.cancel_schedule(99999));
}

TEST(V2ActorRuntimeTest, CancelScheduleTwiceSecondCallReturnsFalse) {
    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<CountingActor>());

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("double-cancel");

    const auto schedule_id = actor_system.schedule_after(
        std::move(message), std::chrono::milliseconds(500));
    ASSERT_NE(schedule_id, 0U);
    EXPECT_TRUE(actor_system.cancel_schedule(schedule_id));
    EXPECT_FALSE(actor_system.cancel_schedule(schedule_id));
}

TEST(V2ActorRuntimeTest, DispatchRoundMessagesUnaffectedByCancelSchedule) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("round-msg");

    actor_system.send_after(std::move(message), std::size_t{1});

    EXPECT_FALSE(actor_system.cancel_schedule(1));

    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(received.size(), 1U);
    EXPECT_EQ(received.front(), "round-msg");
}

TEST(V2ActorRuntimeTest, ShutdownClearsPendingWallClockSchedules) {
    std::vector<std::string> received;
    v2::runtime::ActorSystem actor_system;
    auto receiver = actor_system.create_actor(std::make_unique<RecordingActor>(received));

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = receiver.actor_id();
    message.payload = std::string("shutdown-clear");

    const auto schedule_id = actor_system.schedule_after(
        std::move(message), std::chrono::milliseconds(10));
    ASSERT_NE(schedule_id, 0U);

    actor_system.shutdown();
    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_TRUE(received.empty());
}

TEST(V2ActorRuntimeTest, DispatchAllAfterShutdownReturnsZero) {
    v2::runtime::ActorSystem actor_system;
    actor_system.shutdown();
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
}

TEST(V2ActorRuntimeTest, MultipleActorsOneCancelledOtherDelivers) {
    std::vector<std::string> received_a;
    std::vector<std::string> received_b;

    v2::runtime::ActorSystem actor_system;
    auto actor_a = actor_system.create_actor(std::make_unique<RecordingActor>(received_a));
    auto actor_b = actor_system.create_actor(std::make_unique<RecordingActor>(received_b));

    v2::actor::Message msg_a;
    msg_a.header.kind = v2::actor::MessageKind::kUser;
    msg_a.header.target_actor = actor_a.actor_id();
    msg_a.payload = std::string("msg-a");

    v2::actor::Message msg_b;
    msg_b.header.kind = v2::actor::MessageKind::kUser;
    msg_b.header.target_actor = actor_b.actor_id();
    msg_b.payload = std::string("msg-b");

    const auto id_a = actor_system.schedule_after(std::move(msg_a), std::chrono::milliseconds(10));
    const auto id_b = actor_system.schedule_after(std::move(msg_b), std::chrono::milliseconds(10));
    ASSERT_NE(id_a, 0U);
    ASSERT_NE(id_b, 0U);

    EXPECT_TRUE(actor_system.cancel_schedule(id_a));

    std::this_thread::sleep_for(std::chrono::milliseconds(15));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_TRUE(received_a.empty());
    ASSERT_EQ(received_b.size(), 1U);
    EXPECT_EQ(received_b.front(), "msg-b");
}

TEST(V2ActorRuntimeTest, DispatchOwnerCoreReflectsCurrentIoCoreDuringDispatch) {
    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    io_engine.current_core_id_ = 1U;
    actor_system.set_io_engine(&io_engine);

    std::vector<std::string> observed;
    class OwnerInspectingActor final : public v2::actor::Actor {
    public:
        OwnerInspectingActor(v2::runtime::ActorSystem& system, std::vector<std::string>& observed)
            : system_(system), observed_(observed) {}

        void on_message(v2::actor::Message&&) override {
            const auto core = system_.dispatch_owner_core();
            observed_.push_back(core.has_value() ? std::to_string(*core) : "none");
        }

    private:
        v2::runtime::ActorSystem& system_;
        std::vector<std::string>& observed_;
    };

    auto actor = actor_system.create_actor(std::make_unique<OwnerInspectingActor>(actor_system, observed));
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::string("owner-core");
    actor.tell(std::move(message));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    ASSERT_EQ(observed.size(), 1U);
    EXPECT_EQ(observed.front(), "1");
    EXPECT_EQ(actor_system.dispatch_owner_core(), std::nullopt);
}
