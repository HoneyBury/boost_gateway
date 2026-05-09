#include "v2/gateway/gateway_server_bridge.h"

#include <utility>

namespace v2::gateway {

void GatewayServerShadowBridge::on_packet(const std::shared_ptr<net::Session>& session,
                                          const net::Session::PacketMessage& message) {
    (void)adapter_.handle_incoming(ClientEnvelope{
        .session_id = get_or_create_session_id(session),
        .protocol_message_id = message.message_id,
        .request_id = message.request_id,
        .error_code = message.error_code,
        .flags = message.flags,
        .body = message.body,
    });
}

void GatewayServerShadowBridge::on_close(const std::shared_ptr<net::Session>& session) {
    auto it = session_ids_by_ptr_.find(session.get());
    if (it == session_ids_by_ptr_.end()) {
        return;
    }

    runtime_.on_session_closed(it->second);
    sessions_by_id_.erase(it->second);
    session_ids_by_ptr_.erase(it);
}

void GatewayServerShadowBridge::deliver(SessionWrite write) {
    if (!emit_responses_) {
        return;
    }

    auto it = sessions_by_id_.find(write.envelope.session_id);
    if (it == sessions_by_id_.end()) {
        return;
    }

    auto session = it->second.lock();
    if (!session) {
        return;
    }

    session->send(write.envelope.protocol_message_id,
                  write.envelope.request_id,
                  write.envelope.error_code,
                  std::move(write.envelope.body),
                  write.envelope.flags);
}

SessionId GatewayServerShadowBridge::get_or_create_session_id(const std::shared_ptr<net::Session>& session) {
    auto it = session_ids_by_ptr_.find(session.get());
    if (it != session_ids_by_ptr_.end()) {
        return it->second;
    }

    const auto session_id = next_session_id_++;
    session_ids_by_ptr_.emplace(session.get(), session_id);
    sessions_by_id_.emplace(session_id, session);
    return session_id;
}

}  // namespace v2::gateway
