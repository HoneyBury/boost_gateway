#pragma once

#include "net/message_dispatcher.h"

namespace game::gateway {

class GatewayService {
public:
    void register_handlers(net::MessageDispatcher& dispatcher) const;
};

}  // namespace game::gateway
