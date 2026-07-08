#pragma once

#include "net/session.h"

#include <memory>

namespace v2::gateway {

/// Abstract interface for packet handling in the gateway.
/// v1 legacy code (game::gateway::GatewayPacketBridge) also inherits from this
/// to bridge the old gateway into the v2 pipeline.
class PacketBridge {
public:
    virtual ~PacketBridge() = default;

    virtual void on_packet(const std::shared_ptr<net::Session>& session,
                           const net::Session::PacketMessage& message) = 0;
    virtual void on_close(const std::shared_ptr<net::Session>& session) = 0;
};

}  // namespace v2::gateway
