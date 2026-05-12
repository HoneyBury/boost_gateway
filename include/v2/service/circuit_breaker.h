#pragma once

#include <chrono>
#include <cstdint>

namespace v2::service {

struct CircuitBreakerOptions {
    std::uint32_t failure_threshold = 3;
    std::chrono::milliseconds timeout{30'000};
    std::uint32_t half_open_max_requests = 1;
};

enum class CircuitBreakerState {
    kClosed,
    kOpen,
    kHalfOpen,
};

class CircuitBreaker {
public:
    CircuitBreaker();
    explicit CircuitBreaker(CircuitBreakerOptions options);

    CircuitBreaker(const CircuitBreaker&) = delete;
    CircuitBreaker& operator=(const CircuitBreaker&) = delete;
    CircuitBreaker(CircuitBreaker&&) = delete;
    CircuitBreaker& operator=(CircuitBreaker&&) = delete;

    void on_success();
    void on_failure();

    [[nodiscard]] bool allow_request();
    [[nodiscard]] CircuitBreakerState state() const noexcept { return state_; }
    [[nodiscard]] std::uint32_t failure_count() const noexcept { return failure_count_; }
    [[nodiscard]] std::uint32_t half_open_requests() const noexcept { return half_open_requests_; }

    void reset();

private:
    void transition_to(CircuitBreakerState new_state);
    [[nodiscard]] bool is_timeout_expired() const;

    CircuitBreakerOptions options_;
    CircuitBreakerState state_{CircuitBreakerState::kClosed};
    std::uint32_t failure_count_{0};
    std::uint32_t half_open_requests_{0};
    std::chrono::steady_clock::time_point opened_at_;
};

}  // namespace v2::service
