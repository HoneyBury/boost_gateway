#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <mutex>
#include <string>
#include <unordered_map>

namespace net {

struct RateLimitConfig {
    std::size_t max_per_second = 32;          // normal rate limit
    std::size_t warmup_max_per_second = 8;    // reduced limit during warmup
    std::chrono::seconds warmup_duration{10}; // ramp-up period
    std::size_t login_max_attempts = 5;       // max failed logins before block
    std::chrono::seconds login_block_window{60}; // block duration after excess failures
    std::size_t guest_max_per_second = 16;    // guest rate limit
};

class RateLimiter {
public:
    explicit RateLimiter(RateLimitConfig config = {}) : config_(config) {}

    // Check if a connection is allowed to send a message.
    // Returns true if within limit, false if rate-limited.
    bool check_connection(std::uint64_t session_id, std::chrono::steady_clock::duration age,
                          bool is_guest = false) {
        const auto now = std::chrono::steady_clock::now();
        std::scoped_lock lock(mutex_);

        auto& state = connection_states_[session_id];
        const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - state.window_start);

        // Reset window if expired
        if (elapsed >= std::chrono::seconds(1)) {
            state.window_start = now;
            state.count = 0;
        }

        // Compute effective limit based on warmup and guest status
        std::size_t limit = config_.max_per_second;
        if (is_guest) {
            limit = config_.guest_max_per_second;
        } else if (age < config_.warmup_duration) {
            // Linear ramp from warmup_max to max over warmup_duration
            const auto age_sec = std::chrono::duration_cast<std::chrono::seconds>(age).count();
            const auto warmup_sec = config_.warmup_duration.count();
            limit = config_.warmup_max_per_second +
                    (config_.max_per_second - config_.warmup_max_per_second) * age_sec / warmup_sec;
        }

        if (state.count >= limit) return false;
        ++state.count;
        return true;
    }

    // Check per-user rate (shared across all connections of same user)
    bool check_user(const std::string& user_id) {
        const auto now = std::chrono::steady_clock::now();
        std::scoped_lock lock(mutex_);

        auto& state = user_states_[user_id];
        const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - state.window_start);

        if (elapsed >= std::chrono::seconds(1)) {
            state.window_start = now;
            state.count = 0;
        }

        // Per-user limit is 2x the per-connection limit (multiple tabs etc)
        const std::size_t limit = config_.max_per_second * 2;
        if (state.count >= limit) return false;
        ++state.count;
        return true;
    }

    // Track login attempt. Returns true if allowed, false if blocked.
    bool check_login_attempt(const std::string& ip, const std::string& user_id) {
        const auto now = std::chrono::steady_clock::now();
        std::scoped_lock lock(mutex_);

        // Check IP block
        auto& ip_state = login_ip_states_[ip];
        if (ip_state.blocked_until > now) return false;
        if (std::chrono::duration_cast<std::chrono::seconds>(now - ip_state.window_start) >=
            config_.login_block_window) {
            ip_state.window_start = now;
            ip_state.failures = 0;
        }

        // Check per-user block
        auto& user_state = login_user_states_[user_id];
        if (user_state.blocked_until > now) return false;
        if (std::chrono::duration_cast<std::chrono::seconds>(now - user_state.window_start) >=
            config_.login_block_window) {
            user_state.window_start = now;
            user_state.failures = 0;
        }

        return true;
    }

    // Record failed login attempt
    void record_login_failure(const std::string& ip, const std::string& user_id) {
        const auto now = std::chrono::steady_clock::now();
        std::scoped_lock lock(mutex_);

        auto& ip_state = login_ip_states_[ip];
        ip_state.failures++;
        if (ip_state.failures >= config_.login_max_attempts) {
            ip_state.blocked_until = now + config_.login_block_window;
        }

        auto& user_state = login_user_states_[user_id];
        user_state.failures++;
        if (user_state.failures >= config_.login_max_attempts) {
            user_state.blocked_until = now + config_.login_block_window;
        }
    }

    void cleanup_session(std::uint64_t session_id) {
        std::scoped_lock lock(mutex_);
        connection_states_.erase(session_id);
    }

private:
    struct WindowState {
        std::chrono::steady_clock::time_point window_start{};
        std::size_t count = 0;
    };

    struct LoginState {
        std::chrono::steady_clock::time_point window_start{};
        std::chrono::steady_clock::time_point blocked_until{};
        std::size_t failures = 0;
    };

    // Check per-message-type rate limit. Returns true if allowed.
    bool check_message_type(std::uint64_t session_id, std::uint16_t message_id,
                            std::chrono::steady_clock::duration age) {
        // System messages (kicks, errors) bypass rate limit
        if (is_system_message(message_id)) return true;

        std::size_t limit = config_.max_per_second;
        if (is_login_message(message_id)) limit = std::min<std::size_t>(5, limit);
        else if (is_battle_message(message_id)) limit = std::max<std::size_t>(limit, 100);

        return check_connection(session_id, age) && check_message_window(session_id, limit);
    }

private:
    static bool is_system_message(std::uint16_t msg_id) {
        return msg_id >= 1003 && msg_id <= 1004;  // kSessionKickedPush, kSessionResumedPush
    }

    static bool is_login_message(std::uint16_t msg_id) {
        return msg_id >= 2001 && msg_id <= 2002;
    }

    static bool is_battle_message(std::uint16_t msg_id) {
        return msg_id >= 4001 && msg_id <= 4006;
    }

    bool check_message_window(std::uint64_t session_id, std::size_t limit) {
        const auto now = std::chrono::steady_clock::now();
        auto& state = msg_type_states_[session_id];
        const auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(now - state.window_start);
        if (elapsed >= std::chrono::seconds(1)) {
            state.window_start = now;
            state.count = 0;
        }
        if (state.count >= limit) return false;
        ++state.count;
        return true;
    }

    RateLimitConfig config_;
    std::mutex mutex_;
    std::unordered_map<std::uint64_t, WindowState> connection_states_;
    std::unordered_map<std::uint64_t, WindowState> msg_type_states_;
    std::unordered_map<std::string, WindowState> user_states_;
    std::unordered_map<std::string, LoginState> login_ip_states_;
    std::unordered_map<std::string, LoginState> login_user_states_;
};

}  // namespace net
