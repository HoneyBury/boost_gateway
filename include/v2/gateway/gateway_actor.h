#pragma once

#include <functional>
#include <optional>
#include <string>

#include "v2/actor/actor.h"

namespace v2::gateway {

class SessionWriteSink {
public:
    virtual ~SessionWriteSink() = default;

    virtual void push(SessionWrite write) = 0;
};

class GatewayActor final : public v2::actor::Actor {
public:
    using RateLimitPolicy = std::function<bool(const ClientEnvelope&)>;

    GatewayActor(SessionWriteSink& sink,
                 RateLimitPolicy rate_limit_policy = {});

    void on_message(v2::actor::Message&& message) override;

private:
    [[nodiscard]] bool is_whitelisted(std::uint16_t protocol_message_id) const;
    [[nodiscard]] std::optional<GatewayCommand> to_command(const ClientEnvelope& envelope) const;

    void emit_error(const ClientEnvelope& envelope,
                    std::int32_t error_code,
                    std::string body);
    void emit_response(const ClientEnvelope& request,
                       std::uint16_t response_message_id,
                       std::int32_t error_code,
                       std::string body);

    SessionWriteSink& sink_;
    RateLimitPolicy rate_limit_policy_;
};

}  // namespace v2::gateway
