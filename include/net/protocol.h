#pragma once

#include <cstdint>

namespace net::protocol {

// Protocol version range supported by this build.
// Used during connection-level version handshake and packet validation.
constexpr std::uint8_t kProtocolVersion = 1;
constexpr std::uint8_t kProtocolMinVersion = 1;
constexpr std::uint8_t kProtocolMaxVersion = 1;

// Version negotiation message types (500-599).
// These are exchanged at connection startup before application messages.
constexpr std::uint16_t kVersionRequest = 500;
constexpr std::uint16_t kVersionResponse = 501;

enum class ErrorCode : std::int32_t {
    kOk = 0,
    kAuthRequired = 1001,
    kInvalidUserId = 1002,
    kInvalidToken = 1003,
    kDuplicateLogin = 1004,
    kInvalidRoomId = 2001,
    kRoomAlreadyExists = 2002,
    kRoomNotFound = 2003,
    kRoomInBattle = 2004,
    kNotInRoom = 2005,
    kNotRoomOwner = 2006,
    kNotAllReady = 2007,
    kNotEnoughPlayers = 3001,
    kBattleAlreadyStarted = 3002,
    kBattleNotStarted = 3003,
    kPlayerNotInBattle = 3004,
    kTokenExpired = 1005,
    kLoginBackendUnavailable = 2008,
    kRoomBackendUnavailable = 2009,
    kBattleBackendUnavailable = 3010,
    kRateLimited = 9001,
    kSessionNotFound = 9002,
};

constexpr std::uint16_t kHeartbeatRequest = 1;
constexpr std::uint16_t kHeartbeatResponse = 2;

constexpr std::uint16_t kEchoRequest = 1001;
constexpr std::uint16_t kEchoResponse = 1002;
constexpr std::uint16_t kSessionKickedPush = 1003;
constexpr std::uint16_t kSessionResumedPush = 1004;

constexpr std::uint16_t kLoginRequest = 2001;
constexpr std::uint16_t kLoginResponse = 2002;
constexpr std::uint16_t kRegisterRequest = 2003;
constexpr std::uint16_t kRegisterResponse = 2004;

constexpr std::uint16_t kRoomCreateRequest = 3001;
constexpr std::uint16_t kRoomCreateResponse = 3002;
constexpr std::uint16_t kRoomJoinRequest = 3003;
constexpr std::uint16_t kRoomJoinResponse = 3004;
constexpr std::uint16_t kRoomLeaveRequest = 3005;
constexpr std::uint16_t kRoomLeaveResponse = 3006;
constexpr std::uint16_t kRoomReadyRequest = 3007;
constexpr std::uint16_t kRoomReadyResponse = 3008;
constexpr std::uint16_t kRoomStatePush = 3009;
constexpr std::uint16_t kRoomListRequest = 3010;
constexpr std::uint16_t kRoomListResponse = 3011;
constexpr std::uint16_t kRoomDetailRequest = 3012;
constexpr std::uint16_t kRoomDetailResponse = 3013;
constexpr std::uint16_t kRoomKickRequest = 3014;
constexpr std::uint16_t kRoomKickResponse = 3015;
constexpr std::uint16_t kRoomTransferOwnerRequest = 3016;
constexpr std::uint16_t kRoomTransferOwnerResponse = 3017;

constexpr std::uint16_t kBattleStartRequest = 4001;
constexpr std::uint16_t kBattleStartResponse = 4002;
constexpr std::uint16_t kBattleInputRequest = 4003;
constexpr std::uint16_t kBattleInputResponse = 4004;
constexpr std::uint16_t kBattleInputPush = 4005;
constexpr std::uint16_t kBattleStatePush = 4006;
constexpr std::uint16_t kBattleStateRequest = 4007;
constexpr std::uint16_t kBattleStateResponse = 4008;
constexpr std::uint16_t kReplayLoadRequest = 4009;
constexpr std::uint16_t kReplayLoadResponse = 4010;

constexpr std::uint16_t kErrorResponse = 9001;

// Admin/GM commands (5000-5099)
constexpr std::uint16_t kAdminKickPlayer = 5001;
constexpr std::uint16_t kAdminBanIp = 5002;
constexpr std::uint16_t kAdminServerStatus = 5003;
constexpr std::uint16_t kAdminReloadConfig = 5004;
constexpr std::uint16_t kAdminResponse = 5005;

constexpr std::uint16_t kMatchJoinRequest = 6001;
constexpr std::uint16_t kMatchJoinResponse = 6002;
constexpr std::uint16_t kMatchFoundPush = 6003;
constexpr std::uint16_t kMatchLeaveRequest = 6004;
constexpr std::uint16_t kMatchLeaveResponse = 6005;
constexpr std::uint16_t kMatchStatusRequest = 6006;
constexpr std::uint16_t kMatchStatusResponse = 6007;
constexpr std::uint16_t kMatchTimeoutPush = 6008;
constexpr std::uint16_t kMatchToRoomRequest = 6010;
constexpr std::uint16_t kMatchToRoomResponse = 6011;

constexpr std::uint16_t kLeaderboardSubmitRequest = 7001;
constexpr std::uint16_t kLeaderboardSubmitResponse = 7002;
constexpr std::uint16_t kLeaderboardTopRequest = 7003;
constexpr std::uint16_t kLeaderboardTopResponse = 7004;
constexpr std::uint16_t kLeaderboardRankRequest = 7005;
constexpr std::uint16_t kLeaderboardRankResponse = 7006;

[[nodiscard]] constexpr const char* to_string(ErrorCode error_code) {
    switch (error_code) {
        case ErrorCode::kOk:
            return "ok";
        case ErrorCode::kAuthRequired:
            return "auth_required";
        case ErrorCode::kInvalidUserId:
            return "invalid_user_id";
        case ErrorCode::kInvalidToken:
            return "invalid_token";
        case ErrorCode::kTokenExpired:
            return "token_expired";
        case ErrorCode::kDuplicateLogin:
            return "duplicate_login";
        case ErrorCode::kInvalidRoomId:
            return "invalid_room_id";
        case ErrorCode::kRoomAlreadyExists:
            return "room_already_exists";
        case ErrorCode::kRoomNotFound:
            return "room_not_found";
        case ErrorCode::kRoomInBattle:
            return "room_in_battle";
        case ErrorCode::kNotInRoom:
            return "not_in_room";
        case ErrorCode::kNotRoomOwner:
            return "not_room_owner";
        case ErrorCode::kNotAllReady:
            return "not_all_ready";
        case ErrorCode::kNotEnoughPlayers:
            return "not_enough_players";
        case ErrorCode::kBattleAlreadyStarted:
            return "battle_already_started";
        case ErrorCode::kBattleNotStarted:
            return "battle_not_started";
        case ErrorCode::kPlayerNotInBattle:
            return "player_not_in_battle";
        case ErrorCode::kLoginBackendUnavailable:
            return "login_backend_unavailable";
        case ErrorCode::kRoomBackendUnavailable:
            return "room_backend_unavailable";
        case ErrorCode::kBattleBackendUnavailable:
            return "battle_backend_unavailable";
        case ErrorCode::kRateLimited:
            return "rate_limited";
        case ErrorCode::kSessionNotFound:
            return "session_not_found";
    }

    return "unknown_error";
}

}  // namespace net::protocol
