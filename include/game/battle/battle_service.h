#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/battle/battle_manager.h"
#include "game/room/room_manager.h"
#include "net/message_dispatcher.h"

#include <string>

namespace game::battle {

class BattleService {
public:
    BattleService(gateway::SessionManager& session_manager,
                  room::RoomManager& room_manager,
                  BattleManager& battle_manager,
                  gateway::GatewayMetrics& metrics);

    void register_handlers(net::MessageDispatcher& dispatcher) const;

private:
    void broadcast_to_room(const std::string& room_id,
                           std::uint16_t message_id,
                           std::string body,
                           const std::shared_ptr<net::Session>& exclude_session = {}) const;

    gateway::SessionManager& session_manager_;
    room::RoomManager& room_manager_;
    BattleManager& battle_manager_;
    gateway::GatewayMetrics& metrics_;
};

}  // namespace game::battle
