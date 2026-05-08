#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <variant>

namespace v2::player {

using SessionId = std::uint64_t;
using ConnectionId = std::uint64_t;

enum class PlayerLifecycleState : std::uint8_t {
    kOffline = 0,
    kAuthenticating = 1,
    kOnlineIdle = 2,
    kInRoom = 3,
    kInBattle = 4,
    kSuspended = 5,
};

struct PlayerSessionBinding {
    SessionId session_id = 0;
    ConnectionId connection_id = 0;
    std::uint64_t bound_at = 0;
};

struct PlayerRuntimeState {
    PlayerLifecycleState lifecycle = PlayerLifecycleState::kOffline;
    std::string user_id;
    std::string display_name;
    std::optional<PlayerSessionBinding> binding;
    std::optional<std::uint64_t> room_actor_id;
    std::optional<std::string> room_id;
};

struct LoginRequestMsg {
    SessionId session_id = 0;
    std::string user_id;
    std::string token;
    std::optional<std::string> display_name;
};

struct BindSessionMsg {
    SessionId session_id = 0;
    ConnectionId connection_id = 0;
};

struct RoomAssignedMsg {
    std::uint64_t room_actor_id = 0;
    std::string room_id;
};

struct SessionClosedMsg {
    SessionId session_id = 0;
};

struct LoginAcceptedMsg {
    SessionId session_id = 0;
    std::string user_id;
    std::string display_name;
    std::optional<std::string> room_id;
};

struct SessionKickPushMsg {
    SessionId old_session_id = 0;
    SessionId new_session_id = 0;
};

struct SessionResumePushMsg {
    SessionId session_id = 0;
    std::string room_id;
    bool in_battle = false;
};

using PlayerEvent = std::variant<LoginAcceptedMsg, SessionKickPushMsg, SessionResumePushMsg>;

}  // namespace v2::player
