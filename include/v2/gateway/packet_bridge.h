#pragma once

#include "net/session.h"

#include <memory>

namespace v2::gateway {

/// Abstract interface for packet handling in the gateway.
/// This is the v2 canonical interface; legacy v1 code
/// (game::gateway::GatewayPacketBridge) inherits from this.
class PacketBridge {
public:
    virtual ~PacketBridge() = default;

    virtual void on_packet(const std::shared_ptr<net::Session>& session,
                           const net::Session::PacketMessage& message) = 0;
    virtual void on_close(const std::shared_ptr<net::Session>& session) = 0;
};

}  // namespace v2::gateway
