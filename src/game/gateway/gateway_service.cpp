#include "game/gateway/gateway_service.h"

#include "net/protocol.h"

#include <array>
#include <chrono>

namespace game::gateway {

GatewayService::GatewayService(SessionManager& session_manager, GatewayMetrics& metrics)
    : session_manager_(session_manager), metrics_(metrics) {}

void GatewayService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_middleware(
        "auth_whitelist",
        [this](const net::DispatchContext& context) { return should_allow_message(context); });
    dispatcher.register_middleware(
        "rate_limit",
        [this](const net::DispatchContext& context) { return check_rate_limit(context); });

    dispatcher.register_handler(
        net::protocol::kHeartbeatRequest,
        [](const net::DispatchContext& context) {
            // 心跳包不进入复杂业务流程，直接由网关层回包。
            context.session->send(net::protocol::kHeartbeatResponse,
                                  context.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  "pong");
        });
}

bool GatewayService::should_allow_message(const net::DispatchContext& context) const {
    static constexpr std::array<std::uint16_t, 3> kWhitelist = {
        net::protocol::kHeartbeatRequest,
        net::protocol::kLoginRequest,
        net::protocol::kEchoRequest,
    };

    for (const auto message_id : kWhitelist) {
        if (context.message_id == message_id) {
            return true;
        }
    }

    if (session_manager_.is_authenticated(context.session)) {
        return true;
    }

    metrics_.on_packet_blocked();
    context.session->send(net::protocol::kErrorResponse,
                          context.request_id,
                          static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired),
                          net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
    return false;
}

bool GatewayService::check_rate_limit(const net::DispatchContext& context) const {
    if (context.message_id == net::protocol::kHeartbeatRequest) {
        return true;
    }

    const auto session_key = context.session.get();
    const auto now = std::chrono::steady_clock::now();
    bool allowed = false;

    {
        std::scoped_lock lock(rate_limit_mutex_);
        auto& entry = rate_limits_[session_key];
        if (now - entry.window_started_at >= kRateLimitWindow) {
            entry.window_started_at = now;
            entry.message_count = 0;
        }

        if (entry.message_count < kMaxMessagesPerWindow) {
            ++entry.message_count;
            allowed = true;
        }
    }

    if (allowed) {
        return true;
    }

    metrics_.on_packet_blocked();
    context.session->send(net::protocol::kErrorResponse,
                          context.request_id,
                          static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited),
                          net::protocol::to_string(net::protocol::ErrorCode::kRateLimited));
    return false;
}

}  // namespace game::gateway
