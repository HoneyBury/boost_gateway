#pragma once

#include <vector>

#include "v2/actor/actor_ref.h"
#include "v2/gateway/gateway_actor.h"
#include "v2/runtime/actor_system.h"

namespace v2::gateway {

class SessionAdapter final : public SessionWriteSink {
public:
    explicit SessionAdapter(v2::runtime::ActorSystem& actor_system)
        : actor_system_(actor_system) {}

    void bind_gateway(v2::actor::ActorRef gateway_actor) noexcept;
    [[nodiscard]] std::vector<SessionWrite> handle_incoming(ClientEnvelope envelope);

    void push(SessionWrite write) override;

private:
    v2::runtime::ActorSystem& actor_system_;
    v2::actor::ActorRef gateway_actor_;
    std::vector<SessionWrite> outbox_;
};

}  // namespace v2::gateway
