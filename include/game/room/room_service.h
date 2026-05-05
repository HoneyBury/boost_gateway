#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"
#include "net/message_dispatcher.h"

namespace game::room {

class RoomService {
public:
    RoomService(gateway::SessionManager& session_manager,
                RoomManager& room_manager,
                gateway::GatewayMetrics& metrics);

    void register_handlers(net::MessageDispatcher& dispatcher) const;

private:
    gateway::SessionManager& session_manager_;
    RoomManager& room_manager_;
    gateway::GatewayMetrics& metrics_;
};

}  // namespace game::room
