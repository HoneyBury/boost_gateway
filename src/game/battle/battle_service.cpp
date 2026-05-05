#include "game/battle/battle_service.h"

#include "net/protocol.h"

namespace game::battle {

void BattleService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kBattleStartRequest,
        [](const std::shared_ptr<net::Session>& session, std::string body) {
            session->send(net::protocol::kBattleStartResponse, "battle_started:" + body);
        });
}

}  // namespace game::battle
