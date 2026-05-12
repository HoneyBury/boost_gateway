#pragma once

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>

#include "v2/gateway/message_types.h"

namespace v2::gateway {

// ---------------------------------------------------------------------------
// RateLimitResult — returned by every rate-limit check
// ---------------------------------------------------------------------------
struct RateLimitResult {
    bool allowed = true;
    std::string reason;
    int retry_after_ms = 0;
};

// ---------------------------------------------------------------------------
// TokenBucket — token-bucket algorithm with time-based refill
// ---------------------------------------------------------------------------
struct TokenBucket {
    double capacity = 100.0;
    double refill_rate = 10.0;  // tokens per second
    double current = 100.0;
    std::chrono::steady_clock::time_point last_refill =
        std::chrono::steady_clock::now();

    TokenBucket() = default;

    TokenBucket(double cap, double rate)
        : capacity(cap),
          refill_rate(rate),
          current(cap),
          last_refill(std::chrono::steady_clock::now()) {}

    // Refill tokens based on elapsed time, never exceeding capacity.
    void refill() {
        const auto now = std::chrono::steady_clock::now();
        const auto elapsed = now - last_refill;
        if (elapsed > std::chrono::seconds(0)) {
            const double seconds =
                std::chrono::duration<double>(elapsed).count();
            current = std::min(capacity, current + seconds * refill_rate);
            last_refill = now;
        }
    }

    // Consume `count` tokens.  Returns true iff enough tokens were available.
    bool try_consume(double count = 1.0) {
        refill();
        if (current >= count) {
            current -= count;
            return true;
        }
        return false;
    }

    // Estimate how many milliseconds until a single token is available.
    int estimate_retry_ms() const {
        if (current >= 1.0) return 0;
        const double deficit = 1.0 - current;
        const int ms = static_cast<int>(
            std::ceil(deficit / refill_rate * 1000.0));
        return std::max(1, ms);
    }
};

// ---------------------------------------------------------------------------
// RateLimiter — multi-level rate limiter
//
// Checks are applied in this order:
//   1. per-connection (session_id)
//   2. global per-message-type
//   3. per-IP  (when ip_hint is non-empty)
//   4. per-user (when user_id is non-empty)
//   5. login-specific (when protocol_message_id == 2001)
// ---------------------------------------------------------------------------
class RateLimiter {
public:
    struct Config {
        double connection_limit = 100.0;  // tokens/s per connection
        double ip_limit = 200.0;          // tokens/s per IP
        double user_limit = 50.0;         // tokens/s per user
        double login_limit = 5.0;         // tokens/s for login globally
    };

    explicit RateLimiter(Config config = {})
        : config_(config),
          login_bucket_(config_.login_limit, config_.login_limit) {}

    // Check whether the request described by `envelope` is allowed.
    // `session_id` identifies the TCP/WS connection.
    // `ip_hint` is the remote address (may be empty).
    // `user_id` is the authenticated user (may be empty for pre-auth msgs).
    RateLimitResult check(const ClientEnvelope& envelope,
                          SessionId session_id,
                          const std::string& ip_hint = {},
                          const std::string& user_id = {}) {
        std::scoped_lock lock(mutex_);

        // 1. Per-connection
        auto& conn_bucket =
            get_or_create(connection_buckets_, session_id,
                          config_.connection_limit);
        if (!conn_bucket.try_consume()) {
            return {false, "connection_rate_limited",
                    conn_bucket.estimate_retry_ms()};
        }

        // 2. Global per-message-type
        auto& msg_bucket =
            get_or_create(msg_type_buckets_, envelope.protocol_message_id,
                          kDefaultMsgTypeLimit);
        if (!msg_bucket.try_consume()) {
            return {false, "message_type_rate_limited",
                    msg_bucket.estimate_retry_ms()};
        }

        // 3. Per-IP
        if (!ip_hint.empty()) {
            auto& ip_bucket =
                get_or_create(ip_buckets_, ip_hint, config_.ip_limit);
            if (!ip_bucket.try_consume()) {
                return {false, "ip_rate_limited",
                        ip_bucket.estimate_retry_ms()};
            }
        }

        // 4. Per-user
        if (!user_id.empty()) {
            auto& user_bucket =
                get_or_create(user_buckets_, user_id, config_.user_limit);
            if (!user_bucket.try_consume()) {
                return {false, "user_rate_limited",
                        user_bucket.estimate_retry_ms()};
            }
        }

        // 5. Login-specific aggressive limit
        if (envelope.protocol_message_id == 2001) {  // kLoginRequest
            if (!login_bucket_.try_consume()) {
                return {false, "login_rate_limited",
                        login_bucket_.estimate_retry_ms()};
            }
        }

        return {true, "", 0};
    }

private:
    static constexpr double kDefaultMsgTypeLimit = 500.0;

    // Get-or-create helper: returns reference to the bucket for `key`,
    // initialising it with `limit` tokens if it does not exist yet.
    template <typename Key>
    static TokenBucket& get_or_create(
        std::unordered_map<Key, TokenBucket>& map,
        const Key& key,
        double limit) {
        auto [it, created] = map.try_emplace(key, limit, limit);
        return it->second;
    }

    Config config_;
    TokenBucket login_bucket_;
    std::mutex mutex_;
    std::unordered_map<SessionId, TokenBucket> connection_buckets_;
    std::unordered_map<std::string, TokenBucket> ip_buckets_;
    std::unordered_map<std::string, TokenBucket> user_buckets_;
    std::unordered_map<std::uint16_t, TokenBucket> msg_type_buckets_;
};

}  // namespace v2::gateway
