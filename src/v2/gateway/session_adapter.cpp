#include "v2/gateway/session_adapter.h"

#include <utility>

namespace v2::gateway {

void SessionAdapter::bind_gateway(v2::actor::ActorRef gateway_actor) noexcept {
    gateway_actor_ = gateway_actor;
}

std::vector<SessionWrite> SessionAdapter::handle_incoming(ClientEnvelope envelope) {
    outbox_.clear();
    if (!gateway_actor_.is_valid()) {
        return {};
    }

    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.payload = std::move(envelope);
    gateway_actor_.tell(std::move(message));
    actor_system_.dispatch_all();
    return outbox_;
}

void SessionAdapter::push(SessionWrite write) {
    outbox_.push_back(std::move(write));
}

}  // namespace v2::gateway
