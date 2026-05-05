#pragma once

#include "net/message_dispatcher.h"

namespace game::battle {

class BattleService {
public:
    void register_handlers(net::MessageDispatcher& dispatcher) const;
};

}  // namespace game::battle
