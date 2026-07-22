#include <gtest/gtest.h>

#include <chrono>
#include <atomic>
#include <memory>
#include <stdexcept>
#include <thread>
#include <unordered_map>
#include <openssl/core_names.h>
#include <openssl/evp.h>
#include <openssl/pem.h>
#include <nlohmann/json.hpp>
#include "v2/auth/jwt_validator.h"
#include "v2/auth/authorizer.h"

namespace {

constexpr const char* kTestRs256PrivateKey = R"(-----BEGIN PRIVATE KEY-----
MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDdKPKcv8FJB88T
/tAjorWED7EQ/q5cXWlXcBWV5csqFalnD1OClbLf1I+vJYVIqgfGPpRZxRiVNLR6
oFwUykUNzPbbm9YMNPgcpQZIJPXHIEMpzbq4IovS8ZEHGyJGyNOo5OH1G1ZVkRtu
r9b4yySfufsOQkteTnsOKweumKPNjH0VvFOHhyJ3KNKfbn5cEJ4JfI5WQnGSas7I
JX9oOP4c/hA5ml32RIR1dklq3sgDTgKFjBoJOL5qfcBIya/Y8tGisvuo6at3tbcS
zNXSR0oo0FSSc1et4U6/5n+MLOTicU27481rblrYz6p1XqY4sSUezsMdxYWxBD35
enwHzFFbAgMBAAECggEADbHvvO6QHAmGDiFtDYyrIkEZTTADZ3tui56u7E522b80
ppahRiImzP7r+0Pe1vFi50JQMnNNKRqPg7mmNuPECe1waruU/vWEWO9ZJ7xUlR7P
OemTXFXht0+d+pHFRufZv4j8FKihz7OoJLila1hwOkBGJr7KzWcdVKYsGBvVc0o+
o7Qu7Ixd21DVgEurADa3dogVC1znpzQ7zV/VTxV8LO2kYLwn2ch2eWxHKZwUqdZm
8FiknokgQ3GwSqGHy4T6FbjI9HzyX9+4WOjI7rAqpJS0kyqMZWjvjrpvlhaBeGFc
hHkYP4aDDFLbQc2iNtZP43HEPz4l5B9mJ9oU2A/kJQKBgQD47o1ZkGBoeUeKU8te
jTbF5Jjv2LwMEdkSFnkuSfRNQ8rPgugiWZ9oSo4RLZcqr/wu0GZnevI0kKfKATHb
ieuU/n4Q5kRBHjGOvVtDKXaBgIWC2kz4vvWecgLJ8uJG0Rz70drPCysxRcfRVyyY
T8/PyY+4Ru2yYiG+1jvmgr/BDwKBgQDjcIagCsN84V5AFJuOB6doEnKkrPo3v9Iv
ptCQZK+B/Con+y/gsP5w76vO7VXAqxrSexUsK5IKKkEDjPLlq/EDaJJ8Zwv0jtav
P6qLbp4SRPs+7r0FtVnuLqo99WiYkwIrlZHGuzojIa5LNuEqPBaSXcCap/Y9oeFp
uzDQVvKS9QKBgAIBagIet6gf0gO7SRgp6xcNEG5eQKWYPzd2FuPYlK9KrIefdl9Q
eYhNkXdx9pXRdSarZyfORcVGpRNrjwtFwTAiHMHmGQatR5juzZ1s6BeDAZBcUeJv
J2tvX7ZgzpHjfWhJ+IlSfbaX6VQ2b5WKjxINfaruZ1vYjo0LDNB+nSzhAoGAW0As
Y02uPQ5WuDMMbiGYAuNT58oW4gMuGzw8dZJP8EDx0PSwst+QVlNyhSUnwJNlwYjs
Z7pbb4SgbQJB+e/QVOPB0fOuEkK0078hd6u78+yFOSyj3gRyvmMunok1m/Fvb3kk
8azwmGPNABRWppFRJQxEWEiHPRcTz03xOcWIsXkCgYEAitzP/18TLvA+RMZ0AFEg
VyAcc44fyCbjipJJayeJMI+mxhvSjMsWNqP/cUzwxNn8dm9BZxKC1VOWnYBe6kSo
/J2podgtAsNK938995tD2ELPwB7XhSPm4fC2AHEewMH3xOD1yxOEouuhyDK7bKBu
lZ538VHoMkT6G7FCjou+F5s=
-----END PRIVATE KEY-----)";

constexpr const char* kTestRs256PublicKey = R"(-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3SjynL/BSQfPE/7QI6K1
hA+xEP6uXF1pV3AVleXLKhWpZw9TgpWy39SPryWFSKoHxj6UWcUYlTS0eqBcFMpF
Dcz225vWDDT4HKUGSCT1xyBDKc26uCKL0vGRBxsiRsjTqOTh9RtWVZEbbq/W+Msk
n7n7DkJLXk57DisHrpijzYx9FbxTh4cidyjSn25+XBCeCXyOVkJxkmrOyCV/aDj+
HP4QOZpd9kSEdXZJat7IA04ChYwaCTi+an3ASMmv2PLRorL7qOmrd7W3EszV0kdK
KNBUknNXreFOv+Z/jCzk4nFNu+PNa25a2M+qdV6mOLElHs7DHcWFsQQ9+Xp8B8xR
WwIDAQAB
-----END PUBLIC KEY-----)";

constexpr const char* kOtherRs256PublicKey = R"(-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvF+L3dugNniWa7lYprWU
TD4hVV9bNqV0Kr1JoGX3d0No2ccznwW40YZ42oFpJypoM6JtuaiqWdQC66PeU4Rd
yU4xP/L3Xt5CLziAdo427MNtob9qnQeiXT2J4j/McDEHoPcz+fAGzXg7EKmzkYvi
jgI2O4iD7S1CSeZxrvNMqjAx3xtdfNR9dEHYQVOSOrSAWVSLA61n26ypMYmzu2/J
qqPwyJkFr1djOXqX2oO3Ln9mxov6CjwOPiO++bgoHWZqzRBmZHd19MdT2dtXBBxM
vm50KLSp2wx9DwuP75WeCwl1RgEzhd3fVaYsubmBp2uLhaVko1qrrQiGQ5vyLLOk
wwIDAQAB
-----END PUBLIC KEY-----)";

std::string jwks_document(const std::string& kid, const std::string& pem) {
    using BioPtr = std::unique_ptr<BIO, decltype(&BIO_free)>;
    using KeyPtr = std::unique_ptr<EVP_PKEY, decltype(&EVP_PKEY_free)>;
    using BnPtr = std::unique_ptr<BIGNUM, decltype(&BN_free)>;
    BioPtr bio(BIO_new_mem_buf(pem.data(), static_cast<int>(pem.size())), BIO_free);
    KeyPtr key(PEM_read_bio_PUBKEY(bio.get(), nullptr, nullptr, nullptr), EVP_PKEY_free);
    BIGNUM* raw_n = nullptr;
    BIGNUM* raw_e = nullptr;
    if (!key || EVP_PKEY_get_bn_param(key.get(), OSSL_PKEY_PARAM_RSA_N, &raw_n) != 1 ||
        EVP_PKEY_get_bn_param(key.get(), OSSL_PKEY_PARAM_RSA_E, &raw_e) != 1) {
        throw std::runtime_error("failed to derive test JWK");
    }
    BnPtr n(raw_n, BN_free);
    BnPtr e(raw_e, BN_free);
    std::string n_bytes(static_cast<std::size_t>(BN_num_bytes(n.get())), '\0');
    std::string e_bytes(static_cast<std::size_t>(BN_num_bytes(e.get())), '\0');
    BN_bn2bin(n.get(), reinterpret_cast<unsigned char*>(n_bytes.data()));
    BN_bn2bin(e.get(), reinterpret_cast<unsigned char*>(e_bytes.data()));
    return nlohmann::json{{"keys", nlohmann::json::array({{
        {"kid", kid},
        {"kty", "RSA"},
        {"alg", "RS256"},
        {"use", "sig"},
        {"key_ops", nlohmann::json::array({"verify"})},
        {"n", v2::auth::detail::base64url_encode(n_bytes)},
        {"e", v2::auth::detail::base64url_encode(e_bytes)},
    }})}}.dump();
}

}  // namespace

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

TEST(JwtValidatorTest, Rs256GenerateAndValidateToken) {
    v2::auth::JwtValidator::Config config{
        .public_key_pem = kTestRs256PublicKey,
        .private_key_pem = kTestRs256PrivateKey,
        .issuer = "boost-gateway",
        .audience = "game-client",
        .require_expiration = true,
    };
    v2::auth::JwtValidator validator(config);

    auto now = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();

    v2::auth::JwtPayload payload;
    payload.sub = "alice";
    payload.role = "player";
    payload.display_name = "Alice";
    payload.aud = "game-client";
    payload.iat = static_cast<std::uint64_t>(now);
    payload.exp = static_cast<std::uint64_t>(now + 3600);

    auto token = validator.generate(payload);
    ASSERT_FALSE(token.empty());

    auto result = validator.validate(token);
    EXPECT_TRUE(result.valid) << result.error;
    EXPECT_EQ(result.payload.sub, "alice");
    EXPECT_EQ(result.payload.aud, "game-client");
}

TEST(JwtValidatorTest, Rs256WrongPublicKeyRejected) {
    v2::auth::JwtValidator signer({
        .public_key_pem = kTestRs256PublicKey,
        .private_key_pem = kTestRs256PrivateKey,
        .issuer = "boost-gateway",
    });
    v2::auth::JwtValidator verifier({
        .public_key_pem = kTestRs256PublicKey,
        .issuer = "boost-gateway",
    });

    v2::auth::JwtPayload payload{.sub = "alice", .role = "player"};
    auto token = signer.generate(payload);
    ASSERT_FALSE(token.empty());

    v2::auth::JwtValidator wrong_verifier({
        .public_key_pem = kOtherRs256PublicKey,
        .issuer = "boost-gateway",
    });
    auto result = wrong_verifier.validate(token);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "invalid_signature");

    auto control = verifier.validate(token);
    EXPECT_TRUE(control.valid) << control.error;
}

TEST(JwtValidatorTest, AudienceRejectedWhenMismatched) {
    v2::auth::JwtValidator validator({
        .public_key_pem = kTestRs256PublicKey,
        .private_key_pem = kTestRs256PrivateKey,
        .issuer = "boost-gateway",
        .audience = "game-client",
    });

    v2::auth::JwtPayload payload;
    payload.sub = "alice";
    payload.role = "player";
    payload.aud = "admin-console";

    auto token = validator.generate(payload);
    ASSERT_FALSE(token.empty());

    auto result = validator.validate(token);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "invalid_audience");
}

TEST(JwtValidatorTest, StaticKeyRingSelectsKidAndRejectsMissingOrUnknownKid) {
    auto resolver = std::make_shared<v2::auth::StaticJwtKeyResolver>(
        std::unordered_map<std::string, std::string>{{"active", kTestRs256PublicKey},
                                                     {"next", kOtherRs256PublicKey}});
    v2::auth::JwtValidator verifier({
        .key_resolver = resolver,
        .issuer = "boost-gateway",
    });
    v2::auth::JwtValidator signer({
        .public_key_pem = kTestRs256PublicKey,
        .private_key_pem = kTestRs256PrivateKey,
        .issuer = "boost-gateway",
        .signing_kid = "active",
    });
    const auto token = signer.generate(v2::auth::JwtPayload{.sub = "alice"});
    EXPECT_TRUE(verifier.validate(token).valid);

    v2::auth::JwtValidator no_kid_signer({
        .public_key_pem = kTestRs256PublicKey,
        .private_key_pem = kTestRs256PrivateKey,
        .issuer = "boost-gateway",
    });
    EXPECT_EQ(verifier.validate(
                  no_kid_signer.generate(v2::auth::JwtPayload{.sub = "alice"})).error,
              "missing_kid");

    v2::auth::JwtValidator unknown_signer({
        .public_key_pem = kTestRs256PublicKey,
        .private_key_pem = kTestRs256PrivateKey,
        .issuer = "boost-gateway",
        .signing_kid = "unknown",
    });
    EXPECT_EQ(verifier.validate(
                  unknown_signer.generate(v2::auth::JwtPayload{.sub = "alice"})).error,
              "unknown_kid");
}

TEST(JwtValidatorTest, JwksResolverHonorsTtlStaleGraceAndExpiration) {
    using Clock = v2::auth::JwksKeyResolver::Clock;
    auto now = Clock::time_point(std::chrono::seconds(1000));
    v2::auth::JwksKeyResolver resolver({
        .fetcher = [] { return jwks_document("active", kTestRs256PublicKey); },
        .now = [&] { return now; },
        .ttl = std::chrono::seconds(10),
        .stale_grace = std::chrono::seconds(20),
        .minimum_refresh_interval = std::chrono::seconds(0),
    });
    resolver.refresh_now();
    EXPECT_TRUE(resolver.resolve("active").valid());
    now += std::chrono::seconds(11);
    EXPECT_TRUE(resolver.resolve("active").valid());
    EXPECT_TRUE(resolver.metrics().snapshot_stale);
    now += std::chrono::seconds(20);
    EXPECT_EQ(resolver.resolve("active").error, "jwks_stale_expired");
}

TEST(JwtValidatorTest, UnknownKidTriggersRateLimitedBackgroundRefresh) {
    std::atomic_int fetches{0};
    v2::auth::JwksKeyResolver resolver({
        .fetcher = [&] {
            const auto attempt = ++fetches;
            return jwks_document(attempt == 1 ? "old" : "new", kTestRs256PublicKey);
        },
        .ttl = std::chrono::hours(1),
        .stale_grace = std::chrono::hours(1),
        .minimum_refresh_interval = std::chrono::seconds(0),
    });
    resolver.refresh_now();
    EXPECT_EQ(resolver.resolve("new").error, "unknown_kid");
    for (int attempt = 0; attempt < 100 && fetches.load() < 2; ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    EXPECT_GE(fetches.load(), 2);
    EXPECT_TRUE(resolver.resolve("new").valid());
    EXPECT_EQ(resolver.metrics().unknown_kid_rejections, 1U);
}

TEST(JwtValidatorTest, JwksRejectsDuplicateKidAndUnsafeHttpPolicy) {
    auto duplicate = nlohmann::json::parse(jwks_document("dup", kTestRs256PublicKey));
    duplicate["keys"].push_back(duplicate["keys"].front());
    v2::auth::JwksKeyResolver resolver({
        .fetcher = [document = duplicate.dump()] { return document; },
    });
    EXPECT_THROW(resolver.refresh_now(), std::invalid_argument);
    EXPECT_EQ(resolver.metrics().refresh_failures, 1U);

    EXPECT_THROW(static_cast<void>(v2::auth::fetch_jwks_document({
                     .uri = "http://identity.example.test/keys",
                     .allowed_hosts = {"identity.example.test"},
                 })),
                 std::invalid_argument);
    EXPECT_THROW(static_cast<void>(v2::auth::fetch_jwks_document({
                     .uri = "https://identity.example.test/keys",
                     .allowed_hosts = {"other.example.test"},
                 })),
                 std::invalid_argument);
}

TEST(JwtValidatorTest, ExpirationBoundaryAndMalformedClaimTypesFailClosed) {
    v2::auth::JwtValidator signer({.secret = "secret", .require_expiration = true});
    const auto now = std::chrono::duration_cast<std::chrono::seconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    auto token = signer.generate(v2::auth::JwtPayload{
        .sub = "alice",
        .exp = static_cast<std::uint64_t>(now),
    });
    EXPECT_EQ(signer.validate(token).error, "token_expired");

    v2::auth::JwtPayload malformed{.sub = "alice"};
    malformed.extra["exp"] = "tomorrow";
    token = signer.generate(malformed);
    EXPECT_EQ(signer.validate(token).error, "invalid_payload_json");
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
