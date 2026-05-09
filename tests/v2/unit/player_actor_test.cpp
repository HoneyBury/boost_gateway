#include <gtest/gtest.h>

#include <memory>
#include <vector>

#include "v2/player/player_actor.h"
#include "v2/runtime/actor_system.h"

namespace {

class RecordingPlayerSink final : public v2::player::PlayerEventSink {
public:
    void push(v2::player::PlayerEvent event) override {
        events.push_back(std::move(event));
    }

    std::vector<v2::player::PlayerEvent> events;
};

v2::actor::Message make_message(v2::player::BindSessionMsg payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = payload;
    return message;
}

v2::actor::Message make_message(v2::player::LoginRequestMsg payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::move(payload);
    return message;
}

v2::actor::Message make_message(v2::player::RoomAssignedMsg payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::move(payload);
    return message;
}

v2::actor::Message make_message(v2::player::BattleAssignedMsg payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::move(payload);
    return message;
}

v2::actor::Message make_message(v2::player::BattleEndedMsg payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::move(payload);
    return message;
}

v2::actor::Message make_message(v2::player::SessionClosedMsg payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = payload;
    return message;
}

}  // namespace

TEST(V2PlayerActorTest, LoginAcceptsAndTransitionsToOnlineIdle) {
    v2::runtime::ActorSystem actor_system;
    RecordingPlayerSink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_message(v2::player::BindSessionMsg{.session_id = 100, .connection_id = 900}));
    actor_ref.tell(make_message(v2::player::LoginRequestMsg{
        .session_id = 100,
        .user_id = "player_01",
        .token = "token:player_01",
        .display_name = std::string("PlayerOne"),
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::player::PlayerLifecycleState::kOnlineIdle);
    ASSERT_TRUE(actor_ptr->state().binding.has_value());
    EXPECT_EQ(actor_ptr->state().binding->session_id, 100U);
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* accepted = std::get_if<v2::player::LoginAcceptedMsg>(&sink.events.front());
    ASSERT_NE(accepted, nullptr);
    EXPECT_EQ(accepted->user_id, "player_01");
}

TEST(V2PlayerActorTest, DuplicateLoginKicksOldSessionAndRebinds) {
    v2::runtime::ActorSystem actor_system;
    RecordingPlayerSink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_message(v2::player::BindSessionMsg{.session_id = 100, .connection_id = 900}));
    actor_ref.tell(make_message(v2::player::LoginRequestMsg{
        .session_id = 100,
        .user_id = "player_01",
        .token = "token:player_01",
        .display_name = std::string("PlayerOne"),
    }));
    actor_ref.tell(make_message(v2::player::BindSessionMsg{.session_id = 200, .connection_id = 901}));
    actor_ref.tell(make_message(v2::player::LoginRequestMsg{
        .session_id = 200,
        .user_id = "player_01",
        .token = "token:player_01",
        .display_name = std::string("PlayerOne"),
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    ASSERT_TRUE(actor_ptr->state().binding.has_value());
    EXPECT_EQ(actor_ptr->state().binding->session_id, 200U);
    ASSERT_EQ(sink.events.size(), 3U);
    const auto* kick = std::get_if<v2::player::SessionKickPushMsg>(&sink.events[1]);
    ASSERT_NE(kick, nullptr);
    EXPECT_EQ(kick->old_session_id, 100U);
    EXPECT_EQ(kick->new_session_id, 200U);
}

TEST(V2PlayerActorTest, SessionCloseAndReloginResumeAssignedRoom) {
    v2::runtime::ActorSystem actor_system;
    RecordingPlayerSink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_message(v2::player::BindSessionMsg{.session_id = 100, .connection_id = 900}));
    actor_ref.tell(make_message(v2::player::LoginRequestMsg{
        .session_id = 100,
        .user_id = "player_resume",
        .token = "token:player_resume",
        .display_name = std::string("ResumePlayer"),
    }));
    actor_ref.tell(make_message(v2::player::RoomAssignedMsg{
        .room_actor_id = 42,
        .room_id = "room_resume",
    }));
    actor_ref.tell(make_message(v2::player::SessionClosedMsg{.session_id = 100}));
    actor_ref.tell(make_message(v2::player::BindSessionMsg{.session_id = 200, .connection_id = 901}));
    actor_ref.tell(make_message(v2::player::LoginRequestMsg{
        .session_id = 200,
        .user_id = "player_resume",
        .token = "token:player_resume",
        .display_name = std::string("ResumePlayer"),
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 6U);
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::player::PlayerLifecycleState::kInRoom);
    ASSERT_TRUE(actor_ptr->state().room_id.has_value());
    EXPECT_EQ(*actor_ptr->state().room_id, "room_resume");
    ASSERT_EQ(sink.events.size(), 3U);
    const auto* accepted = std::get_if<v2::player::LoginAcceptedMsg>(&sink.events[1]);
    ASSERT_NE(accepted, nullptr);
    ASSERT_TRUE(accepted->room_id.has_value());
    EXPECT_EQ(*accepted->room_id, "room_resume");
    const auto* resumed = std::get_if<v2::player::SessionResumePushMsg>(&sink.events[2]);
    ASSERT_NE(resumed, nullptr);
    EXPECT_EQ(resumed->session_id, 200U);
    EXPECT_EQ(resumed->room_id, "room_resume");
}

TEST(V2PlayerActorTest, BattleAssignedAndEndedTransitionsBackToRoom) {
    v2::runtime::ActorSystem actor_system;
    RecordingPlayerSink sink;
    auto actor = std::make_unique<v2::player::PlayerActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_message(v2::player::BindSessionMsg{.session_id = 100, .connection_id = 900}));
    actor_ref.tell(make_message(v2::player::LoginRequestMsg{
        .session_id = 100,
        .user_id = "fighter",
        .token = "token:fighter",
        .display_name = std::string("Fighter"),
    }));
    actor_ref.tell(make_message(v2::player::RoomAssignedMsg{
        .room_actor_id = 42,
        .room_id = "room_battle",
    }));
    actor_ref.tell(make_message(v2::player::BattleAssignedMsg{
        .battle_actor_id = 77,
        .battle_id = "battle_0001",
    }));
    actor_ref.tell(make_message(v2::player::BattleEndedMsg{
        .battle_id = "battle_0001",
        .reason = "player_disconnected",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 5U);
    EXPECT_EQ(actor_ptr->state().lifecycle, v2::player::PlayerLifecycleState::kInRoom);
    ASSERT_TRUE(actor_ptr->state().room_id.has_value());
    EXPECT_EQ(*actor_ptr->state().room_id, "room_battle");
    EXPECT_FALSE(actor_ptr->state().battle_id.has_value());
    EXPECT_FALSE(actor_ptr->state().battle_actor_id.has_value());
}
