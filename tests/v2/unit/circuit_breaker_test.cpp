#include <gtest/gtest.h>

#include <thread>

#include "v2/service/circuit_breaker.h"

using v2::service::CircuitBreaker;
using v2::service::CircuitBreakerOptions;
using v2::service::CircuitBreakerState;

// ─── Initial state is CLOSED ────────────────────────────────────────

TEST(V2CircuitBreakerTest, InitialStateIsClosed) {
    CircuitBreaker cb;
    EXPECT_EQ(cb.state(), CircuitBreakerState::kClosed);
    EXPECT_EQ(cb.failure_count(), 0);
    EXPECT_TRUE(cb.allow_request());
}

// ─── CLOSED → OPEN after consecutive failures ───────────────────────

TEST(V2CircuitBreakerTest, OpensAfterConsecutiveFailures) {
    CircuitBreakerOptions opts{.failure_threshold = 3};
    CircuitBreaker cb(opts);

    EXPECT_TRUE(cb.allow_request());
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kClosed);

    EXPECT_TRUE(cb.allow_request());
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kClosed);

    EXPECT_TRUE(cb.allow_request());
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kOpen);
}

// ─── OPEN rejects requests ──────────────────────────────────────────

TEST(V2CircuitBreakerTest, OpenRejectsRequests) {
    CircuitBreakerOptions opts{.failure_threshold = 1,
                                .timeout = std::chrono::milliseconds(1000)};
    CircuitBreaker cb(opts);

    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kOpen);
    EXPECT_FALSE(cb.allow_request());
    EXPECT_FALSE(cb.allow_request());
}

// ─── OPEN transitions to HALF_OPEN after timeout ────────────────────

TEST(V2CircuitBreakerTest, TransitionsToHalfOpenAfterTimeout) {
    CircuitBreakerOptions opts{.failure_threshold = 1,
                                .timeout = std::chrono::milliseconds(50)};
    CircuitBreaker cb(opts);

    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kOpen);

    // Wait for timeout
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    EXPECT_TRUE(cb.allow_request());
    EXPECT_EQ(cb.state(), CircuitBreakerState::kHalfOpen);
}

// ─── HALF_OPEN success returns to CLOSED ────────────────────────────

TEST(V2CircuitBreakerTest, HalfOpenSuccessReturnsToClosed) {
    CircuitBreakerOptions opts{.failure_threshold = 2,
                                .timeout = std::chrono::milliseconds(50),
                                .half_open_max_requests = 2};
    CircuitBreaker cb(opts);

    // Trip breaker
    cb.on_failure();
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kOpen);

    // Wait and probe
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    EXPECT_TRUE(cb.allow_request());
    EXPECT_EQ(cb.state(), CircuitBreakerState::kHalfOpen);

    cb.on_success();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kHalfOpen);
    cb.on_success();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kClosed);
    EXPECT_EQ(cb.failure_count(), 0);
}

// ─── HALF_OPEN failure goes back to OPEN ────────────────────────────

TEST(V2CircuitBreakerTest, HalfOpenFailureGoesBackToOpen) {
    CircuitBreakerOptions opts{.failure_threshold = 2,
                                .timeout = std::chrono::milliseconds(50)};
    CircuitBreaker cb(opts);

    // Trip to OPEN
    cb.on_failure();
    cb.on_failure();

    // Wait and go to HALF_OPEN
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    EXPECT_TRUE(cb.allow_request());
    EXPECT_EQ(cb.state(), CircuitBreakerState::kHalfOpen);

    // Fail in HALF_OPEN → back to OPEN
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kOpen);
    EXPECT_FALSE(cb.allow_request());
}

// ─── Success in CLOSED resets failure count ─────────────────────────

TEST(V2CircuitBreakerTest, SuccessResetsFailureCount) {
    CircuitBreakerOptions opts{.failure_threshold = 3};
    CircuitBreaker cb(opts);

    cb.on_failure();
    cb.on_failure();
    EXPECT_EQ(cb.failure_count(), 2);

    cb.on_success();
    EXPECT_EQ(cb.failure_count(), 0);

    // Two more failures shouldn't trip (counter was reset)
    cb.on_failure();
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kClosed);
}

// ─── reset() returns to CLOSED ──────────────────────────────────────

TEST(V2CircuitBreakerTest, ResetReturnsToClosed) {
    CircuitBreaker cb;
    cb.on_failure();
    cb.on_failure();
    cb.on_failure();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kOpen);

    cb.reset();
    EXPECT_EQ(cb.state(), CircuitBreakerState::kClosed);
    EXPECT_EQ(cb.failure_count(), 0);
    EXPECT_TRUE(cb.allow_request());
}
