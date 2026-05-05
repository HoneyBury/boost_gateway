#include "game/login/login_service.h"

#include "app/audit_log.h"
#include "net/protocol.h"

#include <optional>
#include <string_view>

namespace game::login {

namespace {

struct ParsedLoginRequest {
    std::string user_id;
    std::string token;
    std::optional<std::string> display_name;
};

std::optional<ParsedLoginRequest> parse_login_request(std::string_view body) {
    const auto first_sep = body.find('|');
    if (first_sep == std::string_view::npos) {
        return std::nullopt;
    }

    const auto second_sep = body.find('|', first_sep + 1);
    ParsedLoginRequest request;
    request.user_id = std::string(body.substr(0, first_sep));

    if (second_sep == std::string_view::npos) {
        request.token = std::string(body.substr(first_sep + 1));
        return request;
    }

    request.token = std::string(body.substr(first_sep + 1, second_sep - first_sep - 1));
    request.display_name = std::string(body.substr(second_sep + 1));
    return request;
}

}  // namespace

LoginService::LoginService(gateway::SessionManager& session_manager,
                           gateway::PushService& push_service,
                           room::RoomManager& room_manager,
                           const TokenValidator& token_validator,
                           gateway::GatewayMetrics& metrics)
    : session_manager_(session_manager),
      push_service_(push_service),
      room_manager_(room_manager),
      token_validator_(token_validator),
      metrics_(metrics) {}

void LoginService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kLoginRequest,
        [this](const net::DispatchContext& context) {
            const auto request = parse_login_request(context.body);
            if (!request || request->user_id.empty()) {
                context.session->send(
                    net::protocol::kErrorResponse,
                    context.request_id,
                    static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                    net::protocol::to_string(net::protocol::ErrorCode::kInvalidUserId));
                return;
            }

            const auto validation_result =
                token_validator_.validate(request->user_id, request->token, request->display_name);
            if (!validation_result.ok) {
                AUDIT_LOG("login_failure",
                          "user=" + request->user_id + " reason=" + (validation_result.expired ? "token_expired" : "invalid_token"));
                push_service_.send_error(
                    context.session, context.request_id,
                    validation_result.expired ? net::protocol::ErrorCode::kTokenExpired
                                              : net::protocol::ErrorCode::kInvalidToken);
                return;
            }

            auto replaced_session = session_manager_.authenticate(
                context.session,
                gateway::SessionManager::LoginContext{
                    .user_id = validation_result.user_id,
                    .display_name = validation_result.display_name,
                });
            if (replaced_session && replaced_session != context.session) {
                const auto transferred = room_manager_.transfer_session(replaced_session, context.session);
                push_service_.send_push(replaced_session,
                                        net::protocol::kSessionKickedPush,
                                        transferred ? "session_kicked:duplicate_login:room_transferred"
                                                    : "session_kicked:duplicate_login");
                replaced_session->stop();
            }

            metrics_.on_login_success();
            AUDIT_LOG("login_success", "user=" + validation_result.user_id);
            auto response_body = "login_ok:" + validation_result.user_id + ":" + validation_result.display_name;
            std::optional<std::string> resumed_body;
            if (const auto room_snapshot = room_manager_.room_snapshot_of(context.session)) {
                response_body += ":room=" + room_snapshot->room_id;
                resumed_body = "session_resumed:" + room_snapshot->room_id +
                               (room_snapshot->battle_started ? ":battle=1" : ":battle=0");
            }

            push_service_.send_ok(context.session,
                                  net::protocol::kLoginResponse,
                                  context.request_id,
                                  std::move(response_body));
            if (resumed_body) {
                push_service_.send_push(
                    context.session, net::protocol::kSessionResumedPush, std::move(*resumed_body));
            }
        });
}

}  // namespace game::login
