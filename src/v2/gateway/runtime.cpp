#include "v2/gateway/runtime.h"

#include "net/protocol.h"
#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/battle_protocol_codec.h"
#include "v2/gateway/gateway_command_parser.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/service/error_codes.h"

#include <fmt/format.h>
#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include <chrono>
#include <utility>

namespace v2::gateway {

namespace {

std::string build_replay_payload(const v2::battle::BattleSettlementPreparedMsg& settlement) {
    nlohmann::json doc;
    doc["battle_id"] = settlement.battle_id;
    doc["room_id"] = settlement.room_id;
    doc["total_frames"] = settlement.total_frames;
    doc["reason"] = v2::battle::to_string(settlement.reason);
    doc["triggering_user_id"] = settlement.triggering_user_id;
    doc["participants"] = settlement.participant_user_ids;

    nlohmann::json frames = nlohmann::json::array();
    std::uint32_t current_frame = 0;
    nlohmann::json current_inputs = nlohmann::json::array();
    auto flush_frame = [&]() {
        if (current_frame == 0) {
            return;
        }
        frames.push_back({
            {"frame", current_frame},
            {"inputs", current_inputs},
        });
        current_inputs = nlohmann::json::array();
    };

    for (const auto& input : settlement.replay_inputs) {
        if (current_frame != input.frame_number) {
            flush_frame();
            current_frame = input.frame_number;
        }
        current_inputs.push_back({
            {"seq", input.input_seq},
            {"user_id", input.user_id},
            {"payload", input.input_data},
            {"trigger", input.trigger},
        });
    }
    flush_frame();
    doc["frames"] = std::move(frames);
    return doc.dump();
}

}  // namespace

v2::actor::ActorRef Runtime::create_gateway_actor() {
    return actor_system_.create_actor(std::make_unique<GatewayActor>(
        write_sink_,
        this,
        GatewayActor::RateLimitPolicy{},
        [this](const GatewayCommand& command) { return is_authenticated(command); }));
}

bool Runtime::is_authenticated(const GatewayCommand& command) const {
    return users_by_session_id_.contains(command.session_id);
}

void Runtime::on_session_closed(SessionId session_id) {
    const auto user_id = session_user_id(session_id);
    const auto room_it = rooms_by_session_id_.find(session_id);
    const auto room_id = room_it != rooms_by_session_id_.end() ? room_it->second : std::string{};

    if (!user_id.empty()) {
        auto player_it = players_by_user_id_.find(user_id);
        if (player_it != players_by_user_id_.end()) {
            v2::actor::Message closed;
            closed.header.kind = v2::actor::MessageKind::kUser;
            closed.payload = v2::player::SessionClosedMsg{.session_id = session_id};
            player_it->second.tell(std::move(closed));
        }
    }
    pending_battle_input_.erase(session_id);
    pending_battle_end_.erase(session_id);
    rooms_by_session_id_.erase(session_id);
    users_by_session_id_.erase(session_id);

    if (!user_id.empty() && !room_id.empty()) {
        auto battle_it = battles_by_room_id_.find(room_id);
        if (battle_it != battles_by_room_id_.end()) {
            v2::actor::Message disconnected;
            disconnected.header.kind = v2::actor::MessageKind::kUser;
            disconnected.payload = v2::battle::PlayerDisconnectedMsg{.user_id = user_id};
            battle_it->second.tell(std::move(disconnected));
        }
    }

    actor_system_.dispatch_all();
}

void Runtime::set_service_bridge(std::unique_ptr<GatewayServiceBridge> bridge) {
    bridge_ = std::move(bridge);
}

bool Runtime::handle(const GatewayCommand& command) {
    switch (command.type) {
        case GatewayCommandType::kLogin: {
            const auto login_body = parse_login_command_body(command.body);
            if (!login_body.has_value() || !validate_login_command_body(*login_body)) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidUserId));
                return true;
            }

            // Bridge path: delegate auth decision to login_backend
            if (bridge_) {
                nlohmann::json auth_payload{
                    {"user_id", login_body->user_id},
                    {"token", login_body->token},
                    {"display_name", login_body->display_name.value_or("")},
                };

                auto result = bridge_->route(v2::service::ServiceId::kLogin,
                                             "login_request",
                                             auth_payload.dump());

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        bool is_duplicate = resp.value("is_duplicate", false);

                        // Kick old session on duplicate login
                        if (is_duplicate) {
                            auto old_session = session_id_for_user(login_body->user_id);
                            if (old_session.has_value()) {
                                emit(net::protocol::kSessionKickedPush,
                                     *old_session,
                                     0,
                                     static_cast<std::int32_t>(net::protocol::ErrorCode::kDuplicateLogin),
                                     "duplicate_login");
                                users_by_session_id_.erase(*old_session);

                                auto player_it = players_by_user_id_.find(login_body->user_id);
                                if (player_it != players_by_user_id_.end()) {
                                    v2::actor::Message closed;
                                    closed.header.kind = v2::actor::MessageKind::kUser;
                                    closed.payload = v2::player::SessionClosedMsg{.session_id = *old_session};
                                    player_it->second.tell(std::move(closed));
                                    actor_system_.dispatch_all();
                                }
                            }
                        }

                        auto player = get_or_create_player(login_body->user_id);

                        v2::actor::Message bind;
                        bind.header.kind = v2::actor::MessageKind::kUser;
                        bind.payload = v2::player::BindSessionMsg{
                            .session_id = command.session_id,
                            .connection_id = command.session_id,
                        };
                        player.tell(std::move(bind));
                        actor_system_.dispatch_all();

                        users_by_session_id_[command.session_id] = login_body->user_id;
                        emit(net::protocol::kLoginResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             "login_ok:" + login_body->user_id);
                        return true;
                    }

                    // Backend responded but rejected the auth
                    std::string reason = resp.is_discarded() ? "auth_failed"
                        : resp.value("reason", "auth_failed");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken),
                         reason);
                    return true;
                }

                // Bridge routing failure (timeout, unavailable, etc.)
                auto net_error = net::protocol::ErrorCode::kSessionNotFound;
                if (result.error == v2::service::ServiceErrorCode::kRejected) {
                    net_error = net::protocol::ErrorCode::kInvalidToken;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net_error),
                     "backend_error");
                return true;
            }

            // Local path: no bridge configured, use PlayerActor auth
            pending_login_[command.session_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };

            auto player = get_or_create_player(login_body->user_id);

            v2::actor::Message bind;
            bind.header.kind = v2::actor::MessageKind::kUser;
            bind.payload = v2::player::BindSessionMsg{
                .session_id = command.session_id,
                .connection_id = command.session_id,
            };
            player.tell(std::move(bind));

            v2::actor::Message login;
            login.header.kind = v2::actor::MessageKind::kUser;
            login.payload = v2::player::LoginRequestMsg{
                .session_id = command.session_id,
                .user_id = login_body->user_id,
                .token = command.body,
                .display_name = login_body->display_name,
            };
            player.tell(std::move(login));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kRoomCreate: {
            const auto user_id = session_user_id(command.session_id);
            const auto room_id = parse_room_id_body(command.body);
            if (user_id.empty() || !room_id.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidRoomId));
                return true;
            }

            // Bridge path: delegate room state management to room_backend
            if (bridge_) {
                nlohmann::json room_payload{
                    {"user_id", user_id},
                    {"room_id", *room_id},
                };

                auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                             "room_create",
                                             room_payload.dump());

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        rooms_by_session_id_[command.session_id] = *room_id;
                        emit(net::protocol::kRoomCreateResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             *room_id);
                        return true;
                    }
                    std::string reason = resp.is_discarded() ? "room_create_failed"
                        : resp.value("reason", "room_create_failed");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                         reason);
                    return true;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "backend_error");
                return true;
            }

            // Local path: create RoomActor
            auto room_actor = actor_system_.create_actor(std::make_unique<v2::room::RoomActor>(*this));
            rooms_by_room_id_[*room_id] = room_actor;
            pending_room_create_[*room_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };

            v2::actor::Message create;
            create.header.kind = v2::actor::MessageKind::kUser;
            create.payload = v2::room::CreateRoomMsg{
                .room_id = *room_id,
                .owner_user_id = user_id,
                .owner_actor_id = players_by_user_id_.at(user_id).actor_id(),
            };
            room_actor.tell(std::move(create));

            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_actor.actor_id(),
                .room_id = *room_id,
            };
            players_by_user_id_.at(user_id).tell(std::move(assign));

            actor_system_.dispatch_all();
            rooms_by_session_id_[command.session_id] = *room_id;
            emit(net::protocol::kRoomCreateResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 *room_id);
            pending_room_create_.erase(*room_id);
            return true;
        }
        case GatewayCommandType::kRoomJoin: {
            const auto user_id = session_user_id(command.session_id);
            const auto room_id = parse_room_id_body(command.body);
            if (user_id.empty() || !room_id.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidRoomId));
                return true;
            }

            // Bridge path: delegate to room_backend
            if (bridge_) {
                nlohmann::json room_payload{
                    {"user_id", user_id},
                    {"room_id", *room_id},
                };

                auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                             "room_join",
                                             room_payload.dump());

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        rooms_by_session_id_[command.session_id] = *room_id;
                        emit(net::protocol::kRoomJoinResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             *room_id);
                        return true;
                    }
                    std::string reason = resp.is_discarded() ? "room_join_failed"
                        : resp.value("reason", "room_join_failed");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                         reason);
                    return true;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "backend_error");
                return true;
            }

            // Local path
            auto room_it = rooms_by_room_id_.find(*room_id);
            if (room_it == rooms_by_room_id_.end()) {
                return false;
            }
            pending_room_join_[*room_id + ":" + user_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };

            v2::actor::Message join;
            join.header.kind = v2::actor::MessageKind::kUser;
            join.payload = v2::room::JoinRoomMsg{
                .user_id = user_id,
                .player_actor_id = players_by_user_id_.at(user_id).actor_id(),
            };
            room_it->second.tell(std::move(join));

            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_it->second.actor_id(),
                .room_id = *room_id,
            };
            players_by_user_id_.at(user_id).tell(std::move(assign));

            actor_system_.dispatch_all();
            rooms_by_session_id_[command.session_id] = *room_id;
            emit(net::protocol::kRoomJoinResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 *room_id);
            pending_room_join_.erase(*room_id + ":" + user_id);
            return true;
        }
        case GatewayCommandType::kRoomReady: {
            const auto user_id = session_user_id(command.session_id);
            auto room_name_it = rooms_by_session_id_.find(command.session_id);
            if (user_id.empty() || room_name_it == rooms_by_session_id_.end()) {
                return false;
            }
            const auto ready = parse_room_ready_body(command.body);
            if (!ready.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_ready_state");
                return true;
            }

            // Bridge path: delegate to room_backend
            if (bridge_) {
                nlohmann::json room_payload{
                    {"user_id", user_id},
                    {"room_id", room_name_it->second},
                    {"ready", *ready},
                };

                auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                             "room_ready",
                                             room_payload.dump());

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        emit(net::protocol::kRoomReadyResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             *ready ? "true" : "false");
                        return true;
                    }
                    std::string reason = resp.is_discarded() ? "ready_failed"
                        : resp.value("reason", "ready_failed");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                         reason);
                    return true;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "backend_error");
                return true;
            }

            // Local path
            auto room_it = rooms_by_room_id_.find(room_name_it->second);
            if (room_it == rooms_by_room_id_.end()) {
                return false;
            }
            pending_room_ready_[room_name_it->second + ":" + user_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message set_ready;
            set_ready.header.kind = v2::actor::MessageKind::kUser;
            set_ready.payload = v2::room::SetReadyMsg{
                .user_id = user_id,
                .ready = *ready,
            };
            room_it->second.tell(std::move(set_ready));
            actor_system_.dispatch_all();
            emit(net::protocol::kRoomReadyResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 *ready ? "true" : "false");
            pending_room_ready_.erase(room_name_it->second + ":" + user_id);
            return true;
        }
        case GatewayCommandType::kBattleStart: {
            const auto user_id = session_user_id(command.session_id);
            auto room_name_it = rooms_by_session_id_.find(command.session_id);
            if (user_id.empty() || room_name_it == rooms_by_session_id_.end()) {
                return false;
            }
            const auto battle_start = parse_battle_start_command_body(command.body);
            if (!battle_start.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidRoomId));
                return true;
            }
            if (battle_start->room_id.has_value() && *battle_start->room_id != room_name_it->second) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidRoomId));
                return true;
            }

            // Bridge path: room_start_battle → cascade to battle_create
            if (bridge_) {
                nlohmann::json room_payload{
                    {"user_id", user_id},
                    {"room_id", room_name_it->second},
                };

                auto room_result = bridge_->route(v2::service::ServiceId::kRoom,
                                                  "room_start_battle",
                                                  room_payload.dump());

                if (!room_result.success) {
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                         "backend_error");
                    return true;
                }

                auto room_resp = nlohmann::json::parse(room_result.response_payload, nullptr, false);
                if (room_resp.is_discarded() || room_resp.value("status", "") != "ok") {
                    std::string reason = room_resp.is_discarded() ? "start_battle_failed"
                        : room_resp.value("reason", "start_battle_failed");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                         reason);
                    return true;
                }

                // Check for forward instruction to cascade to battle_backend
                if (room_resp.contains("forward")) {
                    const auto& fwd = room_resp["forward"];
                    std::string fwd_target = fwd.value("target", "");
                    std::string fwd_msg_type = fwd.value("message_type", "");
                    std::string fwd_payload = fwd.contains("payload")
                        ? fwd["payload"].dump() : "";

                    if (fwd_target == "battle" && !fwd_payload.empty()) {
                        auto battle_result = bridge_->route(
                            v2::service::ServiceId::kBattle,
                            fwd_msg_type,
                            fwd_payload);

                        if (battle_result.success) {
                            auto battle_resp = nlohmann::json::parse(
                                battle_result.response_payload, nullptr, false);

                            if (!battle_resp.is_discarded() &&
                                battle_resp.value("status", "") == "ok") {

                                // Track battle by room_id for push broadcasting
                                std::string battle_id = battle_resp.value("battle_id", "");
                                if (!battle_id.empty()) {
                                    battles_by_room_id_[room_name_it->second] =
                                        v2::actor::ActorRef{};  // placeholder for bridge mode
                                }

                                // Emit kBattleStartResponse to requester
                                emit(net::protocol::kBattleStartResponse,
                                     command.session_id,
                                     command.request_id,
                                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                     format_battle_started_body(
                                         room_name_it->second, battle_id));

                                // Broadcast push_to_sessions events
                                if (battle_resp.contains("push_to_sessions")) {
                                    for (const auto& push : battle_resp["push_to_sessions"]) {
                                        std::string kind = push.value("kind", "");
                                        if (kind == "battle_started") {
                                            for (const auto& [sid, rid] : rooms_by_session_id_) {
                                                if (rid == room_name_it->second) {
                                                    emit(net::protocol::kBattleStatePush,
                                                         sid, 0,
                                                         static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                                         format_battle_state_body(room_name_it->second, battle_id));
                                                }
                                            }
                                        }
                                    }
                                }
                                return true;
                            }
                        }
                        emit(net::protocol::kErrorResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                             "battle_create_failed");
                        return true;
                    }
                }

                // Room start succeeded but no forward (shouldn't normally happen)
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                     "no_battle_forward");
                return true;
            }

            // Local path
            auto room_it = rooms_by_room_id_.find(room_name_it->second);
            if (room_it == rooms_by_room_id_.end()) {
                return false;
            }
            pending_battle_start_[room_name_it->second] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message start;
            start.header.kind = v2::actor::MessageKind::kUser;
            start.payload = v2::room::StartBattleMsg{.requester_user_id = user_id};
            room_it->second.tell(std::move(start));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kBattleInput: {
            const auto user_id = session_user_id(command.session_id);
            auto room_name_it = rooms_by_session_id_.find(command.session_id);
            if (user_id.empty() || room_name_it == rooms_by_session_id_.end()) {
                return false;
            }
            const auto battle_input = parse_battle_input_command_body(command.body);
            if (!battle_input.has_value() || !validate_battle_input_command_body(*battle_input)) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_battle_input");
                return true;
            }

            // Bridge path: route to battle_backend
            if (bridge_) {
                auto battle_it = battles_by_room_id_.find(room_name_it->second);
                if (battle_it == battles_by_room_id_.end()) {
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                         net::protocol::to_string(net::protocol::ErrorCode::kBattleNotStarted));
                    return true;
                }

                if (battle_input->is_finish_request) {
                    nlohmann::json finish_payload{
                        {"user_id", user_id},
                        {"battle_id", ""},  // backend resolves by room
                        {"reason", v2::battle::to_string(battle_input->finish_reason)},
                    };

                    auto result = bridge_->route(v2::service::ServiceId::kBattle,
                                                 "battle_finish",
                                                 finish_payload.dump());

                    if (result.success) {
                        auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                        if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                            emit(net::protocol::kBattleInputResponse,
                                 command.session_id,
                                 command.request_id,
                                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                 format_battle_end_accepted_body(battle_input->finish_reason));

                            if (resp.contains("push_to_sessions")) {
                                for (const auto& push : resp["push_to_sessions"]) {
                                    std::string kind = push.value("kind", "");
                                    if (kind == "battle_finished") {
                                        for (const auto& [sid, rid] : rooms_by_session_id_) {
                                            if (rid == room_name_it->second) {
                                                emit(net::protocol::kBattleStatePush,
                                                     sid, 0,
                                                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                                     push.dump());
                                            }
                                        }
                                    }
                                }
                            }
                            battles_by_room_id_.erase(room_name_it->second);
                            return true;
                        }
                    }
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                         "battle_finish_failed");
                    return true;
                }

                // Normal input
                nlohmann::json input_payload{
                    {"user_id", user_id},
                    {"battle_id", ""},  // backend resolves by room
                    {"input_data", battle_input->input_data},
                    {"score", battle_input->score},
                    {"submitted_frame", battle_input->score},  // approximate
                };

                auto result = bridge_->route(v2::service::ServiceId::kBattle,
                                             "battle_input",
                                             input_payload.dump());

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        auto input_seq = resp.value("input_seq", std::uint64_t{0});
                        emit(net::protocol::kBattleInputResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             format_battle_input_response_body(input_seq));

                        if (resp.contains("push_to_sessions")) {
                            for (const auto& push : resp["push_to_sessions"]) {
                                std::string kind = push.value("kind", "");
                                if (kind == "frame_advanced") {
                                    for (const auto& [sid, rid] : rooms_by_session_id_) {
                                        if (rid == room_name_it->second) {
                                            emit(net::protocol::kBattleStatePush,
                                                 sid, 0,
                                                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                                 push.dump());
                                        }
                                    }
                                } else if (kind == "battle_finished") {
                                    for (const auto& [sid, rid] : rooms_by_session_id_) {
                                        if (rid == room_name_it->second) {
                                            emit(net::protocol::kBattleStatePush,
                                                 sid, 0,
                                                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                                 push.dump());
                                        }
                                    }
                                    battles_by_room_id_.erase(room_name_it->second);
                                }
                            }
                        }
                        return true;
                    }
                    std::string reason = resp.is_discarded() ? "input_rejected"
                        : resp.value("reason", "input_rejected");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                         reason);
                    return true;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "backend_error");
                return true;
            }

            // Local path
            auto battle_it = battles_by_room_id_.find(room_name_it->second);
            if (battle_it == battles_by_room_id_.end()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                     net::protocol::to_string(net::protocol::ErrorCode::kBattleNotStarted));
                return true;
            }
            if (battle_input->is_finish_request) {
                pending_battle_end_[command.session_id] = PendingResponse{
                    .session_id = command.session_id,
                    .request_id = command.request_id,
                };
                v2::actor::Message end;
                end.header.kind = v2::actor::MessageKind::kUser;
                end.payload = v2::battle::EndBattleMsg{
                    .reason = battle_input->finish_reason,
                    .triggering_user_id = user_id,
                };
                battle_it->second.tell(std::move(end));
                actor_system_.dispatch_all();
                return true;
            }
            pending_battle_input_[command.session_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message input;
            input.header.kind = v2::actor::MessageKind::kUser;
            input.payload = v2::battle::SubmitBattleInputMsg{
                .user_id = user_id,
                .request_id = command.request_id,
                .input_data = battle_input->input_data,
                .score = battle_input->score,
            };
            battle_it->second.tell(std::move(input));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kHeartbeat:
        case GatewayCommandType::kEcho:
        case GatewayCommandType::kUnknown:
            return false;
    }

    return false;
}

void Runtime::push(v2::player::PlayerEvent event) {
    if (const auto* accepted = std::get_if<v2::player::LoginAcceptedMsg>(&event)) {
        users_by_session_id_[accepted->session_id] = accepted->user_id;
        auto pending = pending_login_.find(accepted->session_id);
        if (pending != pending_login_.end()) {
            emit(net::protocol::kLoginResponse,
                 accepted->session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 "login_ok:" + accepted->user_id);
            pending_login_.erase(pending);
        }
        return;
    }

    if (const auto* kicked = std::get_if<v2::player::SessionKickPushMsg>(&event)) {
        emit(net::protocol::kSessionKickedPush,
             kicked->old_session_id,
             0,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kDuplicateLogin),
             "duplicate_login");
        return;
    }

    if (const auto* resumed = std::get_if<v2::player::SessionResumePushMsg>(&event)) {
        emit(net::protocol::kSessionResumedPush,
             resumed->session_id,
             0,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
             resumed->room_id);
    }

    if (const auto* applied = std::get_if<v2::player::BattleSettlementAppliedMsg>(&event)) {
        auto it = pending_settlement_acks_.find(applied->battle_id);
        if (it != pending_settlement_acks_.end()) {
            ++it->second.received_acks;
            if (it->second.received_acks >= it->second.expected_acks) {
                process_deferred_finished(applied->battle_id);
            }
        }
    }
}

void Runtime::push(v2::battle::BattleEvent event) {
    if (const auto* created = std::get_if<v2::battle::BattleCreatedMsg>(&event)) {
        auto room_it = rooms_by_room_id_.find(created->room_id);
        if (room_it != rooms_by_room_id_.end()) {
            v2::actor::Message started;
            started.header.kind = v2::actor::MessageKind::kUser;
            started.payload = v2::room::BattleStartedMsg{.battle_id = created->battle_id};
            room_it->second.tell(std::move(started));
        }
        for (const auto& user_id : created->player_ids) {
            auto player_it = players_by_user_id_.find(user_id);
            if (player_it == players_by_user_id_.end()) {
                continue;
            }
            auto battle_it = battles_by_room_id_.find(created->room_id);
            if (battle_it == battles_by_room_id_.end()) {
                continue;
            }
            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::BattleAssignedMsg{
                .battle_actor_id = battle_it->second.actor_id(),
                .battle_id = created->battle_id,
            };
            player_it->second.tell(std::move(assign));
        }
        actor_system_.dispatch_all();

        auto pending = pending_battle_start_.find(created->room_id);
        if (pending != pending_battle_start_.end()) {
            emit(net::protocol::kBattleStartResponse,
                 pending->second.session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 format_battle_started_body(created->room_id, created->battle_id));
            pending_battle_start_.erase(pending);
        }
        for (const auto& [session_id, room_id] : rooms_by_session_id_) {
            if (room_id == created->room_id) {
                emit(net::protocol::kBattleStatePush,
                     session_id,
                     0,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     format_battle_state_body(created->room_id, created->battle_id));
            }
        }
        return;
    }

    if (const auto* input = std::get_if<v2::battle::BattleInputAcceptedMsg>(&event)) {
        const auto sid = session_id_for_user(input->user_id);
        if (sid.has_value()) {
            auto pending = pending_battle_input_.find(*sid);
            if (pending != pending_battle_input_.end()) {
                emit(net::protocol::kBattleInputResponse,
                     pending->second.session_id,
                     pending->second.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     format_battle_input_response_body(input->input_seq));
                pending_battle_input_.erase(pending);
            }
        }
        for (const auto& [session_id, room_id] : rooms_by_session_id_) {
            if (room_id == input->room_id && session_user_id(session_id) != input->user_id) {
                emit(net::protocol::kBattleInputPush,
                     session_id,
                     0,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     format_battle_input_push_body(input->user_id, input->input_seq, input->input_data));
            }
        }

        auto battle_it = battles_by_room_id_.find(input->room_id);
        if (battle_it != battles_by_room_id_.end()) {
            v2::actor::Message tick;
            tick.header.kind = v2::actor::MessageKind::kUser;
            tick.payload = v2::battle::TickBattleMsg{
                .trigger = fmt::format("input:{}:{}", input->user_id, input->input_seq),
            };
            battle_it->second.tell(std::move(tick));
        }
        return;
    }

    if (const auto* frame = std::get_if<v2::battle::BattleFrameAdvancedMsg>(&event)) {
        for (const auto& [session_id, room_id] : rooms_by_session_id_) {
            if (room_id == frame->room_id) {
                emit(net::protocol::kBattleStatePush,
                     session_id,
                     0,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     format_battle_frame_body(*frame));
            }
        }
        return;
    }

    if (const auto* settlement = std::get_if<v2::battle::BattleSettlementPreparedMsg>(&event)) {
        archive_battle(*settlement);

        const int expected_acks = 1 + static_cast<int>(settlement->participant_user_ids.size());
        pending_settlement_acks_[settlement->battle_id] = PendingSettlementAck{
            .expected_acks = expected_acks,
            .received_acks = 0,
        };

        auto room_it = rooms_by_room_id_.find(settlement->room_id);
        if (room_it != rooms_by_room_id_.end()) {
            v2::actor::Message room_settlement;
            room_settlement.header.kind = v2::actor::MessageKind::kUser;
            room_settlement.payload = v2::room::BattleSettlementMsg{
                .battle_id = settlement->battle_id,
                .reason = v2::battle::to_string(settlement->reason),
            };
            room_it->second.tell(std::move(room_settlement));
        }

        for (const auto& user_id : settlement->participant_user_ids) {
            auto player_it = players_by_user_id_.find(user_id);
            if (player_it == players_by_user_id_.end()) {
                continue;
            }
            v2::actor::Message player_settlement;
            player_settlement.header.kind = v2::actor::MessageKind::kUser;
            player_settlement.payload = v2::player::BattleSettlementMsg{
                .battle_id = settlement->battle_id,
                .reason = v2::battle::to_string(settlement->reason),
            };
            player_it->second.tell(std::move(player_settlement));
        }
        actor_system_.dispatch_all();

        auto ack_it = pending_settlement_acks_.find(settlement->battle_id);
        if (ack_it != pending_settlement_acks_.end() &&
            ack_it->second.received_acks >= ack_it->second.expected_acks) {
            pending_settlement_acks_.erase(ack_it);
        }

        for (const auto& [session_id, room_id] : rooms_by_session_id_) {
            if (room_id == settlement->room_id) {
                emit(net::protocol::kBattleStatePush,
                     session_id,
                     0,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     format_battle_settlement_body(*settlement));
            }
        }
        return;
    }

    if (const auto* finished = std::get_if<v2::battle::BattleFinishedMsg>(&event)) {
        if (pending_settlement_acks_.contains(finished->battle_id)) {
            deferred_finished_events_[finished->battle_id] = *finished;
            return;
        }
        process_battle_finished(*finished);
        return;
    }
}

std::optional<Runtime::BattleArchive> Runtime::archived_battle(std::string_view battle_id) const {
    auto it = archived_battles_.find(std::string(battle_id));
    if (it == archived_battles_.end()) {
        return std::nullopt;
    }
    return it->second;
}

void Runtime::push(v2::room::RoomEvent event) {
    if (const auto* requested = std::get_if<v2::room::BattleStartRequestedMsg>(&event)) {
        const auto battle_id = fmt::format("battle_{:04}", next_battle_id_++);
        auto battle_actor = actor_system_.create_actor(std::make_unique<v2::battle::BattleActor>(*this));
        battles_by_room_id_[requested->room_id] = battle_actor;

        v2::actor::Message create;
        create.header.kind = v2::actor::MessageKind::kUser;
        create.payload = v2::battle::CreateBattleMsg{
            .battle_id = battle_id,
            .room_id = requested->room_id,
            .player_ids = requested->player_ids,
            .max_frames = 3,
        };
        battle_actor.tell(std::move(create));
        actor_system_.dispatch_all();

        v2::actor::Message timeout;
        timeout.header.kind = v2::actor::MessageKind::kUser;
        timeout.payload = v2::battle::EndBattleMsg{
            .reason = v2::battle::BattleFinishReason::kTimeout,
            .triggering_user_id = {},
        };
        const auto schedule_id = battle_actor.schedule_after(std::move(timeout), std::chrono::seconds(120));
        if (schedule_id != 0) {
            pending_battle_timeout_.emplace(battle_id,
                                           v2::runtime::ScheduleHandle(&actor_system_, schedule_id));
        }
        return;
    }

    if (const auto* rejected = std::get_if<v2::room::BattleStartRejectedMsg>(&event)) {
        auto pending = pending_battle_start_.find(rejected->room_id);
        if (pending != pending_battle_start_.end()) {
            auto error_code = net::protocol::ErrorCode::kAuthRequired;
            if (rejected->reason == "not_room_owner") {
                error_code = net::protocol::ErrorCode::kNotRoomOwner;
            } else if (rejected->reason == "not_enough_players") {
                error_code = net::protocol::ErrorCode::kNotEnoughPlayers;
            } else if (rejected->reason == "not_all_ready") {
                error_code = net::protocol::ErrorCode::kNotAllReady;
            } else if (rejected->reason == "battle_already_started") {
                error_code = net::protocol::ErrorCode::kBattleAlreadyStarted;
            }
            emit(net::protocol::kErrorResponse,
                 pending->second.session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(error_code),
                 rejected->reason);
            pending_battle_start_.erase(pending);
        }
        return;
    }

    if (const auto* applied = std::get_if<v2::room::BattleSettlementAppliedMsg>(&event)) {
        auto it = pending_settlement_acks_.find(applied->battle_id);
        if (it != pending_settlement_acks_.end()) {
            ++it->second.received_acks;
            if (it->second.received_acks >= it->second.expected_acks) {
                process_deferred_finished(applied->battle_id);
            }
        }
    }
}

v2::actor::ActorRef Runtime::get_or_create_player(const std::string& user_id) {
    auto it = players_by_user_id_.find(user_id);
    if (it != players_by_user_id_.end()) {
        return it->second;
    }
    auto actor = actor_system_.create_actor(std::make_unique<v2::player::PlayerActor>(*this));
    players_by_user_id_.emplace(user_id, actor);
    return actor;
}

std::string Runtime::session_user_id(SessionId session_id) const {
    auto it = users_by_session_id_.find(session_id);
    if (it == users_by_session_id_.end()) {
        return {};
    }
    return it->second;
}

std::optional<SessionId> Runtime::session_id_for_user(const std::string& user_id) const {
    for (const auto& [session_id, mapped_user_id] : users_by_session_id_) {
        if (mapped_user_id == user_id) {
            return session_id;
        }
    }
    return std::nullopt;
}

void Runtime::process_battle_finished(const v2::battle::BattleFinishedMsg& finished) {
    pending_battle_timeout_.erase(finished.battle_id);
    battles_by_room_id_.erase(finished.room_id);

    if (!finished.triggering_user_id.empty()) {
        const auto session_id = session_id_for_user(finished.triggering_user_id);
        if (session_id.has_value()) {
            auto pending = pending_battle_end_.find(*session_id);
            if (pending != pending_battle_end_.end()) {
                emit(net::protocol::kBattleInputResponse,
                     pending->second.session_id,
                     pending->second.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                     format_battle_end_accepted_body(finished.reason));
                pending_battle_end_.erase(pending);
            }
        }
    }

    auto room_it = rooms_by_room_id_.find(finished.room_id);
    if (room_it != rooms_by_room_id_.end()) {
        v2::actor::Message ended;
        ended.header.kind = v2::actor::MessageKind::kUser;
        ended.payload = v2::room::BattleEndedMsg{
            .battle_id = finished.battle_id,
            .reason = v2::battle::to_string(finished.reason),
        };
        room_it->second.tell(std::move(ended));
    }

    for (auto& [user_id, player_actor] : players_by_user_id_) {
        v2::actor::Message ended;
        ended.header.kind = v2::actor::MessageKind::kUser;
        ended.payload = v2::player::BattleEndedMsg{
            .battle_id = finished.battle_id,
            .reason = v2::battle::to_string(finished.reason),
        };
        player_actor.tell(std::move(ended));
    }
    actor_system_.dispatch_all();

    for (const auto& [session_id, room_id] : rooms_by_session_id_) {
        if (room_id == finished.room_id) {
            emit(net::protocol::kBattleStatePush,
                 session_id,
                 0,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 format_battle_finished_body(finished));
        }
    }
}

void Runtime::process_deferred_finished(const std::string& battle_id) {
    pending_settlement_acks_.erase(battle_id);
    auto it = deferred_finished_events_.find(battle_id);
    if (it != deferred_finished_events_.end()) {
        auto finished = it->second;
        deferred_finished_events_.erase(it);
        process_battle_finished(finished);
    }
}

void Runtime::archive_battle(const v2::battle::BattleSettlementPreparedMsg& settlement) {
    auto archive = BattleArchive{
        .battle_id = settlement.battle_id,
        .room_id = settlement.room_id,
        .reason = v2::battle::to_string(settlement.reason),
        .triggering_user_id = settlement.triggering_user_id,
        .total_frames = settlement.total_frames,
        .participant_user_ids = settlement.participant_user_ids,
        .replay_payload = build_replay_payload(settlement),
        .result = settlement.result,
    };
    archived_battles_[settlement.battle_id] = archive;
    if (archive_sink_ != nullptr) {
        if (!archive_sink_->persist(archive)) {
            SPDLOG_ERROR("Failed to persist battle archive {}", settlement.battle_id);
        }

        nlohmann::json participants = nlohmann::json::array();
        for (const auto& score : settlement.result.scores) {
            participants.push_back({
                {"user_id", score.user_id},
                {"online", false},
                {"score", score.score},
                {"last_submitted_frame", 0},
                {"last_acked_frame", 0},
            });
        }

        nlohmann::json replay_inputs = nlohmann::json::array();
        for (const auto& r : settlement.replay_inputs) {
            replay_inputs.push_back({
                {"input_seq", r.input_seq},
                {"frame_number", r.frame_number},
                {"user_id", r.user_id},
                {"input_data", r.input_data},
                {"score", r.score},
                {"trigger", r.trigger},
            });
        }

        nlohmann::json snapshot{
            {"clock", {{"frame_number", settlement.total_frames}, {"last_trigger", settlement.triggering_user_id}}},
            {"metadata", {
                {"battle_id", settlement.battle_id},
                {"room_id", settlement.room_id},
                {"lifecycle", static_cast<int>(v2::battle::BattleLifecycleState::kFinished)},
                {"frame_number", settlement.total_frames},
                {"max_frames", 0},
                {"next_input_seq", static_cast<std::uint64_t>(0)},
            }},
            {"participants", std::move(participants)},
            {"replay_inputs", std::move(replay_inputs)},
        };

        if (!archive_sink_->save_snapshot(settlement.battle_id, snapshot.dump())) {
            SPDLOG_ERROR("Failed to persist battle snapshot {}", settlement.battle_id);
        }
    }
}

void Runtime::emit(std::uint16_t message_id,
                   SessionId session_id,
                   std::uint32_t request_id,
                   std::int32_t error_code,
                   std::string body) {
    SessionWrite write;
    write.envelope.session_id = session_id;
    write.envelope.protocol_message_id = message_id;
    write.envelope.request_id = request_id;
    write.envelope.error_code = error_code;
    write.envelope.body = std::move(body);
    write_sink_.push(std::move(write));
}

}  // namespace v2::gateway
