#include "game/room/room_service.h"

#include "net/protocol.h"

namespace game::room {

RoomService::RoomService(gateway::SessionManager& session_manager,
                         RoomManager& room_manager,
                         gateway::GatewayMetrics& metrics)
    : session_manager_(session_manager), room_manager_(room_manager), metrics_(metrics) {}

void RoomService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kRoomJoinRequest,
        [this](const net::DispatchContext& context) {
            if (!session_manager_.is_authenticated(context.session)) {
                context.session->send(net::protocol::kErrorResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                                      net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
                return;
            }

            const auto outcome = room_manager_.join_room(context.session, context.body);
            switch (outcome.result) {
                case RoomManager::JoinRoomResult::kOk:
                    metrics_.on_room_join_success();
                    context.session->send(net::protocol::kRoomJoinResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                          "room_joined:" + outcome.room_id + ":" +
                                              std::to_string(outcome.player_count));
                    return;

                case RoomManager::JoinRoomResult::kInvalidRoomId:
                    context.session->send(
                        net::protocol::kErrorResponse,
                        context.request_id,
                        static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                        net::protocol::to_string(net::protocol::ErrorCode::kInvalidRoomId));
                    return;

                case RoomManager::JoinRoomResult::kRoomInBattle:
                    context.session->send(
                        net::protocol::kErrorResponse,
                        context.request_id,
                        static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomInBattle),
                        net::protocol::to_string(net::protocol::ErrorCode::kRoomInBattle));
                    return;

                case RoomManager::JoinRoomResult::kSessionNotFound:
                    context.session->send(
                        net::protocol::kErrorResponse,
                        context.request_id,
                        static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                        net::protocol::to_string(net::protocol::ErrorCode::kSessionNotFound));
                    return;
            }
        });
}

}  // namespace game::room
