#include "game/room/room_manager.h"

#include <utility>

namespace game::room {

RoomManager::JoinRoomOutcome RoomManager::join_room(const SessionPtr& session, std::string room_id) {
    std::scoped_lock lock(mutex_);

    const auto key = session.get();
    if (key == nullptr) {
        return {};
    }

    if (room_id.empty()) {
        return {JoinRoomResult::kInvalidRoomId, "", 0};
    }

    const auto existing_room_it = rooms_.find(room_id);
    if (existing_room_it != rooms_.end() && existing_room_it->second.battle_started) {
        return {JoinRoomResult::kRoomInBattle, room_id, existing_room_it->second.members.size()};
    }

    remove_from_room_unlocked(key);

    auto& room = rooms_[room_id];
    room.members.insert(key);
    session_rooms_[key] = room_id;
    return {JoinRoomResult::kOk, std::move(room_id), room.members.size()};
}

void RoomManager::remove_session(const SessionPtr& session) {
    std::scoped_lock lock(mutex_);
    remove_from_room_unlocked(session.get());
}

std::optional<std::string> RoomManager::room_id_of(const SessionPtr& session) const {
    std::scoped_lock lock(mutex_);
    const auto it = session_rooms_.find(session.get());
    if (it == session_rooms_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::size_t RoomManager::room_count() const {
    std::scoped_lock lock(mutex_);
    return rooms_.size();
}

std::size_t RoomManager::member_count(const std::string& room_id) const {
    std::scoped_lock lock(mutex_);
    const auto it = rooms_.find(room_id);
    if (it == rooms_.end()) {
        return 0;
    }
    return it->second.members.size();
}

bool RoomManager::battle_started(const std::string& room_id) const {
    std::scoped_lock lock(mutex_);
    const auto it = rooms_.find(room_id);
    return it != rooms_.end() && it->second.battle_started;
}

bool RoomManager::mark_battle_started(const std::string& room_id) {
    std::scoped_lock lock(mutex_);
    const auto it = rooms_.find(room_id);
    if (it == rooms_.end() || it->second.battle_started) {
        return false;
    }

    it->second.battle_started = true;
    return true;
}

void RoomManager::remove_from_room_unlocked(SessionKey session_key) {
    const auto session_room_it = session_rooms_.find(session_key);
    if (session_room_it == session_rooms_.end()) {
        return;
    }

    const auto room_id = session_room_it->second;
    const auto room_it = rooms_.find(room_id);
    if (room_it != rooms_.end()) {
        room_it->second.members.erase(session_key);
        if (room_it->second.members.empty()) {
            rooms_.erase(room_it);
        }
    }

    session_rooms_.erase(session_room_it);
}

}  // namespace game::room
