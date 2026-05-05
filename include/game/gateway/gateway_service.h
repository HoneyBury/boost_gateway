#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "net/message_dispatcher.h"

#include <chrono>
#include <mutex>
#include <unordered_set>
#include <unordered_map>

namespace game::gateway {

class GatewayService {
public:
    GatewayService(SessionManager& session_manager, GatewayMetrics& metrics);

    void register_handlers(net::MessageDispatcher& dispatcher) const;

private:
    [[nodiscard]] bool should_allow_message(const net::DispatchContext& context) const;
    [[nodiscard]] bool check_rate_limit(const net::DispatchContext& context) const;

    struct RateLimitEntry {
        std::chrono::steady_clock::time_point window_started_at{};
        std::size_t message_count = 0;
    };

    static constexpr auto kRateLimitWindow = std::chrono::milliseconds(1000);
    static constexpr std::size_t kMaxMessagesPerWindow = 32;

    SessionManager& session_manager_;
    GatewayMetrics& metrics_;
    mutable std::mutex rate_limit_mutex_;
    mutable std::unordered_map<const net::Session*, RateLimitEntry> rate_limits_;
};

}  // namespace game::gateway
