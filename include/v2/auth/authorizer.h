#pragma once
// v2.2.0 Message-level RBAC Authorizer

#include <cstdint>
#include <string>
#include <unordered_set>

namespace v2::auth {

enum class Role : std::uint8_t {
    kPlayer = 0,
    kAdmin = 1,
    kObserver = 2,
};

inline auto role_from_string(const std::string& s) -> Role {
    if (s == "admin") return Role::kAdmin;
    if (s == "observer") return Role::kObserver;
    return Role::kPlayer;  // default
}

inline auto to_string(Role role) -> const char* {
    switch (role) {
        case Role::kAdmin: return "admin";
        case Role::kObserver: return "observer";
        case Role::kPlayer: return "player";
    }
    return "player";
}

// ── Authorizer ───────────────────────────────────────────────────────────

class Authorizer {
public:
    Authorizer() { init_defaults(); }

    /// Check if a role is allowed to send a specific protocol message.
    [[nodiscard]] bool is_allowed(Role role, std::uint16_t protocol_message_id) const;

    /// Grant a specific message to a role.
    void allow(Role role, std::uint16_t msg_id) {
        rules_[static_cast<int>(role)].insert(msg_id);
    }

    /// Revoke a specific message from a role.
    void deny(Role role, std::uint16_t msg_id) {
        rules_[static_cast<int>(role)].erase(msg_id);
    }

    [[nodiscard]] static Authorizer& instance() {
        static Authorizer auth;
        return auth;
    }

private:
    void init_defaults();

    // One set per role: allowed protocol message IDs
    std::unordered_set<std::uint16_t> rules_[3];  // player, admin, observer
};

// ── Default rules ────────────────────────────────────────────────────────
// Admin messages (5001-5005) are admin-only.
// All standard game messages are allowed for players.
// Observers can only send heartbeat and echo.
inline void Authorizer::init_defaults() {
    // Player: all game messages
    auto& p = rules_[static_cast<int>(Role::kPlayer)];
    p = {
        1,     // kHeartbeatRequest
        1001,  // kEchoRequest
        2001,  // kLoginRequest
        2003,  // kRegisterRequest
        3001,  // kRoomCreateRequest
        3003,  // kRoomJoinRequest
        3005,  // kRoomLeaveRequest
        3007,  // kRoomReadyRequest
        3010,  // kRoomListRequest
        3012,  // kRoomDetailRequest
        3014,  // kRoomKickRequest
        3016,  // kRoomTransferOwnerRequest
        4001,  // kBattleStartRequest
        4003,  // kBattleInputRequest
        4007,  // kBattleStateRequest
        4009,  // kReplayLoadRequest
        6001,  // kMatchJoinRequest
        6004,  // kMatchLeaveRequest
        6006,  // kMatchStatusRequest
        7001,  // kLeaderboardSubmitRequest
        7003,  // kLeaderboardTopRequest
        7005,  // kLeaderboardRankRequest
    };

    // Admin: all messages + admin commands
    auto& a = rules_[static_cast<int>(Role::kAdmin)];
    a = p;
    a.insert({5001, 5002, 5003, 5004});  // admin commands

    // Observer: read-only
    auto& o = rules_[static_cast<int>(Role::kObserver)];
    o = {1, 1001, 2001};  // heartbeat, echo, login only
}

inline bool Authorizer::is_allowed(Role role,
                                   std::uint16_t protocol_message_id) const {
    const auto& set = rules_[static_cast<int>(role)];
    return set.count(protocol_message_id) > 0;
}

}  // namespace v2::auth
