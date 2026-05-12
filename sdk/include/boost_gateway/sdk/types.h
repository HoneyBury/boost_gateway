#pragma once
// BoostGateway SDK: public types and callbacks for the client SDK.

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace boost_gateway {
namespace sdk {

using SessionId = std::uint64_t;
using RequestId = std::uint32_t;

// ── Push message from server ──────────────────────────────────────────

struct PushMessage {
    std::uint16_t message_id = 0;
    std::string body;
};

// ── Result types ──────────────────────────────────────────────────────

struct LoginResult {
    bool ok = false;
    std::int32_t error_code = 0;
    std::string error_message;
    std::string user_id;
    std::string display_name;
};

struct RoomResult {
    bool ok = false;
    std::int32_t error_code = 0;
    std::string error_message;
    std::string room_id;
    int member_count = 0;
};

struct BattleStartResult {
    bool ok = false;
    std::int32_t error_code = 0;
    std::string error_message;
    std::string battle_id;
};

struct BattleInputResult {
    bool ok = false;
    std::int32_t error_code = 0;
    std::string error_message;
    std::uint64_t input_seq = 0;
};

struct EchoResult {
    bool ok = false;
    std::string echo_body;
};

// ── Callback types ────────────────────────────────────────────────────

using PushCallback = std::function<void(const PushMessage&)>;
using DisconnectCallback = std::function<void()>;

}  // namespace sdk
}  // namespace boost_gateway
