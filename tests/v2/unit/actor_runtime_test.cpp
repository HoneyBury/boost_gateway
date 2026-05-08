#include <gtest/gtest.h>

#include <memory>
#include <string>
#include <utility>
#include <vector>

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
