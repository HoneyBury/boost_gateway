#pragma once

#include "net/message_dispatcher.h"

namespace game::login {

class LoginService {
public:
    void register_handlers(net::MessageDispatcher& dispatcher) const;
};

}  // namespace game::login
