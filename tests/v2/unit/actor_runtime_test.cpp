#include <gtest/gtest.h>

#include <chrono>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <utility>
#include <vector>

#include "v2/io/io_engine.h"
#include "v2/io/mailbox.h"
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

class SequencingActor final : public v2::actor::Actor {
public:
    SequencingActor(std::string name, std::vector<std::string>& sequence)
        : name_(std::move(name)), sequence_(sequence) {}

    void on_message(v2::actor::Message&& message) override {
        if (const auto* text = std::get_if<std::string>(&message.payload)) {
            sequence_.push_back(name_ + ":" + *text);
        }
    }

private:
    std::string name_;
    std::vector<std::string>& sequence_;
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
    bool post_mailbox(std::uint32_t core_id, v2::actor::Message message) override {
        if (core_id >= mailboxes_.size()) {
            return false;
        }
        mailboxes_[core_id].push_back(std::move(message));
        return true;
    }
    [[nodiscard]] std::vector<v2::actor::Message> drain_mailbox(std::uint32_t core_id) override {
        if (core_id >= mailboxes_.size()) {
            return {};
        }
        auto drained = std::move(mailboxes_[core_id]);
        mailboxes_[core_id].clear();
        return drained;
    }
    void set_actor_system(v2::runtime::ActorSystem*) override {}

    std::optional<std::uint32_t> current_core_id_;
    std::vector<std::vector<v2::actor::Message>> mailboxes_{3};
};

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

class ShutdownOnMessageActor final : public v2::actor::Actor {
public:
    explicit ShutdownOnMessageActor(v2::runtime::ActorSystem& system)
        : system_(system) {}

    void on_message(v2::actor::Message&&) override { system_.shutdown(); }

private:
    v2::runtime::ActorSystem& system_;
};

class ExternalCountingActor final : public v2::actor::Actor {
public:
    explicit ExternalCountingActor(std::size_t& count)
        : count_(count) {}

    void on_message(v2::actor::Message&&) override { ++count_; }

private:
    std::size_t& count_;
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

TEST(V2ActorRuntimeTest, DrainMailboxDispatchUsesDrainedCoreAsOwner) {
    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    io_engine.current_core_id_ = 0U;
    actor_system.set_io_engine(&io_engine);

    std::vector<std::string> observed;
    auto actor = actor_system.create_actor(
        std::make_unique<OwnerInspectingActor>(actor_system, observed),
        {},
        1U);

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::string("cross-core-owner");
    actor.tell(std::move(message));

    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_TRUE(io_engine.drain_mailbox(0).empty());

    EXPECT_EQ(actor_system.drain_mailbox_and_dispatch(1), 1U);
    ASSERT_EQ(observed.size(), 1U);
    EXPECT_EQ(observed.front(), "1");
    EXPECT_EQ(actor_system.dispatch_owner_core(), std::nullopt);
}

TEST(V2ActorRuntimeTest, ShutdownDuringDispatchStopsWithoutDeliveringQueuedTail) {
    v2::runtime::ActorSystem actor_system;
    auto shutdown_actor = actor_system.create_actor(
        std::make_unique<ShutdownOnMessageActor>(actor_system));

    v2::actor::Message first;
    first.header.kind = v2::actor::MessageKind::kUser;
    first.payload = std::string("first");
    shutdown_actor.tell(std::move(first));

    v2::actor::Message second;
    second.header.kind = v2::actor::MessageKind::kUser;
    second.payload = std::string("second");
    shutdown_actor.tell(std::move(second));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_EQ(actor_system.dispatch_owner_core(), std::nullopt);
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
}

TEST(V2ActorRuntimeTest, DispatchAllInterleavesReadyActorsFairly) {
    std::vector<std::string> sequence;

    v2::runtime::ActorSystem actor_system;
    auto first = actor_system.create_actor(
        std::make_unique<SequencingActor>("first", sequence));
    auto second = actor_system.create_actor(
        std::make_unique<SequencingActor>("second", sequence));

    for (int i = 0; i < 3; ++i) {
        v2::actor::Message message;
        message.header.kind = v2::actor::MessageKind::kUser;
        message.payload = std::string("first-") + std::to_string(i);
        first.tell(std::move(message));
    }

    v2::actor::Message second_message;
    second_message.header.kind = v2::actor::MessageKind::kUser;
    second_message.payload = std::string("second");
    second.tell(std::move(second_message));

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    ASSERT_EQ(sequence.size(), 4U);
    EXPECT_EQ(sequence[0], "first:first-0");
    EXPECT_EQ(sequence[1], "second:second");
    EXPECT_EQ(sequence[2], "first:first-1");
    EXPECT_EQ(sequence[3], "first:first-2");
}

TEST(V2ActorRuntimeTest, ShutdownDuringFairDispatchStopsOtherReadyActors) {
    std::vector<std::string> received;

    v2::runtime::ActorSystem actor_system;
    auto shutdown_actor = actor_system.create_actor(
        std::make_unique<ShutdownOnMessageActor>(actor_system));
    auto recording_actor = actor_system.create_actor(
        std::make_unique<RecordingActor>(received));

    v2::actor::Message shutdown_message;
    shutdown_message.header.kind = v2::actor::MessageKind::kUser;
    shutdown_message.payload = std::string("shutdown");
    shutdown_actor.tell(std::move(shutdown_message));

    v2::actor::Message recording_message;
    recording_message.header.kind = v2::actor::MessageKind::kUser;
    recording_message.payload = std::string("should-not-deliver");
    recording_actor.tell(std::move(recording_message));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_TRUE(received.empty());
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
}

TEST(V2ActorRuntimeTest, CrossCoreMailboxStressDoesNotDropMessages) {
    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    io_engine.current_core_id_ = 0U;
    actor_system.set_io_engine(&io_engine);

    std::size_t delivered = 0;
    auto actor = actor_system.create_actor(std::make_unique<ExternalCountingActor>(delivered), {}, 1U);

    constexpr std::size_t kMessages = 10000;
    for (std::size_t i = 0; i < kMessages; ++i) {
        v2::actor::Message message;
        message.header.kind = v2::actor::MessageKind::kUser;
        message.payload = std::string("cross-core-stress");
        actor.tell(std::move(message));
    }

    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_EQ(actor_system.drain_mailbox_and_dispatch(1U), kMessages);
    EXPECT_EQ(delivered, kMessages);
    EXPECT_EQ(actor_system.drain_mailbox_and_dispatch(1U), 0U);
}

TEST(V2ActorRuntimeTest, ShutdownDropsQueuedCrossCoreMailboxMessagesSafely) {
    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    io_engine.current_core_id_ = 0U;
    actor_system.set_io_engine(&io_engine);

    std::size_t delivered = 0;
    auto actor = actor_system.create_actor(std::make_unique<ExternalCountingActor>(delivered), {}, 1U);

    for (std::size_t i = 0; i < 128; ++i) {
        v2::actor::Message message;
        message.header.kind = v2::actor::MessageKind::kUser;
        message.payload = std::string("queued-before-shutdown");
        actor.tell(std::move(message));
    }

    actor_system.shutdown();
    EXPECT_EQ(actor_system.drain_mailbox_and_dispatch(1U), 0U);
    EXPECT_EQ(delivered, 0U);
    EXPECT_EQ(actor_system.dispatch_owner_core(), std::nullopt);
}

// ─── R3: Cross-core multi-actor stress ─────────────────────────────────

TEST(V2ActorRuntimeTest, CrossCoreMultiActorStress) {
    constexpr std::size_t kNumActors = 8;
    constexpr std::size_t kNumCores = 3;
    constexpr std::size_t kMessagesPerSender = 2000;

    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    actor_system.set_io_engine(&io_engine);

    std::vector<std::size_t> delivered(kNumActors, 0);
    std::vector<v2::actor::ActorRef> actors;
    actors.reserve(kNumActors);

    for (std::size_t i = 0; i < kNumActors; ++i) {
        auto actor = actor_system.create_actor(
            std::make_unique<ExternalCountingActor>(delivered[i]),
            {},
            static_cast<std::uint32_t>(i % kNumCores));
        ASSERT_TRUE(actor.is_valid());
        actors.push_back(actor);
    }

    // Simulate each core sending to every actor (including same-core targets).
    for (std::uint32_t core = 0; core < kNumCores; ++core) {
        io_engine.current_core_id_ = core;
        for (auto& target : actors) {
            for (std::size_t m = 0; m < kMessagesPerSender; ++m) {
                v2::actor::Message msg;
                msg.header.kind = v2::actor::MessageKind::kUser;
                msg.payload = std::string("stress");
                target.tell(std::move(msg));
            }
        }
    }

    // Drain and dispatch across all cores.
    for (std::uint32_t core = 0; core < kNumCores; ++core) {
        io_engine.current_core_id_ = core;
        actor_system.dispatch_all();
        actor_system.drain_mailbox_and_dispatch(core);
    }

    const std::size_t expected_per_actor = kMessagesPerSender * kNumCores;
    for (std::size_t i = 0; i < kNumActors; ++i) {
        EXPECT_EQ(delivered[i], expected_per_actor)
            << "Actor " << i << " on core " << (i % kNumCores);
    }
}

// ─── R3: Shutdown race tests ──────────────────────────────────────────

TEST(V2ActorRuntimeTest, ShutdownRaceWithPendingSchedules) {
    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    io_engine.current_core_id_ = 0U;
    actor_system.set_io_engine(&io_engine);

    std::size_t delivered = 0;
    auto actor = actor_system.create_actor(
        std::make_unique<ExternalCountingActor>(delivered), {}, 1U);

    // Schedule many wall-clock timers.
    for (int i = 0; i < 100; ++i) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.header.target_actor = actor.actor_id();
        msg.payload = std::string("sched-") + std::to_string(i);
        actor_system.schedule_after(std::move(msg), std::chrono::milliseconds(1));
    }

    // Shutdown immediately — all pending schedules must be cleared without
    // delivering any messages.
    actor_system.shutdown();
    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_EQ(delivered, 0U);
}

TEST(V2ActorRuntimeTest, ShutdownRaceWithConcurrentCreateAndSend) {
    v2::runtime::ActorSystem actor_system;
    InspectingIoEngine io_engine;
    io_engine.current_core_id_ = 0U;
    actor_system.set_io_engine(&io_engine);

    std::size_t delivered = 0;
    auto actor = actor_system.create_actor(
        std::make_unique<ExternalCountingActor>(delivered), {}, 1U);

    // Queue a direct message and a scheduled message.
    {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.header.target_actor = actor.actor_id();
        msg.payload = std::string("direct-race");
        actor_system.send(std::move(msg));
    }
    {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.header.target_actor = actor.actor_id();
        msg.payload = std::string("sched-race");
        actor_system.schedule_after(std::move(msg), std::chrono::milliseconds(5));
    }

    // Shutdown — dispatches after this must be no-ops.
    actor_system.shutdown();

    EXPECT_EQ(actor_system.dispatch_all(), 0U);
    EXPECT_EQ(delivered, 0U);

    // create_actor after shutdown must return invalid ref.
    bool started_after = false;
    bool stopped_after = false;
    auto after_shutdown = actor_system.create_actor(
        std::make_unique<LifecycleProbeActor>(started_after, stopped_after));
    EXPECT_FALSE(after_shutdown.is_valid());
    EXPECT_FALSE(started_after);
}

// ─── R3: SPSC full-queue behavior ─────────────────────────────────────

TEST(V2ActorRuntimeTest, SpscQueueFullBehaviorDropsMessages) {
    v2::io::SpscQueue<v2::actor::Message> queue(4);
    ASSERT_EQ(queue.capacity(), 4U);

    // Fill the queue.
    for (std::size_t i = 0; i < 4; ++i) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::string("msg-") + std::to_string(i);
        EXPECT_TRUE(queue.try_enqueue(std::move(msg)));
    }
    EXPECT_EQ(queue.size(), 4U);

    // Next enqueue fails — queue is full.
    v2::actor::Message overflow;
    overflow.header.kind = v2::actor::MessageKind::kUser;
    overflow.payload = std::string("overflow");
    EXPECT_FALSE(queue.try_enqueue(std::move(overflow)));
    EXPECT_EQ(queue.size(), 4U);

    // Drain — only the original 4 messages are recovered.
    auto drained = queue.drain();
    EXPECT_EQ(drained.size(), 4U);
    for (std::size_t i = 0; i < 4; ++i) {
        const auto* text = std::get_if<std::string>(&drained[i].payload);
        ASSERT_NE(text, nullptr);
        EXPECT_EQ(*text, "msg-" + std::to_string(i));
    }

    // After drain, enqueue works again.  Recreate overflow since the first
    // try_enqueue move-constructed from overflow (then returned false),
    // leaving overflow in a moved-from state.
    v2::actor::Message overflow2;
    overflow2.header.kind = v2::actor::MessageKind::kUser;
    overflow2.payload = std::string("overflow");
    EXPECT_TRUE(queue.try_enqueue(std::move(overflow2)));
    EXPECT_EQ(queue.size(), 1U);

    auto post_drain = queue.drain();
    ASSERT_EQ(post_drain.size(), 1U);
    const auto* text = std::get_if<std::string>(&post_drain[0].payload);
    ASSERT_NE(text, nullptr);
    EXPECT_EQ(*text, "overflow");
}
