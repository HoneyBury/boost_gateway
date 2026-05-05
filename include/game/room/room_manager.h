#pragma once

#include "net/session.h"

#include <cstddef>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace game::room {

class RoomManager {
public:
    using SessionPtr = std::shared_ptr<net::Session>;

    struct RoomMember {
        SessionPtr session;
        bool ready = false;
    };

    struct RoomSnapshot {
        std::string room_id;
        SessionPtr owner;
        bool battle_started = false;
        std::vector<RoomMember> members;
    };

    enum class CreateRoomResult {
        kOk,
        kSessionNotFound,
        kInvalidRoomId,
        kRoomAlreadyExists,
    };

    enum class JoinRoomResult {
        kOk,
        kSessionNotFound,
        kInvalidRoomId,
        kRoomNotFound,
        kRoomInBattle,
    };

    enum class LeaveRoomResult {
        kOk,
        kSessionNotFound,
        kNotInRoom,
    };

    enum class ReadyResult {
        kOk,
        kSessionNotFound,
        kNotInRoom,
        kRoomInBattle,
    };

    struct RoomActionOutcome {
        std::string room_id;
        std::size_t player_count = 0;
    };

    std::pair<CreateRoomResult, RoomActionOutcome> create_room(const SessionPtr& session, std::string room_id);
    std::pair<JoinRoomResult, RoomActionOutcome> join_room(const SessionPtr& session, std::string room_id);
    std::pair<LeaveRoomResult, RoomActionOutcome> leave_room(const SessionPtr& session);
    std::pair<ReadyResult, RoomActionOutcome> set_ready(const SessionPtr& session, bool ready);
    bool transfer_session(const SessionPtr& from_session, const SessionPtr& to_session);

    void remove_session(const SessionPtr& session);

    [[nodiscard]] std::optional<std::string> room_id_of(const SessionPtr& session) const;
    [[nodiscard]] std::optional<RoomSnapshot> room_snapshot_of(const SessionPtr& session) const;
    [[nodiscard]] std::optional<RoomSnapshot> room_snapshot(const std::string& room_id) const;
    [[nodiscard]] std::vector<SessionPtr> room_members(const std::string& room_id) const;
    [[nodiscard]] std::size_t room_count() const;
    [[nodiscard]] std::size_t member_count(const std::string& room_id) const;
    [[nodiscard]] bool mark_battle_started(const std::string& room_id);
    [[nodiscard]] bool battle_started(const std::string& room_id) const;

    // COW snapshot: snapshot member list under lock, then invoke callback outside lock.
    template <typename F>
    void broadcast_to_room(const std::string& room_id, F&& callback) const {
        std::vector<SessionPtr> members;
        {
            std::scoped_lock lock(mutex_);
            const auto it = rooms_.find(room_id);
            if (it == rooms_.end()) return;
            members.reserve(it->second.members.size());
            for (const auto& [key, member] : it->second.members) {
                members.push_back(member.session);
            }
        }
        for (const auto& session : members) {
            callback(session);
        }
    }

private:
    using SessionKey = const net::Session*;

    struct RoomState {
        SessionPtr owner;
        bool battle_started = false;
        std::unordered_map<SessionKey, RoomMember> members;
    };

    void remove_from_room_unlocked(SessionKey session_key);
    [[nodiscard]] std::optional<RoomSnapshot> snapshot_unlocked(const std::string& room_id) const;

    mutable std::mutex mutex_;
    std::unordered_map<std::string, RoomState> rooms_;
    std::unordered_map<SessionKey, std::string> session_rooms_;
};

}  // namespace game::room
