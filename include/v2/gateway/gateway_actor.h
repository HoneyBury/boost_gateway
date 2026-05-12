#pragma once

#include <functional>
#include <optional>
#include <string>

#include "v2/actor/actor.h"
#include "v2/gateway/rate_limiter.h"

namespace v2::gateway {

class SessionWriteSink {
public:
    virtual ~SessionWriteSink() = default;

    virtual void push(SessionWrite write) = 0;
};

class GatewayCommandSink {
public:
    virtual ~GatewayCommandSink() = default;

    virtual bool handle(const GatewayCommand& command) = 0;
};

class GatewayActor final : public v2::actor::Actor {
public:
    using RateLimitPolicy = std::function<RateLimitResult(const ClientEnvelope&, SessionId)>;
    using AuthorizePolicy = std::function<bool(const GatewayCommand&)>;

    GatewayActor(SessionWriteSink& sink,
                 GatewayCommandSink* command_sink = nullptr,
                 RateLimitPolicy rate_limit_policy = {},
                 AuthorizePolicy authorize_policy = {});

    void on_message(v2::actor::Message&& message) override;

private:
    [[nodiscard]] bool is_public_message(std::uint16_t protocol_message_id) const;
    [[nodiscard]] std::optional<GatewayCommand> to_command(const ClientEnvelope& envelope) const;

    void emit_error(const ClientEnvelope& envelope,
                    std::int32_t error_code,
                    std::string body);
    void emit_response(const ClientEnvelope& request,
                       std::uint16_t response_message_id,
                       std::int32_t error_code,
                       std::string body);

    SessionWriteSink& sink_;
    GatewayCommandSink* command_sink_ = nullptr;
    RateLimitPolicy rate_limit_policy_;
    AuthorizePolicy authorize_policy_;
};

class DownstreamSessionWriteSink {
public:
    virtual ~DownstreamSessionWriteSink() = default;

    virtual void deliver(SessionWrite write) = 0;
};

}  // namespace v2::gateway
