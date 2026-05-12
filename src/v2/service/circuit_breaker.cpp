#include "v2/service/circuit_breaker.h"

namespace v2::service {

CircuitBreaker::CircuitBreaker() = default;

CircuitBreaker::CircuitBreaker(CircuitBreakerOptions options)
    : options_(options) {}

void CircuitBreaker::on_success() {
    switch (state_) {
        case CircuitBreakerState::kClosed:
            failure_count_ = 0;
            break;
        case CircuitBreakerState::kHalfOpen:
            ++half_open_requests_;
            if (half_open_requests_ >= options_.half_open_max_requests) {
                transition_to(CircuitBreakerState::kClosed);
            }
            break;
        case CircuitBreakerState::kOpen:
            break;
    }
}

void CircuitBreaker::on_failure() {
    switch (state_) {
        case CircuitBreakerState::kClosed:
            ++failure_count_;
            if (failure_count_ >= options_.failure_threshold) {
                transition_to(CircuitBreakerState::kOpen);
            }
            break;
        case CircuitBreakerState::kHalfOpen:
            transition_to(CircuitBreakerState::kOpen);
            break;
        case CircuitBreakerState::kOpen:
            break;
    }
}

bool CircuitBreaker::allow_request() {
    if (state_ == CircuitBreakerState::kOpen) {
        if (is_timeout_expired()) {
            transition_to(CircuitBreakerState::kHalfOpen);
            return true;
        }
        return false;
    }
    return true;
}

void CircuitBreaker::reset() {
    state_ = CircuitBreakerState::kClosed;
    failure_count_ = 0;
    half_open_requests_ = 0;
}

void CircuitBreaker::transition_to(CircuitBreakerState new_state) {
    state_ = new_state;
    if (new_state == CircuitBreakerState::kOpen) {
        opened_at_ = std::chrono::steady_clock::now();
    } else if (new_state == CircuitBreakerState::kClosed) {
        failure_count_ = 0;
        half_open_requests_ = 0;
    } else if (new_state == CircuitBreakerState::kHalfOpen) {
        half_open_requests_ = 0;
    }
}

bool CircuitBreaker::is_timeout_expired() const {
    const auto now = std::chrono::steady_clock::now();
    return (now - opened_at_) >= options_.timeout;
}

}  // namespace v2::service
