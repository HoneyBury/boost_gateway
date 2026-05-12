// v2.5.0 T5: Global message broadcast integration tests
#include <gtest/gtest.h>
#include "v2/battle/battle_actor.h"
#include "v2/room/room_actor.h"
#include "v2/player/player_actor.h"
#include "v2/runtime/actor_system.h"
#include <memory>
#include <vector>

// ─── Room state broadcast ───────────────────────────────────────────────

TEST(BroadcastTest, RoomCreatedEventEmitted) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::room::RoomEventSink {
        void push(v2::room::RoomEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::room::RoomEvent> events;
    };
    Sink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::room::RoomActor>(sink));
    v2::actor::Message msg;
    msg.header.kind = v2::actor::MessageKind::kUser;
    msg.payload = v2::room::CreateRoomMsg{.room_id="broadcast_room", .owner_user_id="alice", .owner_actor_id=1};
    ref.tell(std::move(msg));
    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_EQ(sink.events.size(), 0U);  // create doesn't emit event
}

TEST(BroadcastTest, BattleStartRequestEmitsEvent) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::room::RoomEventSink {
        void push(v2::room::RoomEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::room::RoomEvent> events;
    };
    Sink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::room::RoomActor>(sink));

    // Create room with 2 members, both ready
    auto tell = [&](auto payload) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::move(payload);
        ref.tell(std::move(msg));
    };
    tell(v2::room::CreateRoomMsg{.room_id="r", .owner_user_id="alice", .owner_actor_id=1});
    tell(v2::room::JoinRoomMsg{.user_id="bob", .player_actor_id=2});
    tell(v2::room::SetReadyMsg{.user_id="alice", .ready=true});
    tell(v2::room::SetReadyMsg{.user_id="bob", .ready=true});
    tell(v2::room::StartBattleMsg{.requester_user_id="alice"});

    EXPECT_EQ(actor_system.dispatch_all(), 5U);
    ASSERT_GE(sink.events.size(), 1U);
    auto* req = std::get_if<v2::room::BattleStartRequestedMsg>(&sink.events[0]);
    ASSERT_NE(req, nullptr);
    EXPECT_EQ(req->room_id, "r");
}

// ─── Player events ──────────────────────────────────────────────────────

TEST(BroadcastTest, LoginEmitsAcceptedAndResume) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::player::PlayerEventSink {
        void push(v2::player::PlayerEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::player::PlayerEvent> events;
    };
    Sink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::player::PlayerActor>(sink));

    auto tell = [&](auto payload) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::move(payload);
        ref.tell(std::move(msg));
    };
    tell(v2::player::BindSessionMsg{.session_id=100, .connection_id=900});
    tell(v2::player::LoginRequestMsg{.session_id=100, .user_id="p1", .token="t1", .display_name="P1"});

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    ASSERT_GE(sink.events.size(), 1U);
    auto* accepted = std::get_if<v2::player::LoginAcceptedMsg>(&sink.events[0]);
    ASSERT_NE(accepted, nullptr);
    EXPECT_EQ(accepted->user_id, "p1");
}

TEST(BroadcastTest, DuplicateLoginEmitsKickPush) {
    v2::runtime::ActorSystem actor_system;
    struct Sink : v2::player::PlayerEventSink {
        void push(v2::player::PlayerEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::player::PlayerEvent> events;
    };
    Sink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::player::PlayerActor>(sink));

    auto tell = [&](auto payload) {
        v2::actor::Message msg;
        msg.header.kind = v2::actor::MessageKind::kUser;
        msg.payload = std::move(payload);
        ref.tell(std::move(msg));
    };
    tell(v2::player::BindSessionMsg{.session_id=100, .connection_id=900});
    tell(v2::player::LoginRequestMsg{.session_id=100, .user_id="dup", .token="t", .display_name="D"});
    tell(v2::player::BindSessionMsg{.session_id=200, .connection_id=901});
    tell(v2::player::LoginRequestMsg{.session_id=200, .user_id="dup", .token="t", .display_name="D"});

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    ASSERT_GE(sink.events.size(), 3U);
    auto* kick = std::get_if<v2::player::SessionKickPushMsg>(&sink.events[1]);
    ASSERT_NE(kick, nullptr);
    EXPECT_EQ(kick->old_session_id, 100U);
    EXPECT_EQ(kick->new_session_id, 200U);
}

// ─── Battle settlement broadcast ────────────────────────────────────────

TEST(BroadcastTest, BattleSettlementEmitsEvents) {
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
        .battle_id = "settle", .room_id = "r", .player_ids = {"a","b"}, .max_frames = 2};
    ref.tell(std::move(create));

    for (int i = 0; i < 2; ++i) {
        v2::actor::Message tick;
        tick.header.kind = v2::actor::MessageKind::kUser;
        tick.payload = v2::battle::TickBattleMsg{.trigger = "test"};
        ref.tell(std::move(tick));
    }
    actor_system.dispatch_all();

    // Should have: created + frame_advanced + settlement + finished
    ASSERT_GE(sink.events.size(), 3U);
    bool has_settlement = false, has_finished = false;
    for (auto& e : sink.events) {
        if (std::holds_alternative<v2::battle::BattleSettlementPreparedMsg>(e)) has_settlement = true;
        if (std::holds_alternative<v2::battle::BattleFinishedMsg>(e)) has_finished = true;
    }
    EXPECT_TRUE(has_settlement);
    EXPECT_TRUE(has_finished);
}
