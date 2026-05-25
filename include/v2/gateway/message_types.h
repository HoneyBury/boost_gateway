#pragma once

#include <cstdint>
#include <string>

namespace v2::gateway {

using SessionId = std::uint64_t;

struct ClientEnvelope {
    SessionId session_id = 0;
    std::uint16_t protocol_message_id = 0;
    std::uint32_t request_id = 0;
    std::int32_t error_code = 0;
    std::uint8_t flags = 0;
    std::string body;
};

enum class GatewayCommandType : std::uint16_t {
    kUnknown = 0,
    kHeartbeat = 1,
    kEcho = 2,
    kLogin = 3,
    kRoomCreate = 4,
    kRoomJoin = 5,
    kRoomReady = 6,
    kRoomLeave = 7,
    kBattleStart = 8,
    kBattleInput = 9,
    kMatchJoin = 10,
    kMatchLeave = 11,
    kMatchStatus = 12,
    kLeaderboardSubmit = 13,
    kLeaderboardTop = 14,
    kLeaderboardRank = 15,
    kRoomList = 16,
    kRoomDetail = 17,
    kBattleState = 18,
    kRegister = 19,
    kRoomKick = 20,
    kRoomTransferOwner = 21,
    kReplayLoad = 22,
};

struct GatewayCommand {
    SessionId session_id = 0;
    GatewayCommandType type = GatewayCommandType::kUnknown;
    std::uint16_t protocol_message_id = 0;
    std::uint32_t request_id = 0;
    std::uint8_t flags = 0;
    std::string body;
};

struct SessionWrite {
    ClientEnvelope envelope;
};

}  // namespace v2::gateway
