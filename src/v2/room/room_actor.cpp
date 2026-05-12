#include "v2/room/room_actor.h"

#include <algorithm>
#include <utility>

namespace v2::room {

void RoomActor::on_message(v2::actor::Message&& message) {
    if (const auto* create = std::get_if<CreateRoomMsg>(&message.payload)) {
        handle_create_room(*create);
        return;
    }
    if (const auto* join = std::get_if<JoinRoomMsg>(&message.payload)) {
        handle_join_room(*join);
        return;
    }
    if (const auto* ready = std::get_if<SetReadyMsg>(&message.payload)) {
        handle_set_ready(*ready);
        return;
    }
    if (const auto* start = std::get_if<StartBattleMsg>(&message.payload)) {
        handle_start_battle(*start);
        return;
    }
    if (const auto* started = std::get_if<BattleStartedMsg>(&message.payload)) {
        handle_battle_started(*started);
        return;
    }
    if (const auto* settlement = std::get_if<BattleSettlementMsg>(&message.payload)) {
        handle_battle_settlement(*settlement);
        return;
    }
    if (const auto* ended = std::get_if<BattleEndedMsg>(&message.payload)) {
        handle_battle_ended(*ended);
        return;
    }
    if (const auto* leave = std::get_if<LeaveRoomMsg>(&message.payload)) {
        handle_leave_room(*leave);
        return;
    }
    if (const auto* kick = std::get_if<KickMemberMsg>(&message.payload)) {
        handle_kick_member(*kick);
        return;
    }
    if (const auto* transfer = std::get_if<TransferOwnerMsg>(&message.payload)) {
        handle_transfer_owner(*transfer);
    }
}

void RoomActor::handle_create_room(const CreateRoomMsg& message) {
    state_.room_id = message.room_id;
    state_.owner_user_id = message.owner_user_id;
    state_.members.clear();
    state_.members.push_back(RoomMemberState{
        .user_id = message.owner_user_id,
        .player_actor_id = message.owner_actor_id,
        .ready = false,
    });
}

void RoomActor::handle_join_room(const JoinRoomMsg& message) {
    if (find_member(message.user_id) != nullptr) {
        return;
    }
    state_.members.push_back(RoomMemberState{
        .user_id = message.user_id,
        .player_actor_id = message.player_actor_id,
        .ready = false,
    });
}

void RoomActor::handle_set_ready(const SetReadyMsg& message) {
    auto* member = find_member(message.user_id);
    if (member == nullptr) {
        return;
    }
    member->ready = message.ready;
}

void RoomActor::handle_start_battle(const StartBattleMsg& message) {
    if (state_.active_battle_id.has_value()) {
        sink_.push(BattleStartRejectedMsg{
            .room_id = state_.room_id,
            .reason = "battle_already_started",
        });
        return;
    }

    if (message.requester_user_id != state_.owner_user_id) {
        sink_.push(BattleStartRejectedMsg{
            .room_id = state_.room_id,
            .reason = "not_room_owner",
        });
        return;
    }

    if (state_.members.size() < 2) {
        sink_.push(BattleStartRejectedMsg{
            .room_id = state_.room_id,
            .reason = "not_enough_players",
        });
        return;
    }

    if (!all_members_ready()) {
        sink_.push(BattleStartRejectedMsg{
            .room_id = state_.room_id,
            .reason = "not_all_ready",
        });
        return;
    }

    std::vector<std::string> player_ids;
    player_ids.reserve(state_.members.size());
    for (const auto& member : state_.members) {
        player_ids.push_back(member.user_id);
    }

    sink_.push(BattleStartRequestedMsg{
        .room_id = state_.room_id,
        .player_ids = std::move(player_ids),
        .requester_user_id = message.requester_user_id,
    });
}

void RoomActor::handle_battle_started(const BattleStartedMsg& message) {
    state_.active_battle_id = message.battle_id;
    state_.pending_battle_settlement_reason.reset();
}

void RoomActor::handle_battle_settlement(const BattleSettlementMsg& message) {
    if (!state_.active_battle_id.has_value() || *state_.active_battle_id != message.battle_id) {
        return;
    }

    state_.pending_battle_settlement_reason = message.reason;
    sink_.push(BattleSettlementAppliedMsg{
        .room_id = state_.room_id,
        .battle_id = message.battle_id,
        .reason = message.reason,
    });
}

void RoomActor::handle_battle_ended(const BattleEndedMsg& message) {
    if (!state_.active_battle_id.has_value() || *state_.active_battle_id != message.battle_id) {
        return;
    }

    state_.active_battle_id.reset();
    state_.pending_battle_settlement_reason.reset();
    for (auto& member : state_.members) {
        member.ready = false;
    }
}

void RoomActor::handle_leave_room(const LeaveRoomMsg& message) {
    auto* member = find_member(message.user_id);
    if (member == nullptr) {
        return;
    }

    const bool was_owner = (state_.owner_user_id == message.user_id);
    state_.members.erase(
        std::remove_if(state_.members.begin(), state_.members.end(),
                       [&](const RoomMemberState& m) { return m.user_id == message.user_id; }),
        state_.members.end());

    sink_.push(RoomLeaveAppliedMsg{
        .room_id = state_.room_id,
        .user_id = message.user_id,
    });

    if (was_owner) {
        reassign_owner_if_needed();
    }
}

void RoomActor::handle_kick_member(const KickMemberMsg& message) {
    if (message.requester_user_id != state_.owner_user_id) {
        return;
    }
    if (message.target_user_id == state_.owner_user_id) {
        return;
    }

    auto* target = find_member(message.target_user_id);
    if (target == nullptr) {
        return;
    }

    state_.members.erase(
        std::remove_if(state_.members.begin(), state_.members.end(),
                       [&](const RoomMemberState& m) { return m.user_id == message.target_user_id; }),
        state_.members.end());

    sink_.push(RoomKickAppliedMsg{
        .room_id = state_.room_id,
        .target_user_id = message.target_user_id,
    });
}

void RoomActor::handle_transfer_owner(const TransferOwnerMsg& message) {
    if (message.requester_user_id != state_.owner_user_id) {
        return;
    }
    if (message.new_owner_user_id == state_.owner_user_id) {
        return;
    }
    auto* new_owner = find_member(message.new_owner_user_id);
    if (new_owner == nullptr) {
        return;
    }

    const auto old_owner = state_.owner_user_id;
    state_.owner_user_id = message.new_owner_user_id;

    sink_.push(RoomOwnerTransferredMsg{
        .room_id = state_.room_id,
        .old_owner_user_id = old_owner,
        .new_owner_user_id = message.new_owner_user_id,
    });
}

void RoomActor::reassign_owner_if_needed() {
    if (!state_.members.empty()) {
        state_.owner_user_id = state_.members.front().user_id;
        sink_.push(RoomOwnerTransferredMsg{
            .room_id = state_.room_id,
            .old_owner_user_id = {},
            .new_owner_user_id = state_.owner_user_id,
        });
    }
}

RoomMemberState* RoomActor::find_member(const std::string& user_id) noexcept {
    auto it = std::find_if(
        state_.members.begin(),
        state_.members.end(),
        [&user_id](const RoomMemberState& member) { return member.user_id == user_id; });
    if (it == state_.members.end()) {
        return nullptr;
    }
    return &*it;
}

bool RoomActor::all_members_ready() const noexcept {
    return std::all_of(
        state_.members.begin(),
        state_.members.end(),
        [](const RoomMemberState& member) { return member.ready; });
}

}  // namespace v2::room
