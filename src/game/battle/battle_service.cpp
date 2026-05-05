#include "game/battle/battle_service.h"

#include "net/protocol.h"

namespace game::battle {

BattleService::BattleService(gateway::SessionManager& session_manager,
                             room::RoomManager& room_manager,
                             BattleManager& battle_manager,
                             gateway::GatewayMetrics& metrics)
    : session_manager_(session_manager),
      room_manager_(room_manager),
      battle_manager_(battle_manager),
      metrics_(metrics) {}

void BattleService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kBattleStartRequest,
        [this](const net::DispatchContext& context) {
            if (!session_manager_.is_authenticated(context.session)) {
                context.session->send(net::protocol::kErrorResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                                      net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
                return;
            }

            const auto room_id = room_manager_.room_id_of(context.session);
            if (!room_id) {
                context.session->send(net::protocol::kErrorResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                                      net::protocol::to_string(net::protocol::ErrorCode::kNotInRoom));
                return;
            }

            const auto outcome =
                battle_manager_.start_battle(*room_id, room_manager_.member_count(*room_id));
            switch (outcome.result) {
                case BattleManager::StartBattleResult::kOk:
                    room_manager_.mark_battle_started(outcome.room_id);
                    metrics_.on_battle_start_success();
                    context.session->send(net::protocol::kBattleStartResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                          "battle_started:" + outcome.room_id + ":" +
                                              std::to_string(outcome.player_count));
                    return;

                case BattleManager::StartBattleResult::kNotEnoughPlayers:
                    context.session->send(
                        net::protocol::kErrorResponse,
                        context.request_id,
                        static_cast<std::int32_t>(net::protocol::ErrorCode::kNotEnoughPlayers),
                        net::protocol::to_string(net::protocol::ErrorCode::kNotEnoughPlayers));
                    return;

                case BattleManager::StartBattleResult::kAlreadyStarted:
                    context.session->send(
                        net::protocol::kErrorResponse,
                        context.request_id,
                        static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleAlreadyStarted),
                        net::protocol::to_string(net::protocol::ErrorCode::kBattleAlreadyStarted));
                    return;

                case BattleManager::StartBattleResult::kNotInRoom:
                    context.session->send(net::protocol::kErrorResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                                          net::protocol::to_string(net::protocol::ErrorCode::kNotInRoom));
                    return;
            }
        });
}

}  // namespace game::battle
