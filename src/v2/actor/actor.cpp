#include "v2/actor/actor.h"

namespace v2::actor {

void Actor::tell(const ActorRef& target, Message message) const {
    if (!target.is_valid()) {
        return;
    }
    message.header.source_actor = id();
    message.header.target_actor = target.actor_id();
    target.tell(std::move(message));
}

}  // namespace v2::actor
