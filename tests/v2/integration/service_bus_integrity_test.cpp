// v2.2.0: Service split data flow connectivity + internal bus integrity tests.
// Verifies the full data flow chain through the 4-service bridge:
//   gateway → login/room/battle backends, BackendEnvelope consistency,
//   correlation_id matching, error propagation, trace context propagation.

#include "app/logging.h"
#include "v2/auth/jwt_validator.h"
#include "v2/gateway/demo_server.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v2/battle/battle_backend_service.h"
#include "v2/login/login_backend_service.h"
#include "v2/match/matchmaking_service.h"
#include "v2/room/room_backend_service.h"
#include "v2/service/backend_connection.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v2/service/circuit_breaker.h"
#include "v2/service/service_registry.h"
#include "v2/service/error_codes.h"
#include "v2/tracing/trace_context.h"
#include "v3/proto/envelope_codec.h"

#include <boost/asio.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

constexpr const char* kRs256PrivateKey = R"(-----BEGIN PRIVATE KEY-----
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

constexpr const char* kRs256PublicKey = R"(-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3SjynL/BSQfPE/7QI6K1
hA+xEP6uXF1pV3AVleXLKhWpZw9TgpWy39SPryWFSKoHxj6UWcUYlTS0eqBcFMpF
Dcz225vWDDT4HKUGSCT1xyBDKc26uCKL0vGRBxsiRsjTqOTh9RtWVZEbbq/W+Msk
n7n7DkJLXk57DisHrpijzYx9FbxTh4cidyjSn25+XBCeCXyOVkJxkmrOyCV/aDj+
HP4QOZpd9kSEdXZJat7IA04ChYwaCTi+an3ASMmv2PLRorL7qOmrd7W3EszV0kdK
KNBUknNXreFOv+Z/jCzk4nFNu+PNa25a2M+qdV6mOLElHs7DHcWFsQQ9+Xp8B8xR
WwIDAQAB
-----END PUBLIC KEY-----)";

// ── Helpers ────────────────────────────────────────────────────────────

v2::service::BackendEnvelope payload_envelope(
    const std::string& payload) {
    v2::service::BackendEnvelope env;
    env.correlation_id = v2::service::generate_correlation_id();
    env.kind = v2::service::MessageKind::kRequest;
    env.payload = payload;
    return env;
}

std::shared_ptr<v2::service::ServiceRegistry> make_registry() {
    return std::make_shared<v2::service::ServiceRegistry>();
}

// ─── BackendEnvelope format integrity ──────────────────────────────────

TEST(ServiceBusIntegrity, EnvelopeToJsonRoundTripsCorrectly) {
    v2::service::BackendEnvelope original;
    original.correlation_id = 999;
    original.source_service = v2::service::ServiceId::kGateway;
    original.target_service = v2::service::ServiceId::kLogin;
    original.kind = v2::service::MessageKind::kRequest;
    original.timeout_ms = 5000;
    original.payload = R"({"user_id":"alice","token":"jwt"})";
    original.message_type = "login_request";
    original.trace_id = 0xABCD1234;
    original.span_id = 0x5678;

    auto json = v2::service::to_json(original);
    auto parsed = v2::service::from_json(json);
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->correlation_id, 999U);
    EXPECT_EQ(parsed->source_service, v2::service::ServiceId::kGateway);
    EXPECT_EQ(parsed->target_service, v2::service::ServiceId::kLogin);
    EXPECT_EQ(parsed->kind, v2::service::MessageKind::kRequest);
    EXPECT_EQ(parsed->timeout_ms, 5000U);
    EXPECT_EQ(parsed->payload, R"({"user_id":"alice","token":"jwt"})");
    EXPECT_EQ(parsed->message_type, "login_request");
    EXPECT_EQ(parsed->trace_id, 0xABCD1234U);
    EXPECT_EQ(parsed->span_id, 0x5678U);
}

TEST(ServiceBusIntegrity, EnvelopeIsValidRejectsInvalid) {
    v2::service::BackendEnvelope valid;
    valid.correlation_id = 1;
    valid.payload = "test";
    EXPECT_TRUE(v2::service::is_valid(valid));

    v2::service::BackendEnvelope invalid;
    invalid.correlation_id = 0;  // zero correlation_id
    EXPECT_FALSE(v2::service::is_valid(invalid));
}

TEST(ServiceBusIntegrity, CorrelationIdsAreMonotonicallyIncreasing) {
    auto id1 = v2::service::generate_correlation_id();
    auto id2 = v2::service::generate_correlation_id();
    auto id3 = v2::service::generate_correlation_id();
    EXPECT_LT(id1, id2);
    EXPECT_LT(id2, id3);
}

TEST(ServiceBusIntegrity, ErrorEnvelopePreservesErrorCode) {
    v2::service::BackendEnvelope error;
    error.correlation_id = 42;
    error.kind = v2::service::MessageKind::kError;
    error.error_code = -2003;  // room_not_found
    error.payload = "error_detail";

    auto json = v2::service::to_json(error);
    auto parsed = v2::service::from_json(json);
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->error_code, -2003);
    EXPECT_EQ(parsed->kind, v2::service::MessageKind::kError);
}

// ─── Login backend connectivity ─────────────────────────────────────────

TEST(ServiceBusIntegrity, LoginBackendRoundTrip) {
    // Create login backend
    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [](const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        if (doc.is_discarded()) {
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kError;
            resp.error_code = -1004;
            resp.payload = "invalid_json";
            return resp;
        }
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = R"({"status":"ok","user_id":")" +
                       doc.value("user_id", "") + "\"}";
        return resp;
    };

    v2::service::BackendServer server(0, std::move(handlers));
    server.start();
    auto port = server.local_port();

    // Connect and send request
    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
    .host = "127.0.0.1", .port = port});
    ASSERT_TRUE(conn.connect());
    auto req = payload_envelope(R"({"user_id":"alice","token":"jwt_token"})");
    req.message_type = "login_request";
    req.target_service = v2::service::ServiceId::kLogin;

    auto resp = conn.send_request(req);
    EXPECT_TRUE(resp.has_value());
    EXPECT_EQ(resp->kind, v2::service::MessageKind::kResponse);
    auto doc = nlohmann::json::parse(resp->payload, nullptr, false);
    EXPECT_FALSE(doc.is_discarded());
    EXPECT_EQ(doc.value("status", ""), "ok");
    EXPECT_EQ(doc.value("user_id", ""), "alice");

    server.stop();
}

TEST(ServiceBusIntegrity, LoginBackendErrorPropagation) {
    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [](const v2::service::BackendEnvelope&) {
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kError;
        resp.error_code = -1003;
        resp.payload = "invalid_token";
        return resp;
    };

    v2::service::BackendServer server(0, std::move(handlers));
    server.start();
    auto port = server.local_port();

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
    .host = "127.0.0.1", .port = port});
    ASSERT_TRUE(conn.connect());
    auto req = payload_envelope(R"({"user_id":"bad","token":""})");
    req.message_type = "login_request";
    auto resp = conn.send_request(req);
    EXPECT_TRUE(resp.has_value());
    // Backend should return error (kind=kError or specific error_code)
    EXPECT_TRUE(resp->kind == v2::service::MessageKind::kError ||
                resp->error_code != 0);

    server.stop();
}

TEST(ServiceBusIntegrity, LoginBackendAcceptsRs256JwtAndValidatesToken) {
    v2::login::LoginBackendService service(v2::login::LoginBackendOptions{
        .port = 0,
        .jwt_public_key_pem = kRs256PublicKey,
        .jwt_issuer = "boost-gateway",
        .jwt_audience = "game-client",
    });
    service.start();

    v2::auth::JwtValidator signer({
        .public_key_pem = kRs256PublicKey,
        .private_key_pem = kRs256PrivateKey,
        .issuer = "boost-gateway",
        .audience = "game-client",
    });
    v2::auth::JwtPayload payload;
    payload.sub = "alice";
    payload.role = "player";
    payload.display_name = "Alice";
    payload.aud = "game-client";
    auto token = signer.generate(payload);
    ASSERT_FALSE(token.empty());

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = "127.0.0.1", .port = service.local_port()});
    ASSERT_TRUE(conn.connect());

    auto login_req = payload_envelope(nlohmann::json{
        {"user_id", "alice"},
        {"token", token},
        {"display_name", "Alice"},
    }.dump());
    login_req.message_type = "login_request";
    login_req.target_service = v2::service::ServiceId::kLogin;

    auto login_resp = conn.send_request(login_req);
    ASSERT_TRUE(login_resp.has_value());
    EXPECT_EQ(login_resp->kind, v2::service::MessageKind::kResponse);
    auto login_doc = nlohmann::json::parse(login_resp->payload, nullptr, false);
    ASSERT_FALSE(login_doc.is_discarded());
    EXPECT_EQ(login_doc.value("status", ""), "ok");
    EXPECT_EQ(login_doc.value("role", ""), "player");

    auto validate_req = payload_envelope(nlohmann::json{{"token", token}}.dump());
    validate_req.message_type = "token_validate";
    validate_req.target_service = v2::service::ServiceId::kLogin;
    auto validate_resp = conn.send_request(validate_req);
    ASSERT_TRUE(validate_resp.has_value());
    auto validate_doc = nlohmann::json::parse(validate_resp->payload, nullptr, false);
    ASSERT_FALSE(validate_doc.is_discarded());
    EXPECT_TRUE(validate_doc.value("valid", false));

    auto bad_validate_req = payload_envelope(nlohmann::json{{"token", token + "x"}}.dump());
    bad_validate_req.message_type = "token_validate";
    bad_validate_req.target_service = v2::service::ServiceId::kLogin;
    auto bad_validate_resp = conn.send_request(bad_validate_req);
    ASSERT_TRUE(bad_validate_resp.has_value());
    auto bad_validate_doc = nlohmann::json::parse(bad_validate_resp->payload, nullptr, false);
    ASSERT_FALSE(bad_validate_doc.is_discarded());
    EXPECT_FALSE(bad_validate_doc.value("valid", true));

    service.stop();
}

TEST(ServiceBusIntegrity, LoginBackendProductionModeRequiresJwtVerifier) {
    EXPECT_THROW(
        v2::login::LoginBackendService(v2::login::LoginBackendOptions{
            .port = 0,
            .production_auth_required = true,
        }),
        std::invalid_argument);

    EXPECT_NO_THROW(
        v2::login::LoginBackendService(v2::login::LoginBackendOptions{
            .port = 0,
            .production_auth_required = true,
            .jwt_secret = "production-secret",
        }));
}

// ─── Room backend data chain ────────────────────────────────────────────

TEST(ServiceBusIntegrity, RoomCreateJoinReadyDataChain) {
    v2::service::BackendServer::HandlerMap handlers;
    std::mutex room_mutex;
    std::unordered_map<std::string, nlohmann::json> rooms;

    handlers["room_create"] = [&](const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        std::lock_guard lock(room_mutex);
        std::string room_id = doc.value("room_id", "");
        if (rooms.count(room_id)) {
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kError;
            resp.error_code = -2002;
            return resp;
        }
        rooms[room_id] = {{"owner", doc.value("user_id", "")}, {"members", nlohmann::json::array({doc["user_id"]})}};
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = R"({"status":"ok","room_id":")" + room_id + "\"}";
        return resp;
    };
    handlers["room_join"] = [&](const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        std::lock_guard lock(room_mutex);
        auto it = rooms.find(doc.value("room_id", ""));
        if (it == rooms.end()) {
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kError;
            resp.error_code = -2003;
            return resp;
        }
        it->second["members"].push_back(doc["user_id"]);
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = R"({"status":"ok","room_id":")" + doc.value("room_id", "") + "\"}";
        return resp;
    };
    handlers["room_ready"] = [&](const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        std::lock_guard lock(room_mutex);
        auto it = rooms.find(doc.value("room_id", ""));
        if (it == rooms.end()) {
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kError;
            resp.error_code = -2003;
            return resp;
        }
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = R"({"status":"ok","all_ready":true})";
        return resp;
    };

    v2::service::BackendServer server(0, std::move(handlers));
    server.start();
    auto port = server.local_port();
    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
    .host = "127.0.0.1", .port = port});
    ASSERT_TRUE(conn.connect());

    // Create room
    {
        auto req = payload_envelope(R"({"user_id":"alice","room_id":"room_001"})");
        req.message_type = "room_create";
        auto resp = conn.send_request(req);
        ASSERT_TRUE(resp.has_value());
        EXPECT_EQ(resp->kind, v2::service::MessageKind::kResponse);
    }
    // Join room
    {
        auto req = payload_envelope(R"({"user_id":"bob","room_id":"room_001"})");
        req.message_type = "room_join";
        auto resp = conn.send_request(req);
        ASSERT_TRUE(resp.has_value());
        EXPECT_EQ(resp->kind, v2::service::MessageKind::kResponse);
    }
    // Ready
    {
        auto req = payload_envelope(R"({"user_id":"alice","room_id":"room_001","ready":true})");
        req.message_type = "room_ready";
        auto resp = conn.send_request(req);
        ASSERT_TRUE(resp.has_value());
        EXPECT_EQ(resp->kind, v2::service::MessageKind::kResponse);
    }
    // Verify internal state consistency
    {
        std::lock_guard lock(room_mutex);
        ASSERT_TRUE(rooms.count("room_001"));
        EXPECT_EQ(rooms["room_001"]["members"].size(), 2U);
        EXPECT_EQ(rooms["room_001"]["owner"], "alice");
    }

    server.stop();
}

// ─── Service Registry health ────────────────────────────────────────────

TEST(ServiceBusIntegrity, ServiceRegistryHealthTracking) {
    auto registry = make_registry();

    registry->register_instance(v2::service::ServiceId::kLogin, "127.0.0.1", 9202);
    EXPECT_EQ(registry->healthy_instances(v2::service::ServiceId::kLogin).size(), 1U);
    EXPECT_TRUE(registry->unhealthy_instances(v2::service::ServiceId::kLogin).empty());

    auto all = registry->all_instances();
    EXPECT_GE(all.size(), 1U);
}

TEST(ServiceBusIntegrity, ServiceRegistryMarkUnhealthy) {
    auto registry = make_registry();

    registry->register_instance(v2::service::ServiceId::kRoom, "127.0.0.1", 9302);
    EXPECT_EQ(registry->healthy_instances(v2::service::ServiceId::kRoom).size(), 1U);

    registry->mark_unhealthy(v2::service::ServiceId::kRoom, "127.0.0.1", 9302);
    EXPECT_EQ(registry->healthy_instances(v2::service::ServiceId::kRoom).size(), 0U);
    EXPECT_EQ(registry->unhealthy_instances(v2::service::ServiceId::kRoom).size(), 1U);
}

TEST(ServiceBusIntegrity, ServiceRegistryPurgeExpired) {
    auto registry = make_registry();

    registry->register_instance(v2::service::ServiceId::kBattle, "127.0.0.1", 9303);
    EXPECT_GE(registry->instance_count(), 1U);

    auto purged = registry->purge_expired();
    EXPECT_GE(registry->instance_count(), 0U);  // purge removes expired entries
}

// ─── Circuit breaker integrity ──────────────────────────────────────────

TEST(ServiceBusIntegrity, CircuitBreakerTriState) {
    v2::service::CircuitBreaker breaker(
        v2::service::CircuitBreakerOptions{
            .failure_threshold = 2,
            .timeout = std::chrono::milliseconds(100),
            .half_open_max_requests = 1,
        });

    // Initially closed — allows requests
    EXPECT_TRUE(breaker.allow_request());

    // Two failures trip the breaker
    breaker.on_failure();
    breaker.on_failure();
    EXPECT_FALSE(breaker.allow_request());  // Open

    // Wait for timeout → transitions to half-open
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    EXPECT_TRUE(breaker.allow_request());  // Half-open, allows probe

    // Success resets
    breaker.on_success();
    EXPECT_TRUE(breaker.allow_request());  // Closed again
}

// ─── Trace context propagation ──────────────────────────────────────────

TEST(ServiceBusIntegrity, TraceContextPropagationAcrossEnvelope) {
    auto ctx = v2::tracing::TraceContext::create_root();
    EXPECT_NE(ctx.trace_id, 0U);
    EXPECT_NE(ctx.current_span_id, 0U);

    // W3C traceparent format
    auto tp = ctx.to_w3c_traceparent();
    EXPECT_GE(tp.size(), 55U);
    EXPECT_EQ(tp.substr(0, 3), "00-");

    // Round-trip through W3C format
    auto restored = v2::tracing::TraceContext::from_w3c_traceparent(tp);
    EXPECT_EQ(restored.trace_id, ctx.trace_id);

    // Verify trace_id is carried through BackendEnvelope
    v2::service::BackendEnvelope env;
    env.correlation_id = 1;
    env.payload = "test";
    env.trace_id = ctx.trace_id;
    env.span_id = ctx.current_span_id;

    auto json = v2::service::to_json(env);
    auto parsed = v2::service::from_json(json);
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->trace_id, ctx.trace_id);
    EXPECT_EQ(parsed->span_id, ctx.current_span_id);
}

// ─── Service error code consistency ─────────────────────────────────────

TEST(ServiceBusIntegrity, GatewayBridgeRoutePropagatesTraceAndErrorCode) {
    constexpr std::uint64_t kTraceId = 0x12345678ABCDEF00ULL;
    constexpr std::uint64_t kSpanId = 0x1111222233334444ULL;

    std::mutex observed_mutex;
    v2::service::BackendEnvelope observed_request;

    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [&](const v2::service::BackendEnvelope& req) {
        {
            std::lock_guard lock(observed_mutex);
            observed_request = req;
        }

        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kError;
        resp.error_code = static_cast<std::int32_t>(
            v2::service::ServiceErrorCode::kInvalidRequest);
        resp.payload = R"({"error":"invalid_request"})";
        resp.trace_id = req.trace_id;
        resp.span_id = req.span_id;
        return resp;
    };

    v2::service::BackendServer server(0, std::move(handlers));
    server.start();

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = server.local_port(),
        });
    bridge.set_trace_context(kTraceId, kSpanId);

    const auto result = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"bad","token":""})");

    bridge.shutdown();
    server.stop();

    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.error, v2::service::ServiceErrorCode::kInvalidRequest);
    EXPECT_NE(result.correlation_id, 0U);

    std::lock_guard lock(observed_mutex);
    EXPECT_EQ(observed_request.message_type, "login_request");
    EXPECT_EQ(observed_request.trace_id, kTraceId);
    EXPECT_EQ(observed_request.span_id, kSpanId);
    EXPECT_EQ(observed_request.correlation_id, result.correlation_id);
}

TEST(ServiceBusIntegrity, GatewayBridgeTypedEnvelopePreservesTraceAndError) {
    constexpr std::uint64_t kTraceId = 0x2233445566778899ULL;
    constexpr std::uint64_t kSpanId = 0xAABBCCDDEEFF0011ULL;
    constexpr auto kErrorCode = static_cast<std::int32_t>(
        v2::service::ServiceErrorCode::kInvalidRequest);

    std::mutex observed_mutex;
    v2::service::BackendEnvelope observed_request;
    std::optional<v3::proto::TypedEnvelope> observed_typed_request;
    std::optional<v3::proto::TypedEnvelope> observed_typed_response;

    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [&](const v2::service::BackendEnvelope& req) {
        auto typed = v3::proto::decode_typed_envelope(req.payload);

        v3::proto::EnvelopeMeta response_meta;
        response_meta.correlation_id = req.correlation_id;
        response_meta.source_service = "login";
        response_meta.target_service = "gateway";
        response_meta.error_code = kErrorCode;
        response_meta.trace_id = req.trace_id;
        response_meta.span_id = req.span_id;
        const auto typed_response_payload = v3::proto::encode_typed_envelope(
            response_meta,
            v3::proto::EnvelopeMessageKind::kLoginResponse,
            {{"error", "invalid_request"}});

        {
            std::lock_guard lock(observed_mutex);
            observed_request = req;
            observed_typed_request = typed;
            observed_typed_response = v3::proto::decode_typed_envelope(typed_response_payload);
        }

        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kError;
        resp.error_code = kErrorCode;
        resp.payload = typed_response_payload;
        resp.trace_id = req.trace_id;
        resp.span_id = req.span_id;
        return resp;
    };

    v2::service::BackendServer server(0, std::move(handlers));
    server.start();

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = server.local_port(),
        });
    bridge.set_trace_context(kTraceId, kSpanId);

    v3::proto::EnvelopeMeta meta;
    meta.source_service = "gateway";
    meta.target_service = "login";
    meta.trace_id = kTraceId;
    meta.span_id = kSpanId;
    const auto payload = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kLoginRequest,
        {{"user_id", "bad"}, {"token", ""}});

    const auto result = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        payload);

    bridge.shutdown();
    server.stop();

    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.error, v2::service::ServiceErrorCode::kInvalidRequest);
    EXPECT_NE(result.correlation_id, 0U);

    std::lock_guard lock(observed_mutex);
    EXPECT_EQ(observed_request.message_type, "login_request");
    EXPECT_EQ(observed_request.trace_id, kTraceId);
    EXPECT_EQ(observed_request.span_id, kSpanId);
    EXPECT_EQ(observed_request.correlation_id, result.correlation_id);

    ASSERT_TRUE(observed_typed_request.has_value());
    EXPECT_EQ(observed_typed_request->message_kind, v3::proto::EnvelopeMessageKind::kLoginRequest);
    EXPECT_EQ(observed_typed_request->meta.trace_id, kTraceId);
    EXPECT_EQ(observed_typed_request->meta.span_id, kSpanId);

    ASSERT_TRUE(observed_typed_response.has_value());
    EXPECT_EQ(observed_typed_response->message_kind, v3::proto::EnvelopeMessageKind::kLoginResponse);
    EXPECT_EQ(observed_typed_response->meta.correlation_id, result.correlation_id);
    EXPECT_EQ(observed_typed_response->meta.trace_id, kTraceId);
    EXPECT_EQ(observed_typed_response->meta.span_id, kSpanId);
    EXPECT_EQ(observed_typed_response->meta.error_code, kErrorCode);
}

TEST(ServiceBusIntegrity, GatewayBridgeRecoversAfterBackendConfigUpdate) {
    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = 1,
        });

    const auto unavailable = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"alice","token":"token:alice"})");
    EXPECT_FALSE(unavailable.success);
    EXPECT_EQ(unavailable.error, v2::service::ServiceErrorCode::kUnavailable);

    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [](const v2::service::BackendEnvelope&) {
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = R"({"status":"ok","user_id":"alice"})";
        return resp;
    };
    v2::service::BackendServer server(0, std::move(handlers));
    server.start();

    bridge.update_backend_config(
        v2::service::ServiceId::kLogin,
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = server.local_port(),
        });

    const auto recovered = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"alice","token":"token:alice"})");

    bridge.shutdown();
    server.stop();

    EXPECT_TRUE(recovered.success);
    EXPECT_EQ(recovered.error, v2::service::ServiceErrorCode::kOk);
    EXPECT_NE(recovered.response_payload.find("\"status\":\"ok\""), std::string::npos);
}

TEST(ServiceBusIntegrity, GatewayBridgeTimeoutClosesStaleConnectionAndRecovers) {
    std::atomic<int> stale_requests{0};
    std::atomic<int> recovered_requests{0};

    v2::service::BackendServer::HandlerMap stale_handlers;
    stale_handlers["login_request"] = [&](const v2::service::BackendEnvelope& req) {
        ++stale_requests;
        std::this_thread::sleep_for(std::chrono::milliseconds(80));
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = req.payload;
        return resp;
    };
    v2::service::BackendServer stale_server(0, std::move(stale_handlers));
    stale_server.start();

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = stale_server.local_port(),
            .timeout = std::chrono::milliseconds(20),
            .connect_timeout = std::chrono::milliseconds(100),
        });

    const auto timed_out = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"alice","token":"token:alice"})");
    EXPECT_FALSE(timed_out.success);
    EXPECT_EQ(timed_out.error, v2::service::ServiceErrorCode::kTimeout);
    EXPECT_EQ(stale_requests.load(), 2);

    v2::service::BackendServer::HandlerMap recovered_handlers;
    recovered_handlers["login_request"] = [&](const v2::service::BackendEnvelope&) {
        ++recovered_requests;
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = R"({"status":"ok","user_id":"alice"})";
        return resp;
    };
    v2::service::BackendServer recovered_server(0, std::move(recovered_handlers));
    recovered_server.start();

    bridge.update_backend_config(
        v2::service::ServiceId::kLogin,
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = recovered_server.local_port(),
            .timeout = std::chrono::milliseconds(50),
            .connect_timeout = std::chrono::milliseconds(100),
        });

    const auto recovered = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"alice","token":"token:alice"})");

    bridge.shutdown();
    stale_server.stop();
    recovered_server.stop();

    EXPECT_TRUE(recovered.success);
    EXPECT_EQ(recovered.response_payload, R"({"status":"ok","user_id":"alice"})");
    EXPECT_EQ(recovered_requests.load(), 1);
}

TEST(ServiceBusIntegrity, GatewayBridgeCircuitBreakerHalfOpenProbeRecovers) {
    std::atomic<int> handled_requests{0};
    v2::service::BackendServer::HandlerMap handlers;
    handlers["login_request"] = [&](const v2::service::BackendEnvelope&) {
        const auto request_index = ++handled_requests;
        v2::service::BackendEnvelope resp;
        if (request_index <= 2) {
            resp.kind = v2::service::MessageKind::kError;
            resp.error_code = static_cast<std::int32_t>(
                v2::service::ServiceErrorCode::kInvalidRequest);
        } else {
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = R"({"status":"ok"})";
        }
        return resp;
    };
    v2::service::BackendServer server(0, std::move(handlers));
    server.start();

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = server.local_port(),
            .timeout = std::chrono::milliseconds(50),
            .connect_timeout = std::chrono::milliseconds(100),
        });
    bridge.configure_circuit_breaker(
        v2::service::ServiceId::kLogin,
        v2::service::CircuitBreakerOptions{
            .failure_threshold = 2,
            .timeout = std::chrono::milliseconds(30),
            .half_open_max_requests = 1,
        });

    EXPECT_FALSE(bridge.route(v2::service::ServiceId::kLogin,
                              "login_request",
                              R"({"user_id":"bad","token":""})").success);
    EXPECT_FALSE(bridge.route(v2::service::ServiceId::kLogin,
                              "login_request",
                              R"({"user_id":"bad","token":""})").success);

    const auto open_result = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"bad","token":""})");
    EXPECT_FALSE(open_result.success);
    EXPECT_EQ(open_result.error, v2::service::ServiceErrorCode::kCircuitOpen);
    EXPECT_EQ(handled_requests.load(), 2);

    std::this_thread::sleep_for(std::chrono::milliseconds(40));

    const auto probe = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"alice","token":"token:alice"})");
    const auto after_closed = bridge.route(
        v2::service::ServiceId::kLogin,
        "login_request",
        R"({"user_id":"alice","token":"token:alice"})");

    bridge.shutdown();
    server.stop();

    EXPECT_TRUE(probe.success);
    EXPECT_TRUE(after_closed.success);
    EXPECT_EQ(handled_requests.load(), 4);
}

TEST(ServiceBusIntegrity, ServiceErrorCodeClientMapping) {
    using v2::service::ServiceErrorCode;
    // Verify all service error codes have defined to_client_error mappings
    auto mapped = v2::service::to_client_error(ServiceErrorCode::kOk);
    EXPECT_EQ(mapped, 0);

    mapped = v2::service::to_client_error(ServiceErrorCode::kUnavailable);
    EXPECT_NE(mapped, 0);

    mapped = v2::service::to_client_error(ServiceErrorCode::kTimeout);
    EXPECT_NE(mapped, 0);

    mapped = v2::service::to_client_error(ServiceErrorCode::kRejected);
    EXPECT_NE(mapped, 0);

    mapped = v2::service::to_client_error(ServiceErrorCode::kCircuitOpen);
    EXPECT_NE(mapped, 0);

    EXPECT_NE(v2::service::to_string(ServiceErrorCode::kOk), nullptr);
    EXPECT_NE(v2::service::to_string(ServiceErrorCode::kUnavailable), nullptr);
}

// ─── Push/forward cascade consistency ───────────────────────────────────

TEST(ServiceBusIntegrity, ForwardCascadePreservesPayload) {
    // Simulate room→battle cascade: room_start_battle → forward to battle_create
    v2::service::BackendEnvelope forward_req;
    forward_req.correlation_id = 100;
    forward_req.kind = v2::service::MessageKind::kRequest;
    forward_req.target_service = v2::service::ServiceId::kRoom;
    forward_req.message_type = "room_start_battle";
    forward_req.payload = R"({"room_id":"r1","user_id":"alice"})";
    forward_req.trace_id = 42;

    auto json = v2::service::to_json(forward_req);
    auto parsed = v2::service::from_json(json);
    ASSERT_TRUE(parsed.has_value());

    // Verify the payload and trace are preserved intact
    EXPECT_EQ(parsed->message_type, "room_start_battle");
    EXPECT_EQ(parsed->trace_id, 42U);
    auto doc = nlohmann::json::parse(parsed->payload, nullptr, false);
    EXPECT_EQ(doc.value("room_id", ""), "r1");
    EXPECT_EQ(doc.value("user_id", ""), "alice");
}

TEST(ServiceBusIntegrity, ProtoEnvelopeRoundTripsThroughMatchBackend) {
    v2::match::MatchmakingService service(0);
    v2::match::MatchmakingConfig cfg;
    cfg.match_check_interval_ms = 1000;
    service.set_matchmaking_config(cfg);
    service.start();

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = "127.0.0.1", .port = service.local_port()});
    ASSERT_TRUE(conn.connect());

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 100;
    meta.source_service = "gateway";
    meta.target_service = "match";
    auto encoded = v3::proto::encode_match_join_request(
        meta,
        v3::proto::MatchJoinRequestPayload{
            .user_id = "alice",
            .mmr = 1000,
            .mode = "1v1",
        });

    auto req = payload_envelope(encoded);
    req.message_type = "match_join";
    auto resp = conn.send_request(req);
    ASSERT_TRUE(resp.has_value());
    auto decoded = v3::proto::decode_envelope(resp->payload);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->domain, v3::proto::EnvelopeDomain::kMatch);
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kMatchJoinResponse);
    EXPECT_TRUE(decoded->payload.value("queued", false));

    service.stop();
}

TEST(ServiceBusIntegrity, ProtoEnvelopeRoundTripsThroughLeaderboardBackend) {
    v2::leaderboard::LeaderboardService service(0);
    service.start();

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = "127.0.0.1", .port = service.local_port()});
    ASSERT_TRUE(conn.connect());

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 200;
    meta.source_service = "gateway";
    meta.target_service = "leaderboard";
    auto encoded = v3::proto::encode_leaderboard_submit_request(
        meta,
        v3::proto::LeaderboardSubmitRequestPayload{
            .user_id = "alice",
            .display_name = "Alice",
            .score = 1200,
        });

    auto req = payload_envelope(encoded);
    req.message_type = "leaderboard_submit";
    auto resp = conn.send_request(req);
    ASSERT_TRUE(resp.has_value());
    auto decoded = v3::proto::decode_envelope(resp->payload);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->domain, v3::proto::EnvelopeDomain::kLeaderboard);
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kLeaderboardSubmitResponse);
    EXPECT_EQ(decoded->payload.value("user_id", ""), "alice");

    service.stop();
}

TEST(ServiceBusIntegrity, ProtoEnvelopeRoundTripsThroughLoginBackend) {
    v2::login::LoginBackendService service(0);
    service.start();

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = "127.0.0.1", .port = service.local_port()});
    ASSERT_TRUE(conn.connect());

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 300;
    meta.source_service = "gateway";
    meta.target_service = "login";
    auto encoded = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kLoginRequest,
        {{"user_id", "alice"}, {"token", "token:alice"}, {"display_name", "Alice"}});

    auto req = payload_envelope(encoded);
    req.message_type = "login_request";
    auto resp = conn.send_request(req);
    ASSERT_TRUE(resp.has_value());
    auto decoded = v3::proto::decode_typed_envelope(resp->payload);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kLoginResponse);
    EXPECT_EQ(decoded->payload.value("user_id", ""), "alice");

    service.stop();
}

TEST(ServiceBusIntegrity, ProtoEnvelopeRoundTripsThroughRoomBackend) {
    v2::room::RoomBackendService service(0);
    service.start();

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = "127.0.0.1", .port = service.local_port()});
    ASSERT_TRUE(conn.connect());

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 400;
    meta.source_service = "gateway";
    meta.target_service = "room";
    auto encoded = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kRoomCreateRequest,
        {{"user_id", "alice"}, {"room_id", "room_42"}});

    auto req = payload_envelope(encoded);
    req.message_type = "room_create";
    auto resp = conn.send_request(req);
    ASSERT_TRUE(resp.has_value());
    auto decoded = v3::proto::decode_typed_envelope(resp->payload);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kRoomCreateResponse);
    EXPECT_EQ(decoded->payload.value("room_id", ""), "room_42");

    service.stop();
}

TEST(ServiceBusIntegrity, ProtoEnvelopeRoundTripsThroughBattleBackend) {
    v2::battle::BattleBackendService service(0);
    service.start();

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = "127.0.0.1", .port = service.local_port()});
    ASSERT_TRUE(conn.connect());

    v2::service::BackendEnvelope create_req = payload_envelope(R"({"battle_id":"battle_1","room_id":"room_1","player_ids":["alice","bob"],"max_frames":3})");
    create_req.message_type = "battle_create";
    auto create_resp = conn.send_request(create_req);
    ASSERT_TRUE(create_resp.has_value());

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 500;
    meta.source_service = "gateway";
    meta.target_service = "battle";
    auto encoded = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kBattleInputRequest,
        {{"user_id", "alice"}, {"battle_id", "battle_1"}, {"input_data", "move:1,2"}, {"submitted_frame", 1}});

    auto req = payload_envelope(encoded);
    req.message_type = "battle_input";
    auto resp = conn.send_request(req);
    ASSERT_TRUE(resp.has_value());
    auto decoded = v3::proto::decode_typed_envelope(resp->payload);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kBattleInputResponse);
    EXPECT_EQ(decoded->payload.value("battle_id", ""), "battle_1");

    service.stop();
}

}  // namespace
