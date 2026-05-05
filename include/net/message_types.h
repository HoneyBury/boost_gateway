#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace net::msg {

// ---------------------------------------------------------------------------
// Common
// ---------------------------------------------------------------------------
struct EchoRequest {
    std::string payload;
};

struct EchoResponse {
    std::string payload;
};

struct ErrorDetail {
    std::int32_t error_code = 0;
    std::string message;
};

struct SessionKickedPush {
    std::string reason;
};

struct SessionResumedPush {
    std::string room_id;
};

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------
struct LoginRequest {
    std::string user_id;
    std::string token;
    std::string display_name;
};

struct LoginResponse {
    std::string user_id;
    std::string display_name;
    bool ok = false;
};

// ---------------------------------------------------------------------------
// Room
// ---------------------------------------------------------------------------
struct RoomCreateRequest {
    std::string room_id;
};

struct RoomCreateResponse {
    std::string room_id;
    std::string owner_id;
};

struct RoomJoinRequest {
    std::string room_id;
};

struct RoomJoinResponse {
    std::string room_id;
};

struct RoomLeaveRequest {
    std::string room_id;
};

struct RoomLeaveResponse {
    std::string room_id;
};

struct RoomReadyRequest {
    bool ready = false;
};

struct RoomReadyResponse {
    std::string user_id;
    bool ready = false;
};

struct RoomStatePush {
    std::string room_id;
    std::string owner_id;
    std::vector<std::string> member_ids;
    std::vector<std::string> ready_ids;
};

// ---------------------------------------------------------------------------
// Battle
// ---------------------------------------------------------------------------
struct BattleStartRequest {
    std::string room_id;
};

struct BattleStartResponse {
    std::string battle_id;
    std::string room_id;
};

struct BattleInputRequest {
    std::string input_data;
};

struct BattleInputResponse {
    std::uint64_t input_seq = 0;
};

struct BattleInputPush {
    std::string user_id;
    std::uint64_t input_seq = 0;
    std::string input_data;
};

struct BattleStatePush {
    std::string battle_id;
    std::string room_id;
    std::string state;
};

}  // namespace net::msg
