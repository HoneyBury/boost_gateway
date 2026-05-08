#pragma once

#include <cstdint>
#include <string>
#include <variant>
#include <vector>

namespace v2::room {

struct RoomMemberState {
    std::string user_id;
    std::uint64_t player_actor_id = 0;
    bool ready = false;
};

struct RoomRuntimeState {
    std::string room_id;
    std::string owner_user_id;
    std::vector<RoomMemberState> members;
};

struct CreateRoomMsg {
    std::string room_id;
    std::string owner_user_id;
    std::uint64_t owner_actor_id = 0;
};

struct JoinRoomMsg {
    std::string user_id;
    std::uint64_t player_actor_id = 0;
};

struct SetReadyMsg {
    std::string user_id;
    bool ready = false;
};

struct StartBattleMsg {
    std::string requester_user_id;
};

struct BattleStartRequestedMsg {
    std::string room_id;
    std::vector<std::string> player_ids;
    std::string requester_user_id;
};

struct BattleStartRejectedMsg {
    std::string room_id;
    std::string reason;
};

using RoomEvent = std::variant<BattleStartRequestedMsg, BattleStartRejectedMsg>;

}  // namespace v2::room
