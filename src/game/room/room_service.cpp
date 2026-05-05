#include "game/room/room_service.h"

#include "net/protocol.h"

namespace game::room {

void RoomService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kRoomJoinRequest,
        [](const std::shared_ptr<net::Session>& session, std::string body) {
            session->send(net::protocol::kRoomJoinResponse, "room_joined:" + body);
        });
}

}  // namespace game::room
