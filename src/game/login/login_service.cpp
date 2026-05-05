#include "game/login/login_service.h"

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
                           const TokenValidator& token_validator,
                           gateway::GatewayMetrics& metrics)
    : session_manager_(session_manager), token_validator_(token_validator), metrics_(metrics) {}

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
                context.session->send(
                    net::protocol::kErrorResponse,
                    context.request_id,
                    static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken),
                    net::protocol::to_string(net::protocol::ErrorCode::kInvalidToken));
                return;
            }

            auto replaced_session = session_manager_.authenticate(
                context.session,
                gateway::SessionManager::LoginContext{
                    .user_id = validation_result.user_id,
                    .display_name = validation_result.display_name,
                });
            if (replaced_session && replaced_session != context.session) {
                replaced_session->send(
                    net::protocol::kErrorResponse,
                    0,
                    static_cast<std::int32_t>(net::protocol::ErrorCode::kDuplicateLogin),
                    net::protocol::to_string(net::protocol::ErrorCode::kDuplicateLogin));
                replaced_session->stop();
            }

            metrics_.on_login_success();
            context.session->send(net::protocol::kLoginResponse,
                                  context.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  "login_ok:" + validation_result.user_id + ":" +
                                      validation_result.display_name);
        });
}

}  // namespace game::login
