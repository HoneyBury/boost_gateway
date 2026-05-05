#include "game/login/login_service.h"

#include "net/protocol.h"

namespace game::login {

LoginService::LoginService(gateway::SessionManager& session_manager, gateway::GatewayMetrics& metrics)
    : session_manager_(session_manager), metrics_(metrics) {}

void LoginService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kLoginRequest,
        [this](const net::DispatchContext& context) {
            if (context.body.empty()) {
                context.session->send(
                    net::protocol::kErrorResponse,
                    context.request_id,
                    static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId),
                    net::protocol::to_string(net::protocol::ErrorCode::kInvalidUserId));
                return;
            }

            auto replaced_session = session_manager_.authenticate(context.session, context.body);
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
                                  "login_ok:" + context.body);
        });
}

}  // namespace game::login
