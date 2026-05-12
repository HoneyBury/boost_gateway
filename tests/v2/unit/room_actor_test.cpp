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
    actor_ref.tell(make_room_message(v2::room::BattleSettlementMsg{
        .battle_id = "battle_0001",
        .reason = "surrender",
    }));
    actor_ref.tell(make_room_message(v2::room::StartBattleMsg{
        .requester_user_id = "owner",
    }));
    actor_ref.tell(make_room_message(v2::room::BattleEndedMsg{
        .battle_id = "battle_0001",
        .reason = "player_disconnected",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 8U);
    EXPECT_FALSE(actor_ptr->state().active_battle_id.has_value());
    EXPECT_FALSE(actor_ptr->state().pending_battle_settlement_reason.has_value());
    EXPECT_FALSE(actor_ptr->state().members[0].ready);
    EXPECT_FALSE(actor_ptr->state().members[1].ready);
    ASSERT_EQ(sink.events.size(), 2U);
    const auto* settlement = std::get_if<v2::room::BattleSettlementAppliedMsg>(&sink.events.front());
    ASSERT_NE(settlement, nullptr);
    EXPECT_EQ(settlement->room_id, "room_epsilon");
    EXPECT_EQ(settlement->battle_id, "battle_0001");
    EXPECT_EQ(settlement->reason, "surrender");
    const auto* rejected = std::get_if<v2::room::BattleStartRejectedMsg>(&sink.events[1]);
    ASSERT_NE(rejected, nullptr);
    EXPECT_EQ(rejected->reason, "battle_already_started");
}

TEST(V2RoomActorTest, DuplicateJoinIsNoOp) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_dup",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    ASSERT_EQ(actor_ptr->state().members.size(), 2U);
    EXPECT_EQ(actor_ptr->state().members[0].user_id, "owner");
    EXPECT_EQ(actor_ptr->state().members[1].user_id, "member");
}

TEST(V2RoomActorTest, SetReadyForUnknownUserIsIgnored) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_ready",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "ghost",
        .ready = true,
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_FALSE(actor_ptr->state().members[0].ready);
}

TEST(V2RoomActorTest, BattleSettlementWithWrongBattleIdIsIgnored) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_settle",
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
    actor_ref.tell(make_room_message(v2::room::BattleStartedMsg{
        .battle_id = "battle_settle",
    }));
    // Settlement with wrong battle_id should be ignored
    actor_ref.tell(make_room_message(v2::room::BattleSettlementMsg{
        .battle_id = "wrong_battle",
        .reason = "surrender",
    }));
    // Settlement with correct battle_id should be applied
    actor_ref.tell(make_room_message(v2::room::BattleSettlementMsg{
        .battle_id = "battle_settle",
        .reason = "victory",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 8U);
    ASSERT_EQ(sink.events.size(), 2U);
    const auto* requested = std::get_if<v2::room::BattleStartRequestedMsg>(&sink.events[0]);
    ASSERT_NE(requested, nullptr);
    EXPECT_EQ(requested->room_id, "room_settle");
    const auto* applied = std::get_if<v2::room::BattleSettlementAppliedMsg>(&sink.events[1]);
    ASSERT_NE(applied, nullptr);
    EXPECT_EQ(applied->battle_id, "battle_settle");
    EXPECT_EQ(applied->reason, "victory");
    ASSERT_TRUE(actor_ptr->state().pending_battle_settlement_reason.has_value());
    EXPECT_EQ(*actor_ptr->state().pending_battle_settlement_reason, "victory");
}

TEST(V2RoomActorTest, MemberReadyStateTogglesOnOff) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_toggle",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = true,
    }));
    actor_ref.tell(make_room_message(v2::room::SetReadyMsg{
        .user_id = "owner",
        .ready = false,
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_FALSE(actor_ptr->state().members[0].ready);
}

TEST(V2RoomActorTest, LeaveRoomRemovesMemberAndEmitsEvent) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_leave",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::LeaveRoomMsg{
        .user_id = "member",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_EQ(actor_ptr->state().members[0].user_id, "owner");
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* leave_event = std::get_if<v2::room::RoomLeaveAppliedMsg>(&sink.events.front());
    ASSERT_NE(leave_event, nullptr);
    EXPECT_EQ(leave_event->user_id, "member");
    EXPECT_EQ(leave_event->room_id, "room_leave");
}

TEST(V2RoomActorTest, OwnerLeavingTransfersOwnershipToNextMember) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_owner_leave",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::LeaveRoomMsg{
        .user_id = "owner",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_EQ(actor_ptr->state().members[0].user_id, "member");
    EXPECT_EQ(actor_ptr->state().owner_user_id, "member");
    ASSERT_GE(sink.events.size(), 2U);
    const auto* leave_event = std::get_if<v2::room::RoomLeaveAppliedMsg>(&sink.events[0]);
    ASSERT_NE(leave_event, nullptr);
    EXPECT_EQ(leave_event->user_id, "owner");
    const auto* transfer_event = std::get_if<v2::room::RoomOwnerTransferredMsg>(&sink.events[1]);
    ASSERT_NE(transfer_event, nullptr);
    EXPECT_EQ(transfer_event->new_owner_user_id, "member");
}

TEST(V2RoomActorTest, OwnerKicksMemberSuccessfully) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_kick",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::KickMemberMsg{
        .requester_user_id = "owner",
        .target_user_id = "member",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_EQ(actor_ptr->state().members[0].user_id, "owner");
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* kick_event = std::get_if<v2::room::RoomKickAppliedMsg>(&sink.events.front());
    ASSERT_NE(kick_event, nullptr);
    EXPECT_EQ(kick_event->target_user_id, "member");
}

TEST(V2RoomActorTest, NonOwnerCannotKickMember) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_nokick",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "third",
        .player_actor_id = 1003,
    }));
    actor_ref.tell(make_room_message(v2::room::KickMemberMsg{
        .requester_user_id = "member",
        .target_user_id = "third",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 4U);
    ASSERT_EQ(actor_ptr->state().members.size(), 3U);
    EXPECT_EQ(sink.events.size(), 0U);
}

TEST(V2RoomActorTest, OwnerCannotKickSelf) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_selfkick",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::KickMemberMsg{
        .requester_user_id = "owner",
        .target_user_id = "owner",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_EQ(sink.events.size(), 0U);
}

TEST(V2RoomActorTest, TransferOwnerToValidMember) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_transfer",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::JoinRoomMsg{
        .user_id = "member",
        .player_actor_id = 1002,
    }));
    actor_ref.tell(make_room_message(v2::room::TransferOwnerMsg{
        .requester_user_id = "owner",
        .new_owner_user_id = "member",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 3U);
    EXPECT_EQ(actor_ptr->state().owner_user_id, "member");
    ASSERT_EQ(sink.events.size(), 1U);
    const auto* transfer_event = std::get_if<v2::room::RoomOwnerTransferredMsg>(&sink.events.front());
    ASSERT_NE(transfer_event, nullptr);
    EXPECT_EQ(transfer_event->old_owner_user_id, "owner");
    EXPECT_EQ(transfer_event->new_owner_user_id, "member");
}

TEST(V2RoomActorTest, TransferOwnerToNonMemberIsRejected) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_badtransfer",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::TransferOwnerMsg{
        .requester_user_id = "owner",
        .new_owner_user_id = "ghost",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    EXPECT_EQ(actor_ptr->state().owner_user_id, "owner");
    EXPECT_EQ(sink.events.size(), 0U);
}

TEST(V2RoomActorTest, LeaveRoomWithUnknownUserIsIgnored) {
    v2::runtime::ActorSystem actor_system;
    RecordingRoomSink sink;
    auto actor = std::make_unique<v2::room::RoomActor>(sink);
    auto* actor_ptr = actor.get();
    auto actor_ref = actor_system.create_actor(std::move(actor));

    actor_ref.tell(make_room_message(v2::room::CreateRoomMsg{
        .room_id = "room_ghost",
        .owner_user_id = "owner",
        .owner_actor_id = 1001,
    }));
    actor_ref.tell(make_room_message(v2::room::LeaveRoomMsg{
        .user_id = "ghost",
    }));

    EXPECT_EQ(actor_system.dispatch_all(), 2U);
    ASSERT_EQ(actor_ptr->state().members.size(), 1U);
    EXPECT_EQ(actor_ptr->state().members[0].user_id, "owner");
}
