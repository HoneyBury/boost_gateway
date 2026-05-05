#include "game/battle/battle_manager.h"

namespace game::battle {

BattleManager::StartBattleOutcome BattleManager::start_battle(const std::string& room_id,
                                                              std::size_t player_count) {
    std::scoped_lock lock(mutex_);

    if (room_id.empty()) {
        return {StartBattleResult::kNotInRoom, "", 0};
    }

    if (player_count < 2) {
        return {StartBattleResult::kNotEnoughPlayers, room_id, player_count};
    }

    if (active_rooms_.contains(room_id)) {
        return {StartBattleResult::kAlreadyStarted, room_id, player_count};
    }

    active_rooms_.insert(room_id);
    return {StartBattleResult::kOk, room_id, player_count};
}

void BattleManager::remove_room(const std::string& room_id) {
    std::scoped_lock lock(mutex_);
    active_rooms_.erase(room_id);
}

bool BattleManager::battle_started(const std::string& room_id) const {
    std::scoped_lock lock(mutex_);
    return active_rooms_.contains(room_id);
}

std::size_t BattleManager::active_battle_count() const {
    std::scoped_lock lock(mutex_);
    return active_rooms_.size();
}

}  // namespace game::battle
