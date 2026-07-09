#pragma once

#include "net/session.h"
#include "v2/gateway/session_handle.h"

#include <memory>

namespace v2::gateway {

/// Abstract interface for packet handling in the gateway using SessionHandle
/// based API.
class PacketBridge {
public:
    virtual ~PacketBridge() = default;

    virtual void on_packet(SessionHandle session,
                           const net::Session::PacketMessage& message) = 0;
    virtual void on_close(SessionHandle session) = 0;
};

}  // namespace v2::gateway
