#pragma once

#include <cstdint>
#include <optional>
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
    std::optional<std::string> active_battle_id;
    std::optional<std::string> pending_battle_settlement_reason;
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

struct BattleStartedMsg {
    std::string battle_id;
};

struct BattleSettlementMsg {
    std::string battle_id;
    std::string reason;
};

struct BattleEndedMsg {
    std::string battle_id;
    std::string reason;
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

struct BattleSettlementAppliedMsg {
    std::string room_id;
    std::string battle_id;
    std::string reason;
};

struct LeaveRoomMsg {
    std::string user_id;
};

struct KickMemberMsg {
    std::string requester_user_id;
    std::string target_user_id;
};

struct TransferOwnerMsg {
    std::string requester_user_id;
    std::string new_owner_user_id;
};

struct RoomLeaveAppliedMsg {
    std::string room_id;
    std::string user_id;
};

struct RoomKickAppliedMsg {
    std::string room_id;
    std::string target_user_id;
};

struct RoomOwnerTransferredMsg {
    std::string room_id;
    std::string old_owner_user_id;
    std::string new_owner_user_id;
};

using RoomEvent = std::variant<BattleStartRequestedMsg,
                               BattleStartRejectedMsg,
                               BattleSettlementAppliedMsg,
                               RoomLeaveAppliedMsg,
                               RoomKickAppliedMsg,
                               RoomOwnerTransferredMsg>;

}  // namespace v2::room
