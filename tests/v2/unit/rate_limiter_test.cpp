#include <gtest/gtest.h>

#include <thread>

#include "v2/gateway/rate_limiter.h"

namespace v2::gateway {
namespace {

// ============================================================================
// TokenBucket unit tests
// ============================================================================

TEST(TokenBucketTest, InitialStateHasFullCapacity) {
    const TokenBucket bucket(100, 10);
    EXPECT_DOUBLE_EQ(bucket.current, 100.0);
    EXPECT_DOUBLE_EQ(bucket.capacity, 100.0);
    EXPECT_DOUBLE_EQ(bucket.refill_rate, 10.0);
}

TEST(TokenBucketTest, TryConsumeDrainsTokens) {
    TokenBucket bucket(10, 10);

    EXPECT_TRUE(bucket.try_consume(3));
    EXPECT_DOUBLE_EQ(bucket.current, 7.0);

    EXPECT_TRUE(bucket.try_consume(7));
    EXPECT_NEAR(bucket.current, 0.0, 0.01);

    // Should fail — no tokens left
    EXPECT_FALSE(bucket.try_consume(1));
}

TEST(TokenBucketTest, RefillsOverTime) {
    TokenBucket bucket(100, 100);

    // Drain completely
    EXPECT_TRUE(bucket.try_consume(100));
    EXPECT_DOUBLE_EQ(bucket.current, 0.0);

    // Wait ~500 ms → should accumulate ~50 tokens
    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    bucket.refill();

    // Allow generous tolerance (15) to avoid flakiness on busy CI runners
    EXPECT_NEAR(bucket.current, 50.0, 15.0);
}

TEST(TokenBucketTest, NeverExceedsCapacity) {
    TokenBucket bucket(50, 50);

    // Wait well past the time needed to fully refill
    std::this_thread::sleep_for(std::chrono::seconds(2));
    bucket.refill();

    // Must not exceed capacity
    EXPECT_DOUBLE_EQ(bucket.current, 50.0);
}

// ============================================================================
// RateLimiter integration tests
// ============================================================================

TEST(RateLimiterTest, AllowsFirstRequest) {
    RateLimiter::Config config;
    config.connection_limit = 10;
    config.ip_limit = 100;
    config.user_limit = 100;
    config.login_limit = 100;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.session_id = 1;
    env.protocol_message_id = 1001;  // non-login

    const auto result = limiter.check(env, 1, "1.2.3.4");
    EXPECT_TRUE(result.allowed);
    EXPECT_TRUE(result.reason.empty());
    EXPECT_EQ(result.retry_after_ms, 0);
}

TEST(RateLimiterTest, BlocksAfterExhaustingConnectionLimit) {
    RateLimiter::Config config;
    config.connection_limit = 3;
    config.ip_limit = 1000;
    config.user_limit = 1000;
    config.login_limit = 1000;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.session_id = 42;
    env.protocol_message_id = 1001;

    // First 3 requests should be allowed
    EXPECT_TRUE(limiter.check(env, 42, "ip1").allowed);
    EXPECT_TRUE(limiter.check(env, 42, "ip1").allowed);
    EXPECT_TRUE(limiter.check(env, 42, "ip1").allowed);

    // 4th request should be blocked
    const auto result = limiter.check(env, 42, "ip1");
    EXPECT_FALSE(result.allowed);
    EXPECT_EQ(result.reason, "connection_rate_limited");
    EXPECT_GT(result.retry_after_ms, 0);
}

TEST(RateLimiterTest, BlocksAfterExhaustingUserLimit) {
    RateLimiter::Config config;
    config.connection_limit = 1000;
    config.ip_limit = 1000;
    config.user_limit = 3;
    config.login_limit = 1000;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.session_id = 1;
    env.protocol_message_id = 1001;

    // First 3 requests for "alice" should be allowed
    EXPECT_TRUE(limiter.check(env, 1, "ip_a", "alice").allowed);
    EXPECT_TRUE(limiter.check(env, 1, "ip_a", "alice").allowed);
    EXPECT_TRUE(limiter.check(env, 1, "ip_a", "alice").allowed);

    // 4th request for "alice" should be blocked
    const auto result = limiter.check(env, 1, "ip_a", "alice");
    EXPECT_FALSE(result.allowed);
    EXPECT_EQ(result.reason, "user_rate_limited");
    EXPECT_GT(result.retry_after_ms, 0);
}

TEST(RateLimiterTest, BlocksAfterExhaustingIpLimit) {
    RateLimiter::Config config;
    config.connection_limit = 1000;
    config.message_type_limit = 1000;
    config.ip_limit = 3;
    config.user_limit = 1000;
    config.login_limit = 1000;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.protocol_message_id = 1001;

    env.session_id = 1;
    EXPECT_TRUE(limiter.check(env, 1, "10.0.0.7").allowed);
    env.session_id = 2;
    EXPECT_TRUE(limiter.check(env, 2, "10.0.0.7").allowed);
    env.session_id = 3;
    EXPECT_TRUE(limiter.check(env, 3, "10.0.0.7").allowed);
    env.session_id = 4;

    const auto result = limiter.check(env, 4, "10.0.0.7");
    EXPECT_FALSE(result.allowed);
    EXPECT_EQ(result.reason, "ip_rate_limited");
    EXPECT_GT(result.retry_after_ms, 0);
}

TEST(RateLimiterTest, BlocksAfterExhaustingMessageTypeLimit) {
    RateLimiter::Config config;
    config.connection_limit = 1000;
    config.message_type_limit = 3;
    config.ip_limit = 1000;
    config.user_limit = 1000;
    config.login_limit = 1000;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.protocol_message_id = 3001;

    env.session_id = 1;
    EXPECT_TRUE(limiter.check(env, 1, "ip1").allowed);
    env.session_id = 2;
    EXPECT_TRUE(limiter.check(env, 2, "ip2").allowed);
    env.session_id = 3;
    EXPECT_TRUE(limiter.check(env, 3, "ip3").allowed);
    env.session_id = 4;

    const auto result = limiter.check(env, 4, "ip4");
    EXPECT_FALSE(result.allowed);
    EXPECT_EQ(result.reason, "message_type_rate_limited");
    EXPECT_GT(result.retry_after_ms, 0);
}

TEST(RateLimiterTest, AllowsDifferentUsersIndependently) {
    RateLimiter::Config config;
    config.connection_limit = 1000;
    config.ip_limit = 1000;
    config.user_limit = 3;
    config.login_limit = 1000;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.protocol_message_id = 1001;

    // Exhaust alice's limit
    env.session_id = 10;
    EXPECT_TRUE(limiter.check(env, 10, "ip_a", "alice").allowed);
    EXPECT_TRUE(limiter.check(env, 10, "ip_a", "alice").allowed);
    EXPECT_TRUE(limiter.check(env, 10, "ip_a", "alice").allowed);
    EXPECT_FALSE(limiter.check(env, 10, "ip_a", "alice").allowed);

    // Bob (different session, different IP) should still be allowed
    env.session_id = 20;
    EXPECT_TRUE(limiter.check(env, 20, "ip_b", "bob").allowed);
    EXPECT_TRUE(limiter.check(env, 20, "ip_b", "bob").allowed);
    EXPECT_TRUE(limiter.check(env, 20, "ip_b", "bob").allowed);
    EXPECT_FALSE(limiter.check(env, 20, "ip_b", "bob").allowed);
}

TEST(RateLimiterTest, RateLimitsLoginAggressively) {
    RateLimiter::Config config;
    config.connection_limit = 1000;
    config.ip_limit = 1000;
    config.user_limit = 1000;
    config.login_limit = 3;
    RateLimiter limiter(config);

    ClientEnvelope env;
    env.session_id = 1;
    env.protocol_message_id = 2001;  // kLoginRequest

    // First 3 logins should be allowed
    EXPECT_TRUE(limiter.check(env, 1, "ip1").allowed);
    EXPECT_TRUE(limiter.check(env, 1, "ip1").allowed);
    EXPECT_TRUE(limiter.check(env, 1, "ip1").allowed);

    // 4th login should be blocked by the aggressive login limit
    const auto login_result = limiter.check(env, 1, "ip1");
    EXPECT_FALSE(login_result.allowed);
    EXPECT_EQ(login_result.reason, "login_rate_limited");
    EXPECT_GT(login_result.retry_after_ms, 0);

    // Non-login messages from the same session should still be allowed
    env.protocol_message_id = 3001;
    EXPECT_TRUE(limiter.check(env, 1, "ip1").allowed);
}

}  // namespace
}  // namespace v2::gateway
