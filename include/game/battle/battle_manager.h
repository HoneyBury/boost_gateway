#pragma once

#include <cstddef>
#include <mutex>
#include <string>
#include <unordered_set>

namespace game::battle {

class BattleManager {
public:
    enum class StartBattleResult {
        kOk,
        kNotInRoom,
        kAlreadyStarted,
        kNotEnoughPlayers,
    };

    struct StartBattleOutcome {
        StartBattleResult result = StartBattleResult::kNotInRoom;
        std::string room_id;
        std::size_t player_count = 0;
    };

    StartBattleOutcome start_battle(const std::string& room_id, std::size_t player_count);
    void remove_room(const std::string& room_id);
    [[nodiscard]] bool battle_started(const std::string& room_id) const;
    [[nodiscard]] std::size_t active_battle_count() const;

private:
    mutable std::mutex mutex_;
    std::unordered_set<std::string> active_rooms_;
};

}  // namespace game::battle
