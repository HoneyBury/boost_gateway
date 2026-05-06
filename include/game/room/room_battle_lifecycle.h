#pragma once

#include <string>

namespace game::battle {
class BattleManager;
}

namespace game::room {
class RoomManager;

/// v1.1.7 / T08：当 `room_id` 在 `RoomManager` 中已无成员时，移除 `BattleManager` 中对应战斗上下文。
/// **唯一策略入口**：`GatewayServer` 断线清理与 `RoomService` 主动离队应均调用此函数，避免双写分叉。
void clear_battle_if_room_empty(game::battle::BattleManager& battles,
                                const RoomManager& rooms,
                                const std::string& room_id);

}  // namespace game::room
