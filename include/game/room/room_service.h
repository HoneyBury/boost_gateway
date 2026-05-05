#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/battle/battle_manager.h"
#include "game/room/room_manager.h"
#include "net/message_dispatcher.h"

#include <string>

namespace game::room {

class RoomService {
public:
    RoomService(gateway::SessionManager& session_manager,
                battle::BattleManager& battle_manager,
                RoomManager& room_manager,
                gateway::GatewayMetrics& metrics);

    void register_handlers(net::MessageDispatcher& dispatcher) const;

private:
    [[nodiscard]] std::string build_room_state_body(const RoomManager::RoomSnapshot& room_snapshot) const;
    void broadcast_room_state(const std::string& room_id,
                              std::uint16_t message_id,
                              const std::shared_ptr<net::Session>& exclude_session = {}) const;

    gateway::SessionManager& session_manager_;
    battle::BattleManager& battle_manager_;
    RoomManager& room_manager_;
    gateway::GatewayMetrics& metrics_;
};

}  // namespace game::room
