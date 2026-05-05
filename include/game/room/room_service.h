#pragma once

#include "net/message_dispatcher.h"

namespace game::room {

class RoomService {
public:
    void register_handlers(net::MessageDispatcher& dispatcher) const;
};

}  // namespace game::room
