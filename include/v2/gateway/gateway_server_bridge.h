#pragma once

#include "game/gateway/gateway_server.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

#include <memory>
#include <unordered_map>

namespace v2::gateway {

class GatewayServerShadowBridge final : public game::gateway::GatewayPacketBridge,
                                        public DownstreamSessionWriteSink {
public:
    explicit GatewayServerShadowBridge(bool emit_responses = false)
        : adapter_(actor_system_, this),
          runtime_(actor_system_, adapter_),
          emit_responses_(emit_responses) {
        gateway_actor_ = runtime_.create_gateway_actor();
        adapter_.bind_gateway(gateway_actor_);
    }

    void on_packet(const std::shared_ptr<net::Session>& session,
                   const net::Session::PacketMessage& message) override;
    void on_close(const std::shared_ptr<net::Session>& session) override;
    void deliver(SessionWrite write) override;

    [[nodiscard]] bool emit_responses() const noexcept { return emit_responses_; }

private:
    [[nodiscard]] SessionId get_or_create_session_id(const std::shared_ptr<net::Session>& session);

    v2::runtime::ActorSystem actor_system_;
    SessionAdapter adapter_;
    Runtime runtime_;
    v2::actor::ActorRef gateway_actor_;
    bool emit_responses_ = false;
    std::unordered_map<net::Session*, SessionId> session_ids_by_ptr_;
    std::unordered_map<SessionId, std::weak_ptr<net::Session>> sessions_by_id_;
    SessionId next_session_id_ = 1;
};

}  // namespace v2::gateway
