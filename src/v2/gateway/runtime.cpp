#include "v2/gateway/runtime.h"

#include "net/protocol.h"
#include "v2/diagnostics/diagnostics_manager.h"
#include "v2/diagnostics/health_check.h"
#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/battle_protocol_codec.h"
#include "v2/gateway/gateway_command_parser.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/gateway/rate_limiter.h"
#include "v2/service/error_codes.h"
#include "v2/tracing/trace_context.h"
#include "app/audit_log.h"

#include <fmt/format.h>
#include <fmt/ranges.h>
#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

#include <chrono>
#include <cstdlib>
#include <utility>

namespace v2::gateway {

namespace {

void update_max(std::atomic<std::uint64_t>& target, std::uint64_t candidate) {
    auto current = target.load(std::memory_order_relaxed);
    while (current < candidate &&
           !target.compare_exchange_weak(current,
                                         candidate,
                                         std::memory_order_relaxed,
                                         std::memory_order_relaxed)) {
    }
}

double read_rate_limit_override(const char* name, double fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || raw[0] == '\0') {
        return fallback;
    }
    const double parsed = std::strtod(raw, nullptr);
    return parsed > 0.0 ? parsed : fallback;
}

std::uint32_t read_uint32_override(const char* name, std::uint32_t fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || raw[0] == '\0') {
        return fallback;
    }
    const auto parsed = std::strtoul(raw, nullptr, 10);
    return parsed > 0 ? static_cast<std::uint32_t>(parsed) : fallback;
}

std::uint32_t read_uint32_override_allow_zero(const char* name,
                                              std::uint32_t fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || raw[0] == '\0') {
        return fallback;
    }
    return static_cast<std::uint32_t>(std::strtoul(raw, nullptr, 10));
}

std::uint32_t read_nonzero_uint32_override(const char* name, std::uint32_t fallback) {
    const char* raw = std::getenv(name);
    if (raw == nullptr || raw[0] == '\0') {
        return fallback;
    }
    const auto parsed = std::strtoul(raw, nullptr, 10);
    return parsed > 0 ? static_cast<std::uint32_t>(parsed) : fallback;
}

RateLimiter::Config load_rate_limiter_config() {
    RateLimiter::Config config;
    config.connection_limit = read_rate_limit_override(
        "V2_RATE_LIMIT_CONNECTION", config.connection_limit);
    config.message_type_limit = read_rate_limit_override(
        "V2_RATE_LIMIT_MESSAGE_TYPE", config.message_type_limit);
    config.ip_limit = read_rate_limit_override(
        "V2_RATE_LIMIT_IP", config.ip_limit);
    config.user_limit = read_rate_limit_override(
        "V2_RATE_LIMIT_USER", config.user_limit);
    config.login_limit = read_rate_limit_override(
        "V2_RATE_LIMIT_LOGIN", config.login_limit);
    return config;
}

std::string backend_error_reason(const GatewayServiceBridge::BackendRoutingResult& result,
                                 std::string fallback) {
    if (result.response_payload.empty()) {
        return fallback;
    }
    auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
    if (doc.is_discarded()) {
        return result.response_payload;
    }
    return doc.value("reason", std::move(fallback));
}

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

Runtime::Runtime(v2::runtime::ActorSystem& actor_system,
                 SessionWriteSink& write_sink,
                 BattleArchiveSink* archive_sink)
    : actor_system_(actor_system)
    , write_sink_(write_sink)
    , archive_sink_(archive_sink)
    , diagnostics_(std::make_unique<v2::diagnostics::DiagnosticsManager>())
    , health_check_(std::make_unique<v2::diagnostics::HealthCheck>()) {}

Runtime::~Runtime() {
    stop_battle_route_workers();
}

void Runtime::set_battle_route_completion_dispatcher(
    BattleRouteCompletionDispatcher dispatcher) {
    battle_route_completion_dispatcher_ = std::move(dispatcher);
}

void Runtime::shutdown_battle_route_workers() {
    stop_battle_route_workers();
}

void Runtime::set_session_role(SessionId session_id, v2::auth::Role role) {
    session_roles_[session_id] = role;
}

bool Runtime::is_session_allowed(SessionId session_id,
                                  std::uint16_t protocol_message_id) const {
    auto it = session_roles_.find(session_id);
    if (it == session_roles_.end()) {
        // No role stored — skip the check (allowed by default).
        // This covers unauthenticated sessions and the local auth path.
        return true;
    }
    return v2::auth::Authorizer::instance().is_allowed(it->second, protocol_message_id);
}

void Runtime::set_backend_metrics_for_diagnostics(
    std::shared_ptr<BackendMetrics> m) {
    diagnostics_->set_backend_metrics(m);
    health_check_->set_backend_metrics(std::move(m));
}

void Runtime::set_service_registry_for_diagnostics(
    std::shared_ptr<v2::service::ServiceRegistry> r) {
    diagnostics_->set_service_registry(r);
    health_check_->set_service_registry(std::move(r));
}

Runtime::BattleRouteDiagnostics Runtime::battle_route_diagnostics() const noexcept {
    return BattleRouteDiagnostics{
        .completed_tasks = battle_route_completed_tasks_.load(std::memory_order_relaxed),
        .queued_tasks = battle_route_queued_tasks_.load(std::memory_order_relaxed),
        .rejected_tasks = battle_route_rejected_tasks_.load(std::memory_order_relaxed),
        .dropped_completions =
            battle_route_dropped_completions_.load(std::memory_order_relaxed),
        .queue_capacity = battle_route_queue_capacity_,
        .total_queue_wait_us = battle_route_total_queue_wait_us_.load(std::memory_order_relaxed),
        .max_queue_wait_us = battle_route_max_queue_wait_us_.load(std::memory_order_relaxed),
        .total_task_execution_us =
            battle_route_total_task_execution_us_.load(std::memory_order_relaxed),
        .max_task_execution_us =
            battle_route_max_task_execution_us_.load(std::memory_order_relaxed),
        .total_backend_route_us =
            battle_route_total_backend_route_us_.load(std::memory_order_relaxed),
        .max_backend_route_us =
            battle_route_max_backend_route_us_.load(std::memory_order_relaxed),
        .total_response_dispatch_us =
            battle_route_total_response_dispatch_us_.load(std::memory_order_relaxed),
        .max_response_dispatch_us =
            battle_route_max_response_dispatch_us_.load(std::memory_order_relaxed),
    };
}

v2::actor::ActorRef Runtime::create_gateway_actor() {
    if (battle_frame_push_every_ == 0) {
        battle_frame_push_every_ = read_nonzero_uint32_override(
            "V2_BATTLE_FRAME_PUSH_EVERY", 1);
    }
    if (battle_route_worker_count_ == 0) {
        battle_route_worker_count_ = read_uint32_override_allow_zero(
            "V2_BATTLE_ROUTE_WORKERS", 4);
    }
    if (battle_route_queue_capacity_ == 0) {
        battle_route_queue_capacity_ = read_nonzero_uint32_override(
            "V2_BATTLE_ROUTE_QUEUE_CAPACITY", 1024);
    }
    start_battle_route_workers();
    auto rate_limiter = std::make_shared<RateLimiter>(load_rate_limiter_config());
    return actor_system_.create_actor(std::make_unique<GatewayActor>(
        write_sink_,
        this,
        [rate_limiter, this](const ClientEnvelope& envelope,
                             SessionId session_id) -> RateLimitResult {
            // IP hint is not yet available in this context; pass empty.
            const std::string ip_hint;
            const auto user_id = lookup_.user_id_for(session_id);
            return rate_limiter->check(envelope, session_id, ip_hint, user_id);
        },
        [this](const GatewayCommand& command) { return is_authenticated(command); }));
}

bool Runtime::is_authenticated(const GatewayCommand& command) const {
    return !lookup_.user_id_for(command.session_id).empty();
}

void Runtime::on_session_closed(SessionId session_id) {
    const auto user_id = lookup_.user_id_for(session_id);
    const auto room_id = lookup_.room_id_for(session_id);

    if (!user_id.empty()) {
        auto* player_ref = lookup_.player(user_id);
        if (player_ref != nullptr) {
            v2::actor::Message closed;
            closed.header.kind = v2::actor::MessageKind::kUser;
            closed.payload = v2::player::SessionClosedMsg{.session_id = session_id};
            player_ref->tell(std::move(closed));
        }
    }

    // Notify room service that the player disconnected (leave room).
    if (!user_id.empty() && !room_id.empty()) {
        if (bridge_ != nullptr) {
            nlohmann::json room_payload{
                {"user_id", user_id},
                {"room_id", room_id},
            };
            (void)bridge_->route(v2::service::ServiceId::kRoom,
                                 "room_leave",
                                 room_payload.dump());
        } else if (auto* room_ref = lookup_.room(room_id); room_ref != nullptr) {
            v2::actor::Message leave;
            leave.header.kind = v2::actor::MessageKind::kUser;
            leave.payload = v2::room::LeaveRoomMsg{.user_id = user_id};
            room_ref->tell(std::move(leave));
        }
    }

    pending_battle_input_.erase(session_id);
    pending_battle_end_.erase(session_id);
    session_roles_.erase(session_id);
    lookup_.erase_session(session_id);

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

    AUDIT_LOG("session_closed", "session_id=" + std::to_string(session_id) + " user_id=" + user_id);
}

void Runtime::set_service_bridge(std::unique_ptr<GatewayServiceBridge> bridge) {
    bridge_ = std::move(bridge);
}

void Runtime::mark_session_authenticated(SessionId session_id,
                                         const std::string& user_id,
                                         v2::auth::Role role) {
    lookup_.set_session_user(session_id, user_id);
    set_session_role(session_id, role);
}

bool Runtime::handle(const GatewayCommand& command) {
    // v2.2.0: Setup cross-service trace context for distributed tracing
    if (bridge_) {
        auto trace_ctx = v2::tracing::TraceContext::create_root();
        bridge_->set_trace_context(trace_ctx.trace_id, trace_ctx.current_span_id);
    }

    // v2.2.0: RBAC check via Authorizer
    // Only enforced for sessions with a stored role (post-login sessions).
    // Unauthenticated sessions (no role) bypass the check.
    if (!is_session_allowed(command.session_id, command.protocol_message_id)) {
        emit(net::protocol::kErrorResponse,
             command.session_id,
             command.request_id,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
             "permission_denied");
        return true;
    }

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

                auto login_body_str = auth_payload.dump();
                if (schema_validator_.has_schema("login_request")) {
                    auto sv = schema_validator_.validate("login_request", login_body_str);
                    if (!sv.valid) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id, command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                             sv.error);
                        return true;
                    }
                }

                auto result = bridge_->route(v2::service::ServiceId::kLogin,
                                             "login_request",
                                             std::move(login_body_str));

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        bool is_duplicate = resp.value("is_duplicate", false);

                        // Store role for Authorizer RBAC checks
                        std::string role_str = resp.value("role", "player");
                        set_session_role(command.session_id,
                                         v2::auth::role_from_string(role_str));

                        // Kick old session on duplicate login
                        if (is_duplicate) {
                            auto old_session = lookup_.session_for_user(login_body->user_id);
                            if (old_session.has_value()) {
                                emit(net::protocol::kSessionKickedPush,
                                     *old_session,
                                     0,
                                     static_cast<std::int32_t>(net::protocol::ErrorCode::kDuplicateLogin),
                                     "duplicate_login");
                                lookup_.erase_session_user(*old_session);

                                auto* player_ref = lookup_.player(login_body->user_id);
                                if (player_ref != nullptr) {
                                    v2::actor::Message closed;
                                    closed.header.kind = v2::actor::MessageKind::kUser;
                                    closed.payload = v2::player::SessionClosedMsg{.session_id = *old_session};
                                    player_ref->tell(std::move(closed));
                                }
                            }
                        }

                        lookup_.set_session_user(command.session_id, login_body->user_id);
                        emit(net::protocol::kLoginResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             "login_ok:" + login_body->user_id);
                        AUDIT_LOG("login_success", "user_id=" + login_body->user_id + " session_id=" + std::to_string(command.session_id));
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
                    AUDIT_LOG("login_failure", "user_id=" + login_body->user_id + " reason=invalid_token");
                    return true;
                }

                // Bridge routing failure (timeout, unavailable, etc.)
                auto net_error = net::protocol::ErrorCode::kLoginBackendUnavailable;
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
        case GatewayCommandType::kRegister: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kLoginBackendUnavailable),
                     "register_requires_backend_bridge");
                return true;
            }

            auto doc = nlohmann::json::parse(command.body, nullptr, false);
            if (doc.is_discarded() || !doc.is_object()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                     "invalid_register_payload");
                return true;
            }

            auto result = bridge_->route(v2::service::ServiceId::kLogin,
                                         "register_account",
                                         doc.dump());
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kLoginBackendUnavailable),
                     backend_error_reason(result, "register_failed"));
                return true;
            }

            emit(net::protocol::kRegisterResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 result.response_payload);
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

                auto room_body_str = room_payload.dump();
                if (schema_validator_.has_schema("room_create")) {
                    auto sv = schema_validator_.validate("room_create", room_body_str);
                    if (!sv.valid) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id, command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                             sv.error);
                        return true;
                    }
                }

                bridge_route(bridge_.get(),
                             v2::service::ServiceId::kRoom,
                             "room_create",
                             std::move(room_body_str),
                             [&](const nlohmann::json&) {
                                 lookup_.set_session_room(command.session_id, *room_id);
                                 emit(net::protocol::kRoomCreateResponse,
                                      command.session_id,
                                      command.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                      *room_id);
                                 AUDIT_LOG("room_created", "room_id=" + *room_id + " user_id=" + user_id);
                             },
                             [&](const std::string& reason) {
                                 auto error_code = net::protocol::ErrorCode::kInvalidRoomId;
                                 if (reason == "backend_error") {
                                     error_code = net::protocol::ErrorCode::kRoomBackendUnavailable;
                                 }
                                 emit(net::protocol::kErrorResponse,
                                      command.session_id,
                                      command.request_id,
                                      static_cast<std::int32_t>(error_code),
                                      reason);
                             });
                return true;
            }

            // Local path: create RoomActor
            auto room_actor = actor_system_.create_actor(std::make_unique<v2::room::RoomActor>(*this));
            lookup_.set_room(*room_id, room_actor);
            auto pending_guard = PendingResponseGuard(pending_room_create_, *room_id, nullptr);

            v2::actor::Message create;
            create.header.kind = v2::actor::MessageKind::kUser;
            create.payload = v2::room::CreateRoomMsg{
                .room_id = *room_id,
                .owner_user_id = user_id,
                .owner_actor_id = lookup_.player(user_id)->actor_id(),
            };
            room_actor.tell(std::move(create));

            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_actor.actor_id(),
                .room_id = *room_id,
            };
            lookup_.player(user_id)->tell(std::move(assign));

            actor_system_.dispatch_all();
            lookup_.set_session_room(command.session_id, *room_id);
            emit(net::protocol::kRoomCreateResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 *room_id);
            AUDIT_LOG("room_created", "room_id=" + *room_id + " user_id=" + user_id);
            pending_guard.release();
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

                auto room_body_str = room_payload.dump();
                if (schema_validator_.has_schema("room_join")) {
                    auto sv = schema_validator_.validate("room_join", room_body_str);
                    if (!sv.valid) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id, command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                             sv.error);
                        return true;
                    }
                }

                auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                             "room_join",
                                             std::move(room_body_str));

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        lookup_.set_session_room(command.session_id, *room_id);
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
                auto error_code = net::protocol::ErrorCode::kRoomBackendUnavailable;
                if (result.error == v2::service::ServiceErrorCode::kRejected) {
                    error_code = net::protocol::ErrorCode::kInvalidRoomId;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(error_code),
                     backend_error_reason(result,
                                          result.error == v2::service::ServiceErrorCode::kRejected
                                              ? "room_join_rejected"
                                              : "backend_error"));
                return true;
            }

            // Local path
            auto* room_ref = lookup_.room(*room_id);
            if (room_ref == nullptr) {
                return false;
            }
            auto pending_guard = PendingResponseGuard(pending_room_join_, *room_id + ":" + user_id, nullptr);

            v2::actor::Message join;
            join.header.kind = v2::actor::MessageKind::kUser;
            join.payload = v2::room::JoinRoomMsg{
                .user_id = user_id,
                .player_actor_id = lookup_.player(user_id)->actor_id(),
            };
            room_ref->tell(std::move(join));

            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_ref->actor_id(),
                .room_id = *room_id,
            };
            lookup_.player(user_id)->tell(std::move(assign));

            actor_system_.dispatch_all();
            lookup_.set_session_room(command.session_id, *room_id);
            emit(net::protocol::kRoomJoinResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 *room_id);
            pending_guard.release();
            return true;
        }
        case GatewayCommandType::kRoomReady: {
            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto session_room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty() || session_room_id.empty()) {
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
                    {"room_id", session_room_id},
                    {"ready", *ready},
                };

                auto room_body_str = room_payload.dump();
                if (schema_validator_.has_schema("room_ready")) {
                    auto sv = schema_validator_.validate("room_ready", room_body_str);
                    if (!sv.valid) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id, command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                             sv.error);
                        return true;
                    }
                }

                auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                             "room_ready",
                                             std::move(room_body_str));

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        emit(net::protocol::kRoomReadyResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             *ready ? "true" : "false");
                        AUDIT_LOG("room_ready", "room_id=" + session_room_id + " user_id=" + user_id + " ready=" + (*ready ? "true" : "false"));
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
                auto error_code = net::protocol::ErrorCode::kRoomBackendUnavailable;
                if (result.error == v2::service::ServiceErrorCode::kRejected) {
                    error_code = net::protocol::ErrorCode::kAuthRequired;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(error_code),
                     backend_error_reason(result,
                                          result.error == v2::service::ServiceErrorCode::kRejected
                                              ? "room_ready_rejected"
                                              : "backend_error"));
                return true;
            }

            // Local path
            auto* room_ref = lookup_.room(session_room_id);
            if (room_ref == nullptr) {
                return false;
            }
            pending_room_ready_[session_room_id + ":" + user_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message set_ready;
            set_ready.header.kind = v2::actor::MessageKind::kUser;
            set_ready.payload = v2::room::SetReadyMsg{
                .user_id = user_id,
                .ready = *ready,
            };
            room_ref->tell(std::move(set_ready));
            actor_system_.dispatch_all();
            emit(net::protocol::kRoomReadyResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 *ready ? "true" : "false");
            AUDIT_LOG("room_ready", "room_id=" + session_room_id + " user_id=" + user_id + " ready=" + (*ready ? "true" : "false"));
            pending_room_ready_.erase(session_room_id + ":" + user_id);
            return true;
        }
        case GatewayCommandType::kRoomLeave: {
            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto session_room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty() || session_room_id.empty()) {
                return false;
            }

            // Bridge path: delegate to room_backend
            if (bridge_) {
                nlohmann::json room_payload{
                    {"user_id", user_id},
                    {"room_id", session_room_id},
                };

                auto room_body_str = room_payload.dump();
                if (schema_validator_.has_schema("room_leave")) {
                    auto sv = schema_validator_.validate("room_leave", room_body_str);
                    if (!sv.valid) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id, command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                             sv.error);
                        return true;
                    }
                }

                auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                             "room_leave",
                                             std::move(room_body_str));

                if (result.success) {
                    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
                    if (!resp.is_discarded() && resp.value("status", "") == "ok") {
                        lookup_.erase_session_room(command.session_id);
                        emit(net::protocol::kRoomLeaveResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                             session_room_id);
                        AUDIT_LOG("room_left", "room_id=" + session_room_id + " user_id=" + user_id);
                        return true;
                    }
                    std::string reason = resp.is_discarded() ? "leave_failed"
                        : resp.value("reason", "leave_failed");
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                         reason);
                    return true;
                }
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     "backend_error");
                return true;
            }

            // Local path: notify RoomActor
            auto* room_ref = lookup_.room(session_room_id);
            if (room_ref == nullptr) {
                return false;
            }
            v2::actor::Message leave;
            leave.header.kind = v2::actor::MessageKind::kUser;
            leave.payload = v2::room::LeaveRoomMsg{.user_id = user_id};
            room_ref->tell(std::move(leave));
            actor_system_.dispatch_all();

            lookup_.erase_session_room(command.session_id);
            emit(net::protocol::kRoomLeaveResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 session_room_id);
            AUDIT_LOG("room_left", "room_id=" + session_room_id + " user_id=" + user_id);
            return true;
        }
        case GatewayCommandType::kRoomList: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     "room_list_requires_backend_bridge");
                return true;
            }

            std::string body = command.body.empty() ? "{}" : command.body;
            auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                         "room_list",
                                         std::move(body));
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     backend_error_reason(result, "room_list_failed"));
                return true;
            }
            emit(net::protocol::kRoomListResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 result.response_payload);
            return true;
        }
        case GatewayCommandType::kRoomDetail: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     "room_detail_requires_backend_bridge");
                return true;
            }

            const auto room_id = parse_room_id_body(command.body);
            if (!room_id.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId),
                     net::protocol::to_string(net::protocol::ErrorCode::kInvalidRoomId));
                return true;
            }

            nlohmann::json payload{{"room_id", *room_id}};
            auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                         "room_detail",
                                         payload.dump());
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     backend_error_reason(result, "room_detail_failed"));
                return true;
            }
            emit(net::protocol::kRoomDetailResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 result.response_payload);
            return true;
        }
        case GatewayCommandType::kRoomKick: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     "room_kick_requires_backend_bridge");
                return true;
            }

            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty() || room_id.empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                     net::protocol::to_string(net::protocol::ErrorCode::kNotInRoom));
                return true;
            }

            nlohmann::json payload{
                {"user_id", user_id},
                {"room_id", room_id},
                {"target_user_id", command.body},
            };
            auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                         "room_kick",
                                         payload.dump());
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     backend_error_reason(result, "room_kick_failed"));
                return true;
            }

            emit(net::protocol::kRoomKickResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 result.response_payload);
            return true;
        }
        case GatewayCommandType::kRoomTransferOwner: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     "room_transfer_requires_backend_bridge");
                return true;
            }

            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty() || room_id.empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kNotInRoom),
                     net::protocol::to_string(net::protocol::ErrorCode::kNotInRoom));
                return true;
            }

            nlohmann::json payload{
                {"user_id", user_id},
                {"room_id", room_id},
                {"new_owner_id", command.body},
            };
            auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                         "room_transfer_owner",
                                         payload.dump());
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable),
                     backend_error_reason(result, "room_transfer_failed"));
                return true;
            }

            emit(net::protocol::kRoomTransferOwnerResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 result.response_payload);
            return true;
        }
        case GatewayCommandType::kBattleStart: {
            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto session_room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty() || session_room_id.empty()) {
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
            if (battle_start->room_id.has_value() && *battle_start->room_id != session_room_id) {
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
                    {"room_id", session_room_id},
                };

                auto room_body_str = room_payload.dump();
                if (schema_validator_.has_schema("room_start_battle")) {
                    auto sv = schema_validator_.validate("room_start_battle", room_body_str);
                    if (!sv.valid) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id, command.request_id,
                             static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                             sv.error);
                        return true;
                    }
                }

                auto room_result = bridge_->route(v2::service::ServiceId::kRoom,
                                                  "room_start_battle",
                                                  std::move(room_body_str));

                if (!room_result.success) {
                    auto error_code = net::protocol::ErrorCode::kRoomBackendUnavailable;
                    if (room_result.error == v2::service::ServiceErrorCode::kRejected) {
                        error_code = net::protocol::ErrorCode::kBattleNotStarted;
                    }
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(error_code),
                         backend_error_reason(
                             room_result,
                             room_result.error == v2::service::ServiceErrorCode::kRejected
                                 ? "room_start_battle_rejected"
                                 : "backend_error"));
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
                                    battles_by_room_id_[session_room_id] =
                                        v2::actor::ActorRef{};  // placeholder for bridge mode
                                    room_to_battle_id_[session_room_id] = battle_id;
                                }

                                // Emit kBattleStartResponse to requester
                                emit(net::protocol::kBattleStartResponse,
                                     command.session_id,
                                     command.request_id,
                                     static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                     format_battle_started_body(
                                         session_room_id, battle_id));
                                AUDIT_LOG("battle_started", "room_id=" + session_room_id + " user_id=" + user_id);

                                // Broadcast push_to_sessions events
                                if (battle_resp.contains("push_to_sessions")) {
                                    for (const auto& push : battle_resp["push_to_sessions"]) {
                                        std::string kind = push.value("kind", "");
                                        if (kind == "battle_started") {
                                            broadcast_to_room(
                                                session_room_id,
                                                net::protocol::kBattleStatePush,
                                                format_battle_state_body(session_room_id, battle_id));
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
            auto* room_ref = lookup_.room(session_room_id);
            if (room_ref == nullptr) {
                return false;
            }
            pending_battle_start_[session_room_id] = PendingResponse{
                .session_id = command.session_id,
                .request_id = command.request_id,
            };
            v2::actor::Message start;
            start.header.kind = v2::actor::MessageKind::kUser;
            start.payload = v2::room::StartBattleMsg{.requester_user_id = user_id};
            room_ref->tell(std::move(start));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kBattleInput: {
            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto session_room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty() || session_room_id.empty()) {
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
                auto battle_it = battles_by_room_id_.find(session_room_id);
                if (battle_it == battles_by_room_id_.end()) {
                    emit(net::protocol::kErrorResponse,
                         command.session_id,
                         command.request_id,
                         static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                         net::protocol::to_string(net::protocol::ErrorCode::kBattleNotStarted));
                    return true;
                }

                auto battle_id_it = room_to_battle_id_.find(session_room_id);
                std::string bridge_battle_id = battle_id_it != room_to_battle_id_.end()
                    ? battle_id_it->second : "";

                if (battle_input->is_finish_request) {
                    nlohmann::json finish_payload{
                        {"user_id", user_id},
                        {"battle_id", bridge_battle_id},
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

                            bool finished_push_processed = false;
                            if (resp.contains("push_to_sessions") &&
                                resp["push_to_sessions"].is_array()) {
                                for (const auto& push : resp["push_to_sessions"]) {
                                    if (push.value("kind", "") == "battle_finished") {
                                        finished_push_processed = true;
                                    }
                                    process_bridge_battle_finished_push(
                                        push, session_room_id, bridge_battle_id);
                                }
                            }
                            if (!finished_push_processed) {
                                process_bridge_battle_finished_push(
                                    nlohmann::json{
                                        {"kind", "battle_finished"},
                                        {"battle_id", bridge_battle_id},
                                        {"reason", v2::battle::to_string(
                                            battle_input->finish_reason)},
                                    },
                                    session_room_id,
                                    bridge_battle_id);
                            }
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
                    {"battle_id", bridge_battle_id},
                    {"input_data", battle_input->input_data},
                    {"score", battle_input->score},
                    {"submitted_frame", battle_input->submitted_frame},
                };

                if (battle_route_offload_enabled() && !bridge_battle_id.empty()) {
                    const auto session_id = command.session_id;
                    const auto request_id = command.request_id;
                    const auto room_id = session_room_id;
                    auto* bridge = bridge_.get();
                    const auto queued = enqueue_battle_route_task(
                        [this, bridge, session_id, request_id, room_id,
                         battle_id = bridge_battle_id,
                         payload = input_payload.dump()]() mutable {
                            const auto route_started_at = std::chrono::steady_clock::now();
                            auto result = bridge->route(v2::service::ServiceId::kBattle,
                                                        "battle_input",
                                                        payload);
                            const auto route_completed_at = std::chrono::steady_clock::now();
                            const auto backend_route_us = static_cast<std::uint64_t>(
                                std::chrono::duration_cast<std::chrono::microseconds>(
                                    route_completed_at - route_started_at).count());
                            battle_route_total_backend_route_us_.fetch_add(
                                backend_route_us, std::memory_order_relaxed);
                            update_max(battle_route_max_backend_route_us_, backend_route_us);
                            const auto dispatched = battle_route_completion_dispatcher_(
                                [this, session_id, request_id, room_id, battle_id,
                                 result = std::move(result), route_completed_at]() mutable {
                                    complete_bridge_battle_input(
                                        session_id,
                                        request_id,
                                        room_id,
                                        battle_id,
                                        std::move(result));
                                    const auto response_dispatch_us = static_cast<std::uint64_t>(
                                        std::chrono::duration_cast<std::chrono::microseconds>(
                                            std::chrono::steady_clock::now() - route_completed_at).count());
                                    battle_route_total_response_dispatch_us_.fetch_add(
                                        response_dispatch_us, std::memory_order_relaxed);
                                    update_max(battle_route_max_response_dispatch_us_,
                                               response_dispatch_us);
                                });
                            if (!dispatched) {
                                battle_route_dropped_completions_.fetch_add(
                                    1, std::memory_order_relaxed);
                            }
                        });
                    if (!queued) {
                        emit(net::protocol::kErrorResponse,
                             command.session_id,
                             command.request_id,
                             static_cast<std::int32_t>(
                                 net::protocol::ErrorCode::kBattleRouteOverloaded),
                             net::protocol::to_string(
                                 net::protocol::ErrorCode::kBattleRouteOverloaded));
                    }
                    return true;
                }

                auto result = bridge_->route(v2::service::ServiceId::kBattle,
                                             "battle_input",
                                             input_payload.dump());

                complete_bridge_battle_input(command.session_id,
                                             command.request_id,
                                             session_room_id,
                                             bridge_battle_id,
                                             std::move(result));
                return true;
            }

            // Local path
            auto battle_it = battles_by_room_id_.find(session_room_id);
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
                .submitted_frame = battle_input->submitted_frame,
            };
            battle_it->second.tell(std::move(input));
            actor_system_.dispatch_all();
            return true;
        }
        case GatewayCommandType::kBattleState: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleBackendUnavailable),
                     "battle_state_requires_backend_bridge");
                return true;
            }

            const auto user_id = lookup_.user_id_for(command.session_id);
            const auto session_room_id = lookup_.room_id_for(command.session_id);
            if (user_id.empty()) {
                return false;
            }

            std::string requested_battle_id = command.body;
            if (requested_battle_id.empty()) {
                requested_battle_id = battle_id_for_room(session_room_id);
            }
            if (requested_battle_id.empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                     net::protocol::to_string(net::protocol::ErrorCode::kBattleNotStarted));
                return true;
            }

            nlohmann::json payload{{"battle_id", requested_battle_id}};
            auto result = bridge_->route(v2::service::ServiceId::kBattle,
                                         "battle_state",
                                         payload.dump());
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleBackendUnavailable),
                     backend_error_reason(result, "battle_state_failed"));
                return true;
            }

            auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
            if (resp.is_discarded() || resp.value("status", "") != "ok") {
                const auto reason = resp.is_discarded()
                    ? std::string{"battle_state_failed"}
                    : resp.value("reason", "battle_state_failed");
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                     reason);
                return true;
            }

            std::string body = result.response_payload;
            if (resp.contains("snapshot")) {
                body = resp["snapshot"].dump();
            }
            emit(net::protocol::kBattleStateResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 std::move(body));
            return true;
        }
        case GatewayCommandType::kReplayLoad: {
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleBackendUnavailable),
                     "replay_load_requires_backend_bridge");
                return true;
            }

            const auto user_id = lookup_.user_id_for(command.session_id);
            if (user_id.empty()) {
                return false;
            }
            if (command.body.empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted),
                     "empty_battle_id");
                return true;
            }

            nlohmann::json payload{{"battle_id", command.body}};
            auto result = bridge_->route(v2::service::ServiceId::kBattle,
                                         "replay_load",
                                         payload.dump());
            if (!result.success) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleBackendUnavailable),
                     backend_error_reason(result, "replay_load_failed"));
                return true;
            }

            emit(net::protocol::kReplayLoadResponse,
                 command.session_id,
                 command.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 result.response_payload);
            return true;
        }
        case GatewayCommandType::kMatchJoin: {
            const auto session_user_id = lookup_.user_id_for(command.session_id);
            const auto match = parse_match_command_body(command.body);
            if (session_user_id.empty() || !match.has_value() ||
                !validate_match_command_body(*match) ||
                match->user_id != session_user_id) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_match_join");
                return true;
            }
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "matchmaking_backend_unavailable");
                return true;
            }

            nlohmann::json payload{
                {"user_id", match->user_id},
                {"mmr", match->mmr},
                {"mode", match->mode},
            };
            bridge_route(bridge_.get(),
                         v2::service::ServiceId::kMatchmaking,
                          "match_join",
                          payload.dump(),
                          [&](const nlohmann::json& resp) {
                              emit(net::protocol::kMatchJoinResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  resp.dump());
                          },
                          [&](const std::string& reason) {
                              emit(net::protocol::kErrorResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                                  reason);
                         });
            return true;
        }
        case GatewayCommandType::kMatchLeave: {
            const auto session_user_id = lookup_.user_id_for(command.session_id);
            const auto match = parse_match_command_body(command.body);
            if (session_user_id.empty() || !match.has_value() ||
                !validate_match_command_body(*match) ||
                match->user_id != session_user_id) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_match_leave");
                return true;
            }
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "matchmaking_backend_unavailable");
                return true;
            }

            nlohmann::json payload{
                {"user_id", match->user_id},
                {"mode", match->mode},
            };
            bridge_route(bridge_.get(),
                         v2::service::ServiceId::kMatchmaking,
                         "match_leave",
                         payload.dump(),
                         [&](const nlohmann::json& resp) {
                             emit(net::protocol::kMatchLeaveResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  resp.dump());
                         },
                         [&](const std::string& reason) {
                             emit(net::protocol::kErrorResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                                  reason);
                         });
            return true;
        }
        case GatewayCommandType::kMatchStatus: {
            const auto session_user_id = lookup_.user_id_for(command.session_id);
            const auto match = parse_match_command_body(command.body);
            if (session_user_id.empty() || !match.has_value() ||
                !validate_match_command_body(*match) ||
                match->user_id != session_user_id) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_match_status");
                return true;
            }
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "matchmaking_backend_unavailable");
                return true;
            }

            nlohmann::json payload{
                {"user_id", match->user_id},
                {"mode", match->mode},
            };
            bridge_route(bridge_.get(),
                         v2::service::ServiceId::kMatchmaking,
                         "match_status",
                         payload.dump(),
                         [&](const nlohmann::json& resp) {
                             emit(net::protocol::kMatchStatusResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  resp.dump());
                         },
                         [&](const std::string& reason) {
                             emit(net::protocol::kErrorResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                                  reason);
                         });
            return true;
        }
        case GatewayCommandType::kLeaderboardSubmit: {
            const auto session_user_id = lookup_.user_id_for(command.session_id);
            const auto submit = parse_leaderboard_submit_command_body(command.body);
            if (session_user_id.empty() || !submit.has_value() ||
                !validate_leaderboard_submit_command_body(*submit) ||
                submit->user_id != session_user_id) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_leaderboard_submit");
                return true;
            }
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "leaderboard_backend_unavailable");
                return true;
            }

            nlohmann::json payload{
                {"user_id", submit->user_id},
                {"display_name", submit->display_name},
                {"score", submit->score},
            };
            bridge_route(bridge_.get(),
                         v2::service::ServiceId::kLeaderboard,
                         "leaderboard_submit",
                         payload.dump(),
                         [&](const nlohmann::json& resp) {
                             emit(net::protocol::kLeaderboardSubmitResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  resp.dump());
                         },
                         [&](const std::string& reason) {
                             emit(net::protocol::kErrorResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                                  reason);
                         });
            return true;
        }
        case GatewayCommandType::kLeaderboardTop: {
            if (lookup_.user_id_for(command.session_id).empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
                return true;
            }
            const auto top_k = parse_leaderboard_top_command_body(command.body);
            if (!top_k.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_leaderboard_top");
                return true;
            }
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "leaderboard_backend_unavailable");
                return true;
            }

            nlohmann::json payload{{"k", *top_k}};
            bridge_route(bridge_.get(),
                         v2::service::ServiceId::kLeaderboard,
                         "leaderboard_top",
                         payload.dump(),
                         [&](const nlohmann::json& resp) {
                             emit(net::protocol::kLeaderboardTopResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  resp.dump());
                         },
                         [&](const std::string& reason) {
                             emit(net::protocol::kErrorResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                                  reason);
                         });
            return true;
        }
        case GatewayCommandType::kLeaderboardRank: {
            if (lookup_.user_id_for(command.session_id).empty()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
                return true;
            }
            const auto rank_user_id = parse_leaderboard_rank_command_body(command.body);
            if (!rank_user_id.has_value()) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                     "invalid_leaderboard_rank");
                return true;
            }
            if (!bridge_) {
                emit(net::protocol::kErrorResponse,
                     command.session_id,
                     command.request_id,
                     static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                     "leaderboard_backend_unavailable");
                return true;
            }

            nlohmann::json payload{{"user_id", *rank_user_id}};
            bridge_route(bridge_.get(),
                         v2::service::ServiceId::kLeaderboard,
                         "leaderboard_rank",
                         payload.dump(),
                         [&](const nlohmann::json& resp) {
                             emit(net::protocol::kLeaderboardRankResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  resp.dump());
                         },
                         [&](const std::string& reason) {
                             emit(net::protocol::kErrorResponse,
                                  command.session_id,
                                  command.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kSessionNotFound),
                                  reason);
                         });
            return true;
        }
        case GatewayCommandType::kHeartbeat:
        case GatewayCommandType::kEcho:
        case GatewayCommandType::kUnknown:
            return false;
    }

    // v2.2.0: Diagnostics snapshot available via diagnostics().collect()
    // for external polling (HTTP metrics endpoint, health check).
    // The DiagnosticsManager is wired via set_backend_metrics_for_diagnostics()
    // and set_service_registry_for_diagnostics().

    return false;
}

void Runtime::push(v2::player::PlayerEvent event) {
    if (const auto* accepted = std::get_if<v2::player::LoginAcceptedMsg>(&event)) {
        lookup_.set_session_user(accepted->session_id, accepted->user_id);
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
        auto* room_ref = lookup_.room(created->room_id);
        if (room_ref != nullptr) {
            v2::actor::Message started;
            started.header.kind = v2::actor::MessageKind::kUser;
            started.payload = v2::room::BattleStartedMsg{.battle_id = created->battle_id};
            room_ref->tell(std::move(started));
        }
        for (const auto& user_id : created->player_ids) {
            auto* player_ref = lookup_.player(user_id);
            if (player_ref == nullptr) {
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
            player_ref->tell(std::move(assign));
        }
        actor_system_.dispatch_all();

        auto pending = pending_battle_start_.find(created->room_id);
        if (pending != pending_battle_start_.end()) {
            emit(net::protocol::kBattleStartResponse,
                 pending->second.session_id,
                 pending->second.request_id,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 format_battle_started_body(created->room_id, created->battle_id));
            AUDIT_LOG("battle_started", "room_id=" + created->room_id + " user_id=" + (created->player_ids.empty() ? "N/A" : created->player_ids[0]));
            pending_battle_start_.erase(pending);
        }
        broadcast_to_room(created->room_id,
                          net::protocol::kBattleStatePush,
                          format_battle_state_body(created->room_id, created->battle_id));
        return;
    }

    if (const auto* input = std::get_if<v2::battle::BattleInputAcceptedMsg>(&event)) {
        const auto sid = lookup_.session_for_user(input->user_id);
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
        for (const auto session_id : lookup_.sessions_in_room(input->room_id)) {
            if (lookup_.user_id_for(session_id) != input->user_id) {
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
        if (should_emit_battle_frame_push(frame->room_id, frame->frame_number)) {
            broadcast_to_room(frame->room_id,
                              net::protocol::kBattleStatePush,
                              format_battle_frame_body(*frame));
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

        auto* room_ref = lookup_.room(settlement->room_id);
        if (room_ref != nullptr) {
            v2::actor::Message room_settlement;
            room_settlement.header.kind = v2::actor::MessageKind::kUser;
            room_settlement.payload = v2::room::BattleSettlementMsg{
                .battle_id = settlement->battle_id,
                .reason = v2::battle::to_string(settlement->reason),
            };
            room_ref->tell(std::move(room_settlement));
        }

        for (const auto& user_id : settlement->participant_user_ids) {
            auto* player_ref = lookup_.player(user_id);
            if (player_ref == nullptr) {
                continue;
            }
            v2::actor::Message player_settlement;
            player_settlement.header.kind = v2::actor::MessageKind::kUser;
            player_settlement.payload = v2::player::BattleSettlementMsg{
                .battle_id = settlement->battle_id,
                .reason = v2::battle::to_string(settlement->reason),
            };
            player_ref->tell(std::move(player_settlement));
        }
        actor_system_.dispatch_all();

        auto ack_it = pending_settlement_acks_.find(settlement->battle_id);
        if (ack_it != pending_settlement_acks_.end() &&
            ack_it->second.received_acks >= ack_it->second.expected_acks) {
            pending_settlement_acks_.erase(ack_it);
        }

        broadcast_to_room(settlement->room_id,
                          net::protocol::kBattleStatePush,
                          format_battle_settlement_body(*settlement));
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
            .max_frames = read_uint32_override("V2_BATTLE_MAX_FRAMES", 3),
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

// ── Match-found handling ─────────────────────────────────────────────

void Runtime::send_match_found_pushes(const v2::match::MatchResult& result,
                                       const std::string& room_id) {
    // Build MatchFoundPayload from the result
    v2::match::MatchFoundPayload payload;
    payload.match_id = result.match_id;
    payload.mode = v2::match::to_string(result.mode);
    payload.room_id = room_id;
    for (const auto& pid : result.player_ids) {
        v2::match::MatchPlayerInfo info;
        info.user_id = pid;
        info.mmr = result.avg_mmr;
        payload.players.push_back(std::move(info));
    }
    const auto payload_str = payload.to_json_str();

    // Send kMatchFoundPush to each matched player
    for (const auto& pid : result.player_ids) {
        const auto sid = lookup_.session_for_user(pid);
        if (sid.has_value()) {
            emit(net::protocol::kMatchFoundPush,
                 *sid,
                 0,
                 static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                 payload_str);
            AUDIT_LOG("match_found_push",
                      "match_id=" + result.match_id + " user_id=" + pid +
                          " room_id=" + room_id);
        }
    }
}

std::string Runtime::create_room_from_match(const v2::match::MatchResult& result) {
    // Generate a deterministic room_id from the match_id
    const std::string room_id = "room_" + result.match_id;

    // Idempotency: if room already exists, return its id
    if (lookup_.room(room_id) != nullptr) {
        SPDLOG_INFO("Room {} already exists for match {}, skipping creation",
                    room_id, result.match_id);
        return room_id;
    }

    // Use the first player as the "owner" for room creation
    if (result.player_ids.empty()) {
        SPDLOG_ERROR("Cannot create room for match {}: no players", result.match_id);
        return {};
    }

    const std::string& owner_id = result.player_ids.front();

    if (bridge_) {
        // Bridge path: delegate room creation to room_backend
        nlohmann::json room_payload{
            {"user_id", owner_id},
            {"room_id", room_id},
        };
        auto room_result = bridge_->route(v2::service::ServiceId::kRoom,
                                           "room_create",
                                           room_payload.dump());
        if (!room_result.success) {
            SPDLOG_ERROR("Bridge room_create failed for match {}: {}",
                         result.match_id, room_result.response_payload);
            return {};
        }

        // Associate all matched players with the room in the lookup
        for (const auto& pid : result.player_ids) {
            const auto sid = lookup_.session_for_user(pid);
            if (sid.has_value()) {
                lookup_.set_session_room(*sid, room_id);
            }
        }

        // Join all non-owner players to the room
        for (std::size_t i = 1; i < result.player_ids.size(); ++i) {
            const auto& pid = result.player_ids[i];
            nlohmann::json join_payload{
                {"user_id", pid},
                {"room_id", room_id},
            };
            auto join_result = bridge_->route(v2::service::ServiceId::kRoom,
                                               "room_join",
                                               join_payload.dump());
            if (!join_result.success) {
                SPDLOG_WARN("Bridge room_join failed for {} in match {}: {}",
                            pid, result.match_id, join_result.response_payload);
            }
        }

        AUDIT_LOG("match_room_created",
                  "match_id=" + result.match_id + " room_id=" + room_id);
        return room_id;
    }

    // Local path: create RoomActor
    auto room_actor = actor_system_.create_actor(
        std::make_unique<v2::room::RoomActor>(*this));
    lookup_.set_room(room_id, room_actor);

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::room::CreateRoomMsg{
        .room_id = room_id,
        .owner_user_id = owner_id,
        .owner_actor_id = lookup_.player(owner_id)
                              ? lookup_.player(owner_id)->actor_id()
                              : v2::actor::ActorId{0},
    };
    room_actor.tell(std::move(create));

    // Assign room to all players and join non-owners
    for (std::size_t i = 0; i < result.player_ids.size(); ++i) {
        const auto& pid = result.player_ids[i];
        const auto sid = lookup_.session_for_user(pid);
        if (sid.has_value()) {
            lookup_.set_session_room(*sid, room_id);
        }

        auto* player_ref = lookup_.player(pid);
        if (player_ref != nullptr) {
            v2::actor::Message assign;
            assign.header.kind = v2::actor::MessageKind::kUser;
            assign.payload = v2::player::RoomAssignedMsg{
                .room_actor_id = room_actor.actor_id(),
                .room_id = room_id,
            };
            player_ref->tell(std::move(assign));

            if (i > 0) {
                // Non-owner: send JoinRoomMsg
                v2::actor::Message join;
                join.header.kind = v2::actor::MessageKind::kUser;
                join.payload = v2::room::JoinRoomMsg{
                    .user_id = pid,
                    .player_actor_id = player_ref->actor_id(),
                };
                room_actor.tell(std::move(join));
            }
        }
    }

    actor_system_.dispatch_all();
    AUDIT_LOG("match_room_created",
              "match_id=" + result.match_id + " room_id=" + room_id);
    return room_id;
}

void Runtime::ready_all_players(const v2::match::MatchResult& result,
                                 const std::string& room_id) {
    for (const auto& pid : result.player_ids) {
        if (bridge_) {
            nlohmann::json ready_payload{
                {"user_id", pid},
                {"room_id", room_id},
                {"ready", true},
            };
            auto ready_result = bridge_->route(v2::service::ServiceId::kRoom,
                                                "room_ready",
                                                ready_payload.dump());
            if (!ready_result.success) {
                SPDLOG_WARN("Bridge room_ready failed for {} in room {}: {}",
                            pid, room_id, ready_result.response_payload);
            }
        } else {
            auto* room_ref = lookup_.room(room_id);
            if (room_ref == nullptr) {
                SPDLOG_WARN("Room {} not found for ready_all_players", room_id);
                continue;
            }
            v2::actor::Message set_ready;
            set_ready.header.kind = v2::actor::MessageKind::kUser;
            set_ready.payload = v2::room::SetReadyMsg{
                .user_id = pid,
                .ready = true,
            };
            room_ref->tell(std::move(set_ready));
        }
    }
    if (!bridge_) {
        actor_system_.dispatch_all();
    }
}

void Runtime::start_battle_for_room(const std::string& room_id,
                                     const std::string& user_id) {
    if (bridge_) {
        nlohmann::json battle_payload{
            {"user_id", user_id},
            {"room_id", room_id},
        };
        auto room_result = bridge_->route(v2::service::ServiceId::kRoom,
                                           "room_start_battle",
                                           battle_payload.dump());
        if (!room_result.success) {
            SPDLOG_ERROR("Bridge room_start_battle failed for room {}: {}",
                         room_id, room_result.response_payload);
            return;
        }

        auto room_resp = nlohmann::json::parse(
            room_result.response_payload, nullptr, false);
        if (room_resp.is_discarded() || room_resp.value("status", "") != "ok") {
            SPDLOG_ERROR("start_battle_for_room rejected for room {}",
                         room_id);
            return;
        }

        // Handle forward cascade if present
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
                        std::string battle_id = battle_resp.value("battle_id", "");
                        if (!battle_id.empty()) {
                            battles_by_room_id_[room_id] = v2::actor::ActorRef{};
                            room_to_battle_id_[room_id] = battle_id;
                        }
                        AUDIT_LOG("match_battle_started",
                                  "room_id=" + room_id +
                                      " battle_id=" + battle_id);
                    }
                }
            }
        }
        return;
    }

    // Local path: send StartBattleMsg
    auto* room_ref = lookup_.room(room_id);
    if (room_ref == nullptr) {
        SPDLOG_ERROR("Room {} not found for start_battle_for_room", room_id);
        return;
    }
    pending_battle_start_[room_id] = PendingResponse{
        .session_id = 0,  // no specific session for auto-start
        .request_id = 0,
    };
    v2::actor::Message start;
    start.header.kind = v2::actor::MessageKind::kUser;
    start.payload = v2::room::StartBattleMsg{.requester_user_id = user_id};
    room_ref->tell(std::move(start));
    actor_system_.dispatch_all();
}

void Runtime::on_match_found(const v2::match::MatchResult& result) {
    // Idempotency: skip if this match was already processed
    if (!processed_match_ids_.insert(result.match_id).second) {
        SPDLOG_INFO("Match {} already processed, skipping", result.match_id);
        return;
    }

    SPDLOG_INFO("Processing match_found: match_id={} mode={} players={}",
                result.match_id,
                v2::match::to_string(result.mode),
                fmt::join(result.player_ids, ","));

    // Step 1: Create room for the match
    const std::string room_id = create_room_from_match(result);
    if (room_id.empty()) {
        SPDLOG_ERROR("Failed to create room for match {}", result.match_id);
        processed_match_ids_.erase(result.match_id);
        return;
    }

    // Step 2: Send MatchFound push notifications to all matched players
    send_match_found_pushes(result, room_id);

    // Step 3: Set all matched players ready
    ready_all_players(result, room_id);

    // Step 4: Start battle
    // Use the first player as the requester for battle start
    const std::string& starter_id = result.player_ids.front();
    start_battle_for_room(room_id, starter_id);

    AUDIT_LOG("match_to_battle_complete",
              "match_id=" + result.match_id + " room_id=" + room_id);
}

// ── Player management ────────────────────────────────────────────────

v2::actor::ActorRef Runtime::get_or_create_player(const std::string& user_id) {
    auto* existing = lookup_.player(user_id);
    if (existing != nullptr) {
        return *existing;
    }
    auto actor = actor_system_.create_actor(std::make_unique<v2::player::PlayerActor>(*this));
    lookup_.set_player(user_id, actor);
    return actor;
}

std::string Runtime::session_user_id(SessionId session_id) const {
    return lookup_.user_id_for(session_id);
}

std::string Runtime::session_room_id(SessionId session_id) const {
    return lookup_.room_id_for(session_id);
}

void Runtime::mark_session_room(SessionId session_id, const std::string& room_id) {
    lookup_.set_session_room(session_id, room_id);
}

void Runtime::clear_session_room(SessionId session_id) {
    lookup_.erase_session_room(session_id);
}

void Runtime::mark_room_battle(const std::string& room_id, const std::string& battle_id) {
    room_to_battle_id_[room_id] = battle_id;
}

std::string Runtime::battle_id_for_room(const std::string& room_id) const {
    auto it = room_to_battle_id_.find(room_id);
    return it != room_to_battle_id_.end() ? it->second : std::string{};
}

const std::unordered_map<SessionId, std::string>& Runtime::session_users() const {
    return lookup_.session_users();
}

std::optional<SessionId> Runtime::session_id_for_user(const std::string& user_id) const {
    return lookup_.session_for_user(user_id);
}

void Runtime::process_battle_finished(const v2::battle::BattleFinishedMsg& finished) {
    pending_battle_timeout_.erase(finished.battle_id);
    battles_by_room_id_.erase(finished.room_id);
    room_to_battle_id_.erase(finished.room_id);

    if (bridge_) {
        nlohmann::json payload{
            {"room_id", finished.room_id},
            {"battle_id", finished.battle_id},
        };
        auto result = bridge_->route(v2::service::ServiceId::kRoom,
                                     "room_battle_finished",
                                     payload.dump(),
                                     finished.room_id);
        if (!result.success) {
            SPDLOG_WARN("Runtime: failed to mark room battle finished for room={} battle={}",
                        finished.room_id,
                        finished.battle_id);
        }
    }

    if (!finished.triggering_user_id.empty()) {
        const auto sid = lookup_.session_for_user(finished.triggering_user_id);
        if (sid.has_value()) {
            auto pending = pending_battle_end_.find(*sid);
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

    auto* room_ref = lookup_.room(finished.room_id);
    if (room_ref != nullptr) {
        v2::actor::Message ended;
        ended.header.kind = v2::actor::MessageKind::kUser;
        ended.payload = v2::room::BattleEndedMsg{
            .battle_id = finished.battle_id,
            .reason = v2::battle::to_string(finished.reason),
        };
        room_ref->tell(std::move(ended));
    }

    for (const auto& [user_id, player_actor] : lookup_.players()) {
        v2::actor::Message ended;
        ended.header.kind = v2::actor::MessageKind::kUser;
        ended.payload = v2::player::BattleEndedMsg{
            .battle_id = finished.battle_id,
            .reason = v2::battle::to_string(finished.reason),
        };
        player_actor.tell(std::move(ended));
    }
    actor_system_.dispatch_all();

    broadcast_to_room(finished.room_id,
                      net::protocol::kBattleStatePush,
                      format_battle_finished_body(finished));
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

void Runtime::complete_bridge_battle_input(
    SessionId session_id,
    std::uint32_t request_id,
    const std::string& room_id,
    const std::string& battle_id,
    GatewayServiceBridge::BackendRoutingResult result) {
    if (!result.success) {
        emit(net::protocol::kErrorResponse,
             session_id,
             request_id,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleBackendUnavailable),
             "backend_error");
        return;
    }

    auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
    if (resp.is_discarded() || resp.value("status", "") != "ok") {
        const auto reason = resp.is_discarded()
            ? std::string{"input_rejected"}
            : resp.value("reason", std::string{"input_rejected"});
        emit(net::protocol::kErrorResponse,
             session_id,
             request_id,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
             reason);
        return;
    }

    emit(net::protocol::kBattleInputResponse,
         session_id,
         request_id,
         static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
         format_battle_input_response_body(resp.value("input_seq", std::uint64_t{0})));

    if (!resp.contains("push_to_sessions") || !resp["push_to_sessions"].is_array()) {
        return;
    }
    for (const auto& push : resp["push_to_sessions"]) {
        const auto kind = push.value("kind", std::string{});
        if (kind == "frame_advanced") {
            const auto current_battle = room_to_battle_id_.find(room_id);
            if (current_battle == room_to_battle_id_.end() ||
                current_battle->second != battle_id) {
                continue;
            }
            const auto frame_number = push.value("frame_number", std::uint64_t{0});
            if (should_emit_battle_frame_push(room_id, frame_number)) {
                broadcast_to_room(room_id,
                                  net::protocol::kBattleStatePush,
                                  push.dump());
            }
        } else if (kind == "battle_finished") {
            process_bridge_battle_finished_push(push, room_id, battle_id);
        }
    }
}

void Runtime::process_bridge_battle_finished_push(
    const nlohmann::json& push,
    const std::string& room_id,
    const std::string& fallback_battle_id) {
    if (!push.is_object() || push.value("kind", "") != "battle_finished") {
        return;
    }
    const auto battle_id = push.value("battle_id", fallback_battle_id);
    if (battle_id.empty() || !completed_bridge_battle_ids_.insert(battle_id).second) {
        return;
    }

    auto normalized_push = push;
    normalized_push["battle_id"] = battle_id;
    submit_battle_finished_push_to_leaderboard(normalized_push, room_id);
    broadcast_to_room(room_id,
                      net::protocol::kBattleStatePush,
                      normalized_push.dump());

    nlohmann::json room_finish_payload{
        {"room_id", room_id},
        {"battle_id", battle_id},
    };
    auto room_finish = bridge_->route(v2::service::ServiceId::kRoom,
                                      "room_battle_finished",
                                      room_finish_payload.dump(),
                                      room_id);
    if (!room_finish.success) {
        SPDLOG_WARN("Runtime: failed to mark bridge room battle finished for room={} battle={}",
                    room_id,
                    battle_id);
    }

    const auto current_battle = room_to_battle_id_.find(room_id);
    if (current_battle != room_to_battle_id_.end() && current_battle->second == battle_id) {
        battles_by_room_id_.erase(room_id);
        room_to_battle_id_.erase(current_battle);
        last_emitted_battle_frame_.erase(room_id);
    }
}

void Runtime::submit_battle_finished_push_to_leaderboard(const nlohmann::json& push,
                                                         const std::string& room_id) {
    if (!push.is_object() || push.value("kind", "") != "battle_finished") {
        return;
    }
    if (!push.contains("scores") || !push["scores"].is_array()) {
        return;
    }

    std::vector<v2::battle::BattleScore> scores;
    scores.reserve(push["scores"].size());
    for (const auto& score : push["scores"]) {
        const auto user_id = score.value("user_id", std::string{});
        if (user_id.empty()) {
            continue;
        }
        scores.push_back(v2::battle::BattleScore{
            .user_id = user_id,
            .score = score.value("score", std::int64_t{0}),
        });
    }

    submit_battle_settlement_to_leaderboard(
        push.value("battle_id", std::string{}),
        room_id,
        push.value("reason", std::string{"finished"}),
        scores);
}

void Runtime::submit_battle_settlement_to_leaderboard(
    const std::string& battle_id,
    const std::string& room_id,
    const std::string& reason,
    const std::vector<v2::battle::BattleScore>& scores) {
    if (battle_id.empty() || !bridge_) {
        return;
    }

    for (const auto& score : scores) {
        if (score.user_id.empty()) {
            continue;
        }
        const auto idempotency_key = battle_id + ":" + score.user_id;
        if (!leaderboard_settlement_keys_.insert(idempotency_key).second) {
            continue;
        }

        nlohmann::json payload{
            {"user_id", score.user_id},
            {"display_name", score.user_id},
            {"score", score.score},
            {"idempotency_key", idempotency_key},
            {"source", "battle_settlement"},
            {"battle_id", battle_id},
            {"room_id", room_id},
            {"reason", reason},
        };

        auto result = bridge_->route(v2::service::ServiceId::kLeaderboard,
                                     "leaderboard_submit",
                                     payload.dump(),
                                     score.user_id);
        if (!result.success) {
            leaderboard_settlement_keys_.erase(idempotency_key);
            SPDLOG_ERROR("leaderboard settlement submit failed battle_id={} user_id={} reason={} service_error={}",
                         battle_id,
                         score.user_id,
                         reason,
                         static_cast<int>(result.error));
        }
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
    submit_battle_settlement_to_leaderboard(
        settlement.battle_id,
        settlement.room_id,
        v2::battle::to_string(settlement.reason),
        settlement.result.scores);
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

void Runtime::broadcast_to_room(const std::string& room_id,
                                std::uint16_t message_id,
                                std::string body) {
    for (const auto session_id : lookup_.sessions_in_room(room_id)) {
        emit(message_id,
             session_id,
             0,
             static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
             body);
    }
}

bool Runtime::should_emit_battle_frame_push(const std::string& room_id,
                                            std::uint64_t frame_number) {
    if (battle_frame_push_every_ == 0) {
        battle_frame_push_every_ = read_nonzero_uint32_override(
            "V2_BATTLE_FRAME_PUSH_EVERY", 1);
    }
    if (battle_frame_push_every_ <= 1 || frame_number == 0) {
        std::scoped_lock lock(battle_frame_push_mutex_);
        last_emitted_battle_frame_[room_id] = frame_number;
        return true;
    }

    std::scoped_lock lock(battle_frame_push_mutex_);
    auto& last = last_emitted_battle_frame_[room_id];
    if (last == 0 ||
        frame_number <= last ||
        frame_number - last >= battle_frame_push_every_) {
        last = frame_number;
        return true;
    }
    return false;
}

bool Runtime::battle_route_offload_enabled() {
    return battle_route_worker_count_ > 0 &&
           !battle_route_workers_.empty() &&
           static_cast<bool>(battle_route_completion_dispatcher_);
}

void Runtime::start_battle_route_workers() {
    if (battle_route_worker_count_ == 0 ||
        !battle_route_completion_dispatcher_ ||
        !battle_route_workers_.empty()) {
        return;
    }
    battle_route_stopping_.store(false, std::memory_order_release);
    battle_route_workers_.reserve(battle_route_worker_count_);
    for (std::uint32_t i = 0; i < battle_route_worker_count_; ++i) {
        battle_route_workers_.emplace_back([this]() {
            for (;;) {
                std::function<void()> task;
                {
                    std::unique_lock<std::mutex> lock(battle_route_mutex_);
                    battle_route_cv_.wait(lock, [this]() {
                        return battle_route_stopping_.load(std::memory_order_acquire) ||
                               !battle_route_tasks_.empty();
                    });
                    if (battle_route_stopping_.load(std::memory_order_acquire) &&
                        battle_route_tasks_.empty()) {
                        return;
                    }
                    task = std::move(battle_route_tasks_.front());
                    battle_route_tasks_.pop_front();
                    battle_route_queued_tasks_.fetch_sub(1, std::memory_order_relaxed);
                }
                if (task) {
                    task();
                }
            }
        });
    }
}

void Runtime::stop_battle_route_workers() {
    {
        std::scoped_lock lock(battle_route_mutex_);
        battle_route_stopping_.store(true, std::memory_order_release);
        battle_route_tasks_.clear();
        battle_route_queued_tasks_.store(0, std::memory_order_relaxed);
    }
    battle_route_cv_.notify_all();
    for (auto& worker : battle_route_workers_) {
        if (worker.joinable()) {
            worker.join();
        }
    }
    battle_route_workers_.clear();
}

bool Runtime::enqueue_battle_route_task(std::function<void()> task) {
    if (!task) {
        return false;
    }
    const auto enqueued_at = std::chrono::steady_clock::now();
    auto measured_task = [this, enqueued_at, task = std::move(task)]() mutable {
        const auto started_at = std::chrono::steady_clock::now();
        const auto queue_wait_us = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(started_at - enqueued_at).count());
        battle_route_total_queue_wait_us_.fetch_add(queue_wait_us, std::memory_order_relaxed);
        update_max(battle_route_max_queue_wait_us_, queue_wait_us);

        task();

        const auto task_execution_us = static_cast<std::uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(
                std::chrono::steady_clock::now() - started_at).count());
        battle_route_total_task_execution_us_.fetch_add(task_execution_us,
                                                         std::memory_order_relaxed);
        update_max(battle_route_max_task_execution_us_, task_execution_us);
        battle_route_completed_tasks_.fetch_add(1, std::memory_order_relaxed);
    };
    {
        std::scoped_lock lock(battle_route_mutex_);
        if (battle_route_stopping_.load(std::memory_order_acquire) ||
            battle_route_tasks_.size() >= battle_route_queue_capacity_) {
            battle_route_rejected_tasks_.fetch_add(1, std::memory_order_relaxed);
            return false;
        }
        battle_route_tasks_.push_back(std::move(measured_task));
        battle_route_queued_tasks_.fetch_add(1, std::memory_order_relaxed);
    }
    battle_route_cv_.notify_one();
    return true;
}

}  // namespace v2::gateway
