// v2.5.0 T7: Disconnect and reconnect integration tests
#include <gtest/gtest.h>
#include "v2/player/player_actor.h"
#include "v2/room/room_actor.h"
#include "v2/battle/battle_actor.h"
#include "v2/runtime/actor_system.h"
#include <memory>

// ─── Player disconnect → suspend → reconnect ───────────────────────────

TEST(ReconnectTest, SessionCloseWithRoomEntersSuspended) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::player::PlayerEventSink {
        void push(v2::player::PlayerEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::player::PlayerEvent> events;
    };
    Sink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* ptr = actor.get();
    auto ref = actor_system.create_actor(std::move(actor));

    auto tell = [&](auto payload) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::move(payload);
        ref.tell(std::move(msg));
    };
    tell(v2::player::BindSessionMsg{.session_id=100, .connection_id=900});
    tell(v2::player::LoginRequestMsg{.session_id=100, .user_id="r1", .token="t", .display_name="R"});
    tell(v2::player::RoomAssignedMsg{.room_actor_id=42, .room_id="room_x"});
    tell(v2::player::SessionClosedMsg{.session_id=100});

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    EXPECT_EQ(ptr->state().lifecycle, v2::player::PlayerLifecycleState::kSuspended);
    EXPECT_FALSE(ptr->state().binding.has_value());
    EXPECT_TRUE(ptr->state().room_id.has_value());
}

TEST(ReconnectTest, SessionCloseWithoutRoomGoesOffline) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::player::PlayerEventSink {
        void push(v2::player::PlayerEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::player::PlayerEvent> events;
    };
    Sink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* ptr = actor.get();
    auto ref = actor_system.create_actor(std::move(actor));

    auto tell = [&](auto payload) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::move(payload);
        ref.tell(std::move(msg));
    };
    tell(v2::player::BindSessionMsg{.session_id=100, .connection_id=900});
    tell(v2::player::LoginRequestMsg{.session_id=100, .user_id="r2", .token="t", .display_name="R"});
    tell(v2::player::SessionClosedMsg{.session_id=100});

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    EXPECT_EQ(ptr->state().lifecycle, v2::player::PlayerLifecycleState::kOffline);
}

TEST(ReconnectTest, ReconnectWithinWindowRestoresRoomState) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::player::PlayerEventSink {
        void push(v2::player::PlayerEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::player::PlayerEvent> events;
    };
    Sink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* ptr = actor.get();
    auto ref = actor_system.create_actor(std::move(actor));

    auto tell = [&](auto payload) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::move(payload);
        ref.tell(std::move(msg));
    };
    // Initial login + enter room
    tell(v2::player::BindSessionMsg{.session_id=100, .connection_id=900});
    tell(v2::player::LoginRequestMsg{.session_id=100, .user_id="r3", .token="t", .display_name="R"});
    tell(v2::player::RoomAssignedMsg{.room_actor_id=42, .room_id="room_y"});
    actor_system.dispatch_all();
    EXPECT_EQ(ptr->state().lifecycle, v2::player::PlayerLifecycleState::kInRoom);

    // Disconnect → suspended
    tell(v2::player::SessionClosedMsg{.session_id=100});
    actor_system.dispatch_all();
    EXPECT_EQ(ptr->state().lifecycle, v2::player::PlayerLifecycleState::kSuspended);

    // Reconnect within window
    tell(v2::player::BindSessionMsg{.session_id=200, .connection_id=901});
    tell(v2::player::LoginRequestMsg{.session_id=200, .user_id="r3", .token="t", .display_name="R"});
    actor_system.dispatch_all();

    EXPECT_EQ(ptr->state().lifecycle, v2::player::PlayerLifecycleState::kInRoom);
    EXPECT_TRUE(ptr->state().binding.has_value());
    EXPECT_EQ(ptr->state().binding->session_id, 200U);
    EXPECT_TRUE(ptr->state().room_id.has_value());
    EXPECT_EQ(*ptr->state().room_id, "room_y");
}

// ─── Battle disconnect grace period ─────────────────────────────────────

TEST(ReconnectTest, BattleDisconnectDoesNotImmediatelyFinish) {
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
        .battle_id = "dc", .room_id = "r", .player_ids = {"a","b"}};
    ref.tell(std::move(create));
    actor_system.dispatch_all();

    // Disconnect player "a"
    v2::actor::Message dc;
    dc.header.kind = v2::actor::MessageKind::kUser;
    dc.payload = v2::battle::PlayerDisconnectedMsg{.user_id = "a"};
    ref.tell(std::move(dc));
    actor_system.dispatch_all();

    // Battle should STILL be running (grace period, not immediate finish)
    EXPECT_EQ(ptr->state().lifecycle, v2::battle::BattleLifecycleState::kRunning);
    // Only Created event emitted (no finish events)
    bool has_finished = false;
    for (auto& e : sink.events) {
        if (std::holds_alternative<v2::battle::BattleFinishedMsg>(e)) has_finished = true;
    }
    EXPECT_FALSE(has_finished);
}

TEST(ReconnectTest, BattleReconnectWithinGracePeriodRestoresOnline) {
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
        .battle_id = "dc2", .room_id = "r", .player_ids = {"a","b"}};
    ref.tell(std::move(create));

    // Disconnect
    v2::actor::Message dc;
    dc.header.kind = v2::actor::MessageKind::kUser;
    dc.payload = v2::battle::PlayerDisconnectedMsg{.user_id = "a"};
    ref.tell(std::move(dc));

    // Reconnect within grace
    v2::actor::Message rc;
    rc.header.kind = v2::actor::MessageKind::kUser;
    rc.payload = v2::battle::PlayerReconnectedMsg{.user_id = "a"};
    ref.tell(std::move(rc));

    actor_system.dispatch_all();
    EXPECT_EQ(ptr->state().lifecycle, v2::battle::BattleLifecycleState::kRunning);
    EXPECT_EQ(sink.events.size(), 1U);  // only Created
}
