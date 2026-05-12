#include <gtest/gtest.h>

#include <chrono>
#include "v2/auth/jwt_validator.h"
#include "v2/auth/authorizer.h"

TEST(JwtValidatorTest, GenerateAndValidateToken) {
    v2::auth::JwtValidator::Config config{.secret = "test-secret-key", .issuer = "boost-gateway"};
    v2::auth::JwtValidator validator(config);

    v2::auth::JwtPayload payload;
    payload.sub = "alice";
    payload.role = "player";
    payload.display_name = "Alice";

    auto token = validator.generate(payload);
    ASSERT_FALSE(token.empty());

    auto result = validator.validate(token);
    EXPECT_TRUE(result.valid) << result.error;
    EXPECT_EQ(result.payload.sub, "alice");
    EXPECT_EQ(result.payload.role, "player");
}

TEST(JwtValidatorTest, InvalidSignatureRejected) {
    v2::auth::JwtValidator::Config config{.secret = "correct-secret"};
    v2::auth::JwtValidator validator(config);

    // Generate with wrong secret
    v2::auth::JwtValidator::Config wrong_config{.secret = "wrong-secret"};
    v2::auth::JwtValidator wrong_validator(wrong_config);
    v2::auth::JwtPayload payload{.sub = "alice", .role = "player"};
    auto token = wrong_validator.generate(payload);

    auto result = validator.validate(token);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "invalid_signature");
}

TEST(JwtValidatorTest, ExpiredTokenRejected) {
    v2::auth::JwtValidator::Config config{.secret = "secret", .require_expiration = true};
    v2::auth::JwtValidator validator(config);

    auto now = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();

    v2::auth::JwtPayload payload;
    payload.sub = "alice";
    payload.role = "player";
    payload.iat = static_cast<std::uint64_t>(now) - 7200;  // 2 hours ago
    payload.exp = static_cast<std::uint64_t>(now) - 3600;  // expired 1 hour ago

    auto token = validator.generate(payload);
    auto result = validator.validate(token);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "token_expired");
}

TEST(JwtValidatorTest, MalformedTokenRejected) {
    v2::auth::JwtValidator::Config config{.secret = "secret"};
    v2::auth::JwtValidator validator(config);

    auto result = validator.validate("not-a-jwt");
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "malformed_token");

    result = validator.validate("header.payload");  // missing signature
    EXPECT_FALSE(result.valid);
}

TEST(JwtValidatorTest, WrongIssuerRejected) {
    v2::auth::JwtValidator::Config config{.secret = "secret", .issuer = "boost-gateway"};
    v2::auth::JwtValidator validator(config);

    v2::auth::JwtValidator::Config other_config{.secret = "secret", .issuer = "other-issuer"};
    v2::auth::JwtValidator other_validator(other_config);
    v2::auth::JwtPayload payload{.sub = "alice", .role = "player"};
    auto token = other_validator.generate(payload);

    auto result = validator.validate(token);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "invalid_issuer");
}

// ── Authorizer ──────────────────────────────────────────────────────────

TEST(AuthorizerTest, PlayerCanSendGameMessages) {
    auto& auth = v2::auth::Authorizer::instance();
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kPlayer, 2001));  // login
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kPlayer, 3001));  // room create
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kPlayer, 4003));  // battle input
}

TEST(AuthorizerTest, PlayerCannotSendAdminCommands) {
    auto& auth = v2::auth::Authorizer::instance();
    EXPECT_FALSE(auth.is_allowed(v2::auth::Role::kPlayer, 5001));  // admin kick
    EXPECT_FALSE(auth.is_allowed(v2::auth::Role::kPlayer, 5004));  // admin reload
}

TEST(AuthorizerTest, AdminCanSendEverything) {
    auto& auth = v2::auth::Authorizer::instance();
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kAdmin, 2001));   // login
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kAdmin, 3001));   // room create
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kAdmin, 5001));   // admin kick
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kAdmin, 5004));   // admin reload
}

TEST(AuthorizerTest, ObserverReadOnly) {
    auto& auth = v2::auth::Authorizer::instance();
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kObserver, 1));      // heartbeat
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kObserver, 2001));   // login
    EXPECT_FALSE(auth.is_allowed(v2::auth::Role::kObserver, 3001));  // room create
    EXPECT_FALSE(auth.is_allowed(v2::auth::Role::kObserver, 4003));  // battle input
}

TEST(AuthorizerTest, RoleFromString) {
    EXPECT_EQ(v2::auth::role_from_string("admin"), v2::auth::Role::kAdmin);
    EXPECT_EQ(v2::auth::role_from_string("observer"), v2::auth::Role::kObserver);
    EXPECT_EQ(v2::auth::role_from_string("player"), v2::auth::Role::kPlayer);
    EXPECT_EQ(v2::auth::role_from_string("unknown"), v2::auth::Role::kPlayer);  // default
}

TEST(AuthorizerTest, GrantAndDenyModifyRules) {
    v2::auth::Authorizer auth;
    auth.allow(v2::auth::Role::kPlayer, 5001);
    EXPECT_TRUE(auth.is_allowed(v2::auth::Role::kPlayer, 5001));
    auth.deny(v2::auth::Role::kPlayer, 5001);
    EXPECT_FALSE(auth.is_allowed(v2::auth::Role::kPlayer, 5001));
}
