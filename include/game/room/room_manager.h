#pragma once

#include "net/session.h"

#include <cstddef>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>

namespace game::room {

class RoomManager {
public:
    using SessionPtr = std::shared_ptr<net::Session>;

    enum class JoinRoomResult {
        kOk,
        kSessionNotFound,
        kInvalidRoomId,
        kRoomInBattle,
    };

    struct JoinRoomOutcome {
        JoinRoomResult result = JoinRoomResult::kSessionNotFound;
        std::string room_id;
        std::size_t player_count = 0;
    };

    JoinRoomOutcome join_room(const SessionPtr& session, std::string room_id);
    void remove_session(const SessionPtr& session);
    [[nodiscard]] std::optional<std::string> room_id_of(const SessionPtr& session) const;
    [[nodiscard]] std::size_t room_count() const;
    [[nodiscard]] std::size_t member_count(const std::string& room_id) const;
    [[nodiscard]] bool battle_started(const std::string& room_id) const;
    bool mark_battle_started(const std::string& room_id);

private:
    using SessionKey = const net::Session*;

    struct RoomRecord {
        std::unordered_set<SessionKey> members;
        bool battle_started = false;
    };

    void remove_from_room_unlocked(SessionKey session_key);

    mutable std::mutex mutex_;
    std::unordered_map<std::string, RoomRecord> rooms_;
    std::unordered_map<SessionKey, std::string> session_rooms_;
};

}  // namespace game::room
