#pragma once

#include <cstdint>
#include <map>
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
    std::optional<std::uint64_t> battle_actor_id;
    std::optional<std::string> battle_id;
    std::optional<std::string> pending_battle_settlement_reason;
};

struct TokenMeta {
    std::string token_type;
    std::string issuer;
    std::uint64_t issued_at = 0;
    std::uint64_t expires_at = 0;
    std::map<std::string, std::string> claims;
};

struct ResumeMeta {
    std::string reconnect_token;
    std::string room_id;
    std::optional<std::string> battle_id;
    std::uint64_t expiry = 0;
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

struct BattleAssignedMsg {
    std::uint64_t battle_actor_id = 0;
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

struct BattleSettlementAppliedMsg {
    std::string battle_id;
    std::string reason;
};

struct ReconnectTimerExpiredMsg {
    std::string user_id;
};

struct TokenRefreshMsg {
    SessionId session_id = 0;
    std::string user_id;
};

struct TokenRefreshedMsg {
    SessionId session_id = 0;
    std::string user_id;
    std::string new_token;
    std::string refresh_token;
    std::uint64_t expires_at = 0;
};

using PlayerEvent = std::variant<LoginAcceptedMsg,
                                 SessionKickPushMsg,
                                 SessionResumePushMsg,
                                 BattleSettlementAppliedMsg,
                                 TokenRefreshedMsg>;

}  // namespace v2::player
