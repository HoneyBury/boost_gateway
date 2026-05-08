#pragma once

#include <cstdint>
#include <string>
#include <variant>
#include <vector>

namespace v2::battle {

struct BattleRuntimeState {
    std::string battle_id;
    std::string room_id;
    std::vector<std::string> player_ids;
    bool started = false;
};

struct CreateBattleMsg {
    std::string battle_id;
    std::string room_id;
    std::vector<std::string> player_ids;
};

struct BattleCreatedMsg {
    std::string battle_id;
    std::string room_id;
    std::vector<std::string> player_ids;
};

using BattleEvent = std::variant<BattleCreatedMsg>;

}  // namespace v2::battle
