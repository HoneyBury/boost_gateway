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
