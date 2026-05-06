#include "game/room/room_service.h"

#include "game/room/room_battle_lifecycle.h"
#include "net/protocol.h"

#include <fmt/format.h>

namespace game::room {

namespace {

std::pair<std::string, std::optional<std::string>> split_once(const std::string& body) {
    const auto separator = body.find('|');
    if (separator == std::string::npos) {
        return {body, std::nullopt};
    }

    return {body.substr(0, separator), body.substr(separator + 1)};
}

}  // namespace

RoomService::RoomService(gateway::SessionManager& session_manager,
                         gateway::PushService& push_service,
                         battle::BattleManager& battle_manager,
                         RoomManager& room_manager,
                         gateway::GatewayMetrics& metrics)
    : session_manager_(session_manager),
      push_service_(push_service),
      battle_manager_(battle_manager),
      room_manager_(room_manager),
      metrics_(metrics) {}

void RoomService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kRoomCreateRequest,
        [this](const net::DispatchContext& context) {
            if (!session_manager_.is_authenticated(context.session)) {
                push_service_.send_error(
                    context.session, context.request_id, net::protocol::ErrorCode::kAuthRequired);
                return;
            }

            const auto [result, outcome] = room_manager_.create_room(context.session, context.body);
            switch (result) {
                case RoomManager::CreateRoomResult::kOk:
                    push_service_.send_ok(
                        context.session, net::protocol::kRoomCreateResponse, context.request_id, "room_created:" + outcome.room_id);
                    broadcast_room_state(outcome.room_id, net::protocol::kRoomStatePush, context.session);
                    return;

                case RoomManager::CreateRoomResult::kInvalidRoomId:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kInvalidRoomId);
                    return;

                case RoomManager::CreateRoomResult::kRoomAlreadyExists:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kRoomAlreadyExists);
                    return;

                case RoomManager::CreateRoomResult::kSessionNotFound:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kSessionNotFound);
                    return;
            }
        });

    dispatcher.register_handler(
        net::protocol::kRoomJoinRequest,
        [this](const net::DispatchContext& context) {
            if (!session_manager_.is_authenticated(context.session)) {
                push_service_.send_error(
                    context.session, context.request_id, net::protocol::ErrorCode::kAuthRequired);
                return;
            }

            const auto [result, outcome] = room_manager_.join_room(context.session, context.body);
            switch (result) {
                case RoomManager::JoinRoomResult::kOk:
                    metrics_.on_room_join_success();
                    push_service_.send_ok(context.session,
                                          net::protocol::kRoomJoinResponse,
                                          context.request_id,
                                          "room_joined:" + outcome.room_id + ":" +
                                              std::to_string(outcome.player_count));
                    broadcast_room_state(outcome.room_id, net::protocol::kRoomStatePush, context.session);
                    return;

                case RoomManager::JoinRoomResult::kInvalidRoomId:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kInvalidRoomId);
                    return;

                case RoomManager::JoinRoomResult::kRoomNotFound:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kRoomNotFound);
                    return;

                case RoomManager::JoinRoomResult::kRoomInBattle:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kRoomInBattle);
                    return;

                case RoomManager::JoinRoomResult::kSessionNotFound:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kSessionNotFound);
                    return;
            }
        });

    dispatcher.register_handler(
        net::protocol::kRoomLeaveRequest,
        [this](const net::DispatchContext& context) {
            const auto current_room_id = room_manager_.room_id_of(context.session);
            const auto [result, outcome] = room_manager_.leave_room(context.session);
            switch (result) {
                case RoomManager::LeaveRoomResult::kOk:
                    clear_battle_if_room_empty(battle_manager_, room_manager_, outcome.room_id);
                    push_service_.send_ok(
                        context.session, net::protocol::kRoomLeaveResponse, context.request_id, "room_left:" + outcome.room_id);
                    if (current_room_id) {
                        broadcast_room_state(*current_room_id,
                                             net::protocol::kRoomStatePush,
                                             context.session);
                    }
                    return;

                case RoomManager::LeaveRoomResult::kNotInRoom:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kNotInRoom);
                    return;

                case RoomManager::LeaveRoomResult::kSessionNotFound:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kSessionNotFound);
                    return;
            }
        });

    dispatcher.register_handler(
        net::protocol::kRoomReadyRequest,
        [this](const net::DispatchContext& context) {
            const auto [action, value] = split_once(context.body);
            const bool ready = action == "1" || action == "true" || value == "1" || value == "true";

            const auto [result, outcome] = room_manager_.set_ready(context.session, ready);
            switch (result) {
                case RoomManager::ReadyResult::kOk:
                    push_service_.send_ok(context.session,
                                          net::protocol::kRoomReadyResponse,
                                          context.request_id,
                                          ready ? "room_ready:on" : "room_ready:off");
                    broadcast_room_state(outcome.room_id, net::protocol::kRoomStatePush, context.session);
                    return;

                case RoomManager::ReadyResult::kNotInRoom:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kNotInRoom);
                    return;

                case RoomManager::ReadyResult::kRoomInBattle:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kRoomInBattle);
                    return;

                case RoomManager::ReadyResult::kSessionNotFound:
                    push_service_.send_error(
                        context.session, context.request_id, net::protocol::ErrorCode::kSessionNotFound);
                    return;
            }
        });
}

std::string RoomService::build_room_state_body(const RoomManager::RoomSnapshot& room_snapshot) const {
    std::string members;
    bool first = true;
    for (const auto& member : room_snapshot.members) {
        const auto login_context = session_manager_.login_context_of(member.session);
        const auto user_id = login_context ? login_context->user_id : "unknown";
        if (!first) {
            members += ';';
        }
        members += fmt::format("{}:{}", user_id, member.ready ? 1 : 0);
        first = false;
    }

    const auto owner_context = room_snapshot.owner ? session_manager_.login_context_of(room_snapshot.owner)
                                                   : std::optional<gateway::SessionManager::LoginContext>{};
    const auto owner_user_id = owner_context ? owner_context->user_id : "unknown";

    return fmt::format("room_state:{}:owner={}:battle={}:members={}",
                       room_snapshot.room_id,
                       owner_user_id,
                       room_snapshot.battle_started ? 1 : 0,
                       members);
}

void RoomService::broadcast_room_state(const std::string& room_id,
                                       std::uint16_t message_id,
                                       const std::shared_ptr<net::Session>& exclude_session) const {
    const auto room_snapshot = room_manager_.room_snapshot(room_id);
    if (!room_snapshot) {
        return;
    }

    const auto state_body = build_room_state_body(*room_snapshot);
    for (const auto& member : room_snapshot->members) {
        if (exclude_session && member.session.get() == exclude_session.get()) {
            continue;
        }

        push_service_.send_push(member.session, message_id, state_body);
    }
}

}  // namespace game::room
