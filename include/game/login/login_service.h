#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/login/token_validator.h"
#include "net/message_dispatcher.h"

namespace game::login {

class LoginService {
public:
    LoginService(gateway::SessionManager& session_manager,
                 const TokenValidator& token_validator,
                 gateway::GatewayMetrics& metrics);

    void register_handlers(net::MessageDispatcher& dispatcher) const;

private:
    gateway::SessionManager& session_manager_;
    const TokenValidator& token_validator_;
    gateway::GatewayMetrics& metrics_;
};

}  // namespace game::login
