#include <gtest/gtest.h>

#include <memory>
#include <utility>
#include <vector>

#include "v2/room/room_actor.h"
#include "v2/runtime/actor_system.h"

namespace {

class RecordingRoomSink final : public v2::room::RoomEventSink {
public:
    void push(v2::room::RoomEvent event) override {
        events.push_back(std::move(event));
    }

    std::vector<v2::room::RoomEvent> events;
};

template <typename Payload>
v2::actor::Message make_room_message(Payload payload) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::move(payload);
    return message;
}

}  // namespace

TEST(V2RoomActorTest, CreateJoinAndReadyTracksOwnerAndMembers) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_alpha",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "member",
        .ready = true,
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    EXPECT_EQ(actor_ptr->state().room_id, "room_alpha");
    EXPECT_EQ(actor_ptr->state().owner_user_id, "owner");
    ASSERT_EQ(actor_ptr->state().members.size(), 2U);
    EXPECT_TRUE(actor_ptr->state().members[0].ready);
    EXPECT_TRUE(actor_ptr->state().members[1].ready);
}

TEST(V2RoomActorTest, StartBattleRejectedWhenRequesterIsNotOwner) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_beta",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "member",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::StartBattleMsg{
        .requester_user_id = "member",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 5U);
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* rejected = std::get_if<v2::room::BattleStartRejectedMsg>(&sink.events.front());
    ASSERT_NE(rejected, nullptr);
    EXPECT_EQ(rejected->reason, "not_room_owner");
}

TEST(V2RoomActorTest, StartBattleRejectedWhenNotEnoughPlayersOrNotReady) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_gamma",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::StartBattleMsg{
        .requester_user_id = "owner",
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::StartBattleMsg{
        .requester_user_id = "owner",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 5U);
    ASSERT_EQ(sink.events.size(), 2U);
    const auto* first = std::get_if<v2::room::BattleStartRejectedMsg>(&sink.events[0]);
    const auto* second = std::get_if<v2::room::BattleStartRejectedMsg>(&sink.events[1]);
    ASSERT_NE(first, nullptr);
    ASSERT_NE(second, nullptr);
    EXPECT_EQ(first->reason, "not_enough_players");
    EXPECT_EQ(second->reason, "not_all_ready");
}

TEST(V2RoomActorTest, StartBattleRequestsBattleWhenRoomIsEligible) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_delta",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "member",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::StartBattleMsg{
        .requester_user_id = "owner",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 5U);
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* requested = std::get_if<v2::room::BattleStartRequestedMsg>(&sink.events.front());
    ASSERT_NE(requested, nullptr);
    EXPECT_EQ(requested->room_id, "room_delta");
    EXPECT_EQ(requested->requester_user_id, "owner");
    ASSERT_EQ(requested->player_ids.size(), 2U);
    EXPECT_EQ(requested->player_ids[0], "owner");
    EXPECT_EQ(requested->player_ids[1], "member");
}

TEST(V2RoomActorTest, BattleStartedBlocksRestartUntilBattleEnded) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_epsilon",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "member",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::BattleStartedMsg{
        .battle_id = "battle_0001",
    }));
    actor_ref.tell(make_room_message(v2::room::StartBattleMsg{
        .requester_user_id = "owner",
    }));
    actor_ref.tell(make_room_message(v2::room::BattleEndedMsg{
        .battle_id = "battle_0001",
        .reason = "player_disconnected",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 7U);
    EXPECT_FALSE(actor_ptr->state().active_battle_id.has_value());
    EXPECT_FALSE(actor_ptr->state().members[0].ready);
    EXPECT_FALSE(actor_ptr->state().members[1].ready);
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* rejected = std::get_if<v2::room::BattleStartRejectedMsg>(&sink.events.front());
    ASSERT_NE(rejected, nullptr);
    EXPECT_EQ(rejected->reason, "battle_already_started");
}
