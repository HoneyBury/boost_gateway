#pragma once

#include <cstdint>

namespace net::protocol {

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
    kTokenExpired = 1005,
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

constexpr std::uint16_t kRoomCreateRequest = 3001;
constexpr std::uint16_t kRoomCreateResponse = 3002;
constexpr std::uint16_t kRoomJoinRequest = 3003;
constexpr std::uint16_t kRoomJoinResponse = 3004;
constexpr std::uint16_t kRoomLeaveRequest = 3005;
constexpr std::uint16_t kRoomLeaveResponse = 3006;
constexpr std::uint16_t kRoomReadyRequest = 3007;
constexpr std::uint16_t kRoomReadyResponse = 3008;
constexpr std::uint16_t kRoomStatePush = 3009;

constexpr std::uint16_t kBattleStartRequest = 4001;
constexpr std::uint16_t kBattleStartResponse = 4002;
constexpr std::uint16_t kBattleInputRequest = 4003;
constexpr std::uint16_t kBattleInputResponse = 4004;
constexpr std::uint16_t kBattleInputPush = 4005;
constexpr std::uint16_t kBattleStatePush = 4006;

constexpr std::uint16_t kErrorResponse = 9001;

// Admin/GM commands (5000-5099)
constexpr std::uint16_t kAdminKickPlayer = 5001;
constexpr std::uint16_t kAdminBanIp = 5002;
constexpr std::uint16_t kAdminServerStatus = 5003;
constexpr std::uint16_t kAdminReloadConfig = 5004;
constexpr std::uint16_t kAdminResponse = 5005;

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
        case ErrorCode::kRateLimited:
            return "rate_limited";
        case ErrorCode::kSessionNotFound:
            return "session_not_found";
    }

    return "unknown_error";
}

}  // namespace net::protocol
