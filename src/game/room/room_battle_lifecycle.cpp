#include "game/room/room_battle_lifecycle.h"

#include "game/battle/battle_manager.h"
#include "game/room/room_manager.h"

namespace game::room {

void clear_battle_if_room_empty(game::battle::BattleManager& battles,
                                const RoomManager& rooms,
                                const std::string& room_id) {
    if (rooms.member_count(room_id) == 0) {
        battles.remove_room(room_id);
    }
}

}  // namespace game::room
