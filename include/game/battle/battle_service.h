#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/battle/battle_manager.h"
#include "game/room/room_manager.h"
#include "net/message_dispatcher.h"

namespace game::battle {

class BattleService {
public:
    BattleService(gateway::SessionManager& session_manager,
                  room::RoomManager& room_manager,
                  BattleManager& battle_manager,
                  gateway::GatewayMetrics& metrics);

    void register_handlers(net::MessageDispatcher& dispatcher) const;

private:
    gateway::SessionManager& session_manager_;
    room::RoomManager& room_manager_;
    BattleManager& battle_manager_;
    gateway::GatewayMetrics& metrics_;
};

}  // namespace game::battle
