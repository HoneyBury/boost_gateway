#include "game/login/login_service.h"

#include "net/protocol.h"

namespace game::login {

void LoginService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kLoginRequest,
        [](const std::shared_ptr<net::Session>& session, std::string body) {
            session->send(net::protocol::kLoginResponse, "login_ok:" + body);
        });
}

}  // namespace game::login
