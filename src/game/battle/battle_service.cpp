#include "game/battle/battle_service.h"

#include "net/protocol.h"

#include <fmt/format.h>

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

            const auto room_snapshot = room_manager_.room_snapshot_of(context.session);
            if (!room_snapshot) {
                context.session->send(net::protocol::kErrorResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                                      net::protocol::to_string(net::protocol::ErrorCode::kNotInRoom));
                return;
            }

            if (!room_snapshot->owner || room_snapshot->owner.get() != context.session.get()) {
                context.session->send(net::protocol::kErrorResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kNotRoomOwner),
                                      net::protocol::to_string(net::protocol::ErrorCode::kNotRoomOwner));
                return;
            }

            std::vector<std::string> player_ids;
            player_ids.reserve(room_snapshot->members.size());
            for (const auto& member : room_snapshot->members) {
                const auto login_context = session_manager_.login_context_of(member.session);
                if (!login_context) {
                    continue;
                }

                if (!member.ready) {
                    context.session->send(net::protocol::kErrorResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kNotAllReady),
                                          net::protocol::to_string(net::protocol::ErrorCode::kNotAllReady));
                    return;
                }

                player_ids.push_back(login_context->user_id);
            }

            const auto outcome = battle_manager_.start_battle(room_snapshot->room_id, std::move(player_ids));
            switch (outcome.result) {
                case BattleManager::StartBattleResult::kOk: {
                    const auto marked = room_manager_.mark_battle_started(outcome.room_id);
                    (void)marked;
                    metrics_.on_battle_start_success();
                    context.session->send(net::protocol::kBattleStartResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                          fmt::format("battle_started:{}:{}", outcome.room_id, outcome.player_count));
                    broadcast_to_room(outcome.room_id,
                                      net::protocol::kBattleStatePush,
                                      fmt::format("battle_state:started:{}:{}", outcome.room_id, outcome.player_count),
                                      context.session);
                    return;
                }

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

    dispatcher.register_handler(
        net::protocol::kBattleInputRequest,
        [this](const net::DispatchContext& context) {
            const auto room_id = room_manager_.room_id_of(context.session);
            const auto login_context = session_manager_.login_context_of(context.session);
            if (!room_id || !login_context) {
                context.session->send(net::protocol::kErrorResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                                      net::protocol::to_string(net::protocol::ErrorCode::kNotInRoom));
                return;
            }

            const auto outcome =
                battle_manager_.submit_input(*room_id, login_context->user_id, context.body);
            switch (outcome.result) {
                case BattleManager::SubmitInputResult::kOk:
                    context.session->send(net::protocol::kBattleInputResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                          fmt::format("battle_input_accepted:{}:{}", outcome.room_id, outcome.input.sequence));
                    broadcast_to_room(outcome.room_id,
                                      net::protocol::kBattleInputPush,
                                      fmt::format("battle_input:{}:{}:{}:{}",
                                                  outcome.room_id,
                                                  outcome.input.user_id,
                                                  outcome.input.sequence,
                                                  outcome.input.payload),
                                      context.session);
                    return;

                case BattleManager::SubmitInputResult::kBattleNotStarted:
                    context.session->send(
                        net::protocol::kErrorResponse,
                        context.request_id,
                        static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                        net::protocol::to_string(net::protocol::ErrorCode::kBattleNotStarted));
                    return;

                case BattleManager::SubmitInputResult::kPlayerNotInBattle:
                    context.session->send(net::protocol::kErrorResponse,
                                          context.request_id,
                                          static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                                          "player_not_in_battle");
                    return;
            }
        });
}

void BattleService::broadcast_to_room(const std::string& room_id,
                                      std::uint16_t message_id,
                                      std::string body,
                                      const std::shared_ptr<net::Session>& exclude_session) const {
    for (const auto& member : room_manager_.room_members(room_id)) {
        if (exclude_session && member.get() == exclude_session.get()) {
            continue;
        }

        member->send(message_id,
                     0,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     body);
    }
}

}  // namespace game::battle
