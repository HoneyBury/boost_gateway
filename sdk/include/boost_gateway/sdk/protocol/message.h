#pragma once
// SDK v4.1.0: Protocol message IDs and error codes — standalone, zero deps.

#include <cstdint>

namespace boost_gateway {
namespace sdk {
namespace protocol {

// ── Message IDs ─────────────────────────────────────────────────────────

constexpr std::uint16_t kHeartbeatRequest  = 1;
constexpr std::uint16_t kHeartbeatResponse = 2;

constexpr std::uint16_t kEchoRequest       = 1001;
constexpr std::uint16_t kEchoResponse      = 1002;
constexpr std::uint16_t kSessionKickedPush = 1003;
constexpr std::uint16_t kSessionResumedPush = 1004;

constexpr std::uint16_t kLoginRequest      = 2001;
constexpr std::uint16_t kLoginResponse     = 2002;
constexpr std::uint16_t kRegisterRequest   = 2003;
constexpr std::uint16_t kRegisterResponse  = 2004;

constexpr std::uint16_t kRoomCreateRequest = 3001;
constexpr std::uint16_t kRoomCreateResponse = 3002;
constexpr std::uint16_t kRoomJoinRequest   = 3003;
constexpr std::uint16_t kRoomJoinResponse  = 3004;
constexpr std::uint16_t kRoomLeaveRequest  = 3005;
constexpr std::uint16_t kRoomLeaveResponse = 3006;
constexpr std::uint16_t kRoomReadyRequest  = 3007;
constexpr std::uint16_t kRoomReadyResponse = 3008;
constexpr std::uint16_t kRoomStatePush     = 3009;
constexpr std::uint16_t kRoomListRequest   = 3010;
constexpr std::uint16_t kRoomListResponse  = 3011;
constexpr std::uint16_t kRoomDetailRequest = 3012;
constexpr std::uint16_t kRoomDetailResponse = 3013;
constexpr std::uint16_t kRoomKickRequest = 3014;
constexpr std::uint16_t kRoomKickResponse = 3015;
constexpr std::uint16_t kRoomTransferOwnerRequest = 3016;
constexpr std::uint16_t kRoomTransferOwnerResponse = 3017;

constexpr std::uint16_t kBattleStartRequest  = 4001;
constexpr std::uint16_t kBattleStartResponse = 4002;
constexpr std::uint16_t kBattleInputRequest  = 4003;
constexpr std::uint16_t kBattleInputResponse = 4004;
constexpr std::uint16_t kBattleInputPush     = 4005;
constexpr std::uint16_t kBattleStatePush     = 4006;
constexpr std::uint16_t kBattleStateRequest  = 4007;
constexpr std::uint16_t kBattleStateResponse = 4008;
constexpr std::uint16_t kReplayLoadRequest   = 4009;
constexpr std::uint16_t kReplayLoadResponse  = 4010;

constexpr std::uint16_t kAdminKickPlayer     = 5001;
constexpr std::uint16_t kAdminBanIp          = 5002;
constexpr std::uint16_t kAdminServerStatus   = 5003;
constexpr std::uint16_t kAdminReloadConfig   = 5004;
constexpr std::uint16_t kAdminResponse       = 5005;

constexpr std::uint16_t kMatchJoinRequest    = 6001;
constexpr std::uint16_t kMatchJoinResponse   = 6002;
constexpr std::uint16_t kMatchFoundPush      = 6003;
constexpr std::uint16_t kMatchLeaveRequest   = 6004;
constexpr std::uint16_t kMatchLeaveResponse  = 6005;
constexpr std::uint16_t kMatchStatusRequest  = 6006;
constexpr std::uint16_t kMatchStatusResponse = 6007;

constexpr std::uint16_t kLeaderboardSubmitRequest = 7001;
constexpr std::uint16_t kLeaderboardSubmitResponse = 7002;
constexpr std::uint16_t kLeaderboardTopRequest = 7003;
constexpr std::uint16_t kLeaderboardTopResponse = 7004;
constexpr std::uint16_t kLeaderboardRankRequest = 7005;
constexpr std::uint16_t kLeaderboardRankResponse = 7006;

constexpr std::uint16_t kErrorResponse      = 9001;

// ── Error codes ─────────────────────────────────────────────────────────

enum class ErrorCode : std::int32_t {
    kOk = 0,
    kAuthRequired = 1001,
    kInvalidUserId = 1002,
    kInvalidToken = 1003,
    kDuplicateLogin = 1004,
    kTokenExpired = 1005,
    kInvalidRoomId = 2001,
    kRoomAlreadyExists = 2002,
    kRoomNotFound = 2003,
    kRoomInBattle = 2004,
    kNotInRoom = 2005,
    kNotRoomOwner = 2006,
    kNotAllReady = 2007,
    kLoginBackendUnavailable = 2008,
    kRoomBackendUnavailable = 2009,
    kNotEnoughPlayers = 3001,
    kBattleAlreadyStarted = 3002,
    kBattleNotStarted = 3003,
    kPlayerNotInBattle = 3004,
    kBattleBackendUnavailable = 3010,
    kRateLimited = 9001,
    kSessionNotFound = 9002,
};

}  // namespace protocol
}  // namespace sdk
}  // namespace boost_gateway
