#pragma once

#include <cstdint>

namespace net::protocol {

enum class ErrorCode : std::int32_t {
    kOk = 0,
    kAuthRequired = 1001,
    kInvalidUserId = 1002,
    kDuplicateLogin = 1003,
    kInvalidRoomId = 2001,
    kRoomInBattle = 2002,
    kNotInRoom = 2003,
    kNotEnoughPlayers = 3001,
    kBattleAlreadyStarted = 3002,
    kRateLimited = 9001,
    kSessionNotFound = 9002,
};

constexpr std::uint16_t kHeartbeatRequest = 1;
constexpr std::uint16_t kHeartbeatResponse = 2;

constexpr std::uint16_t kEchoRequest = 1001;
constexpr std::uint16_t kEchoResponse = 1002;

constexpr std::uint16_t kLoginRequest = 2001;
constexpr std::uint16_t kLoginResponse = 2002;

constexpr std::uint16_t kRoomJoinRequest = 3001;
constexpr std::uint16_t kRoomJoinResponse = 3002;

constexpr std::uint16_t kBattleStartRequest = 4001;
constexpr std::uint16_t kBattleStartResponse = 4002;

constexpr std::uint16_t kErrorResponse = 9001;

[[nodiscard]] constexpr const char* to_string(ErrorCode error_code) {
    switch (error_code) {
        case ErrorCode::kOk:
            return "ok";
        case ErrorCode::kAuthRequired:
            return "auth_required";
        case ErrorCode::kInvalidUserId:
            return "invalid_user_id";
        case ErrorCode::kDuplicateLogin:
            return "duplicate_login";
        case ErrorCode::kInvalidRoomId:
            return "invalid_room_id";
        case ErrorCode::kRoomInBattle:
            return "room_in_battle";
        case ErrorCode::kNotInRoom:
            return "not_in_room";
        case ErrorCode::kNotEnoughPlayers:
            return "not_enough_players";
        case ErrorCode::kBattleAlreadyStarted:
            return "battle_already_started";
        case ErrorCode::kRateLimited:
            return "rate_limited";
        case ErrorCode::kSessionNotFound:
            return "session_not_found";
    }

    return "unknown_error";
}

}  // namespace net::protocol
