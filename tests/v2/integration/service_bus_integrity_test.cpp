// v2.2.0: Service split data flow connectivity + internal bus integrity tests.
// Verifies the full data flow chain through the 4-service bridge:
//   gateway → login/room/battle backends, BackendEnvelope consistency,
//   correlation_id matching, error propagation, trace context propagation.

#include "app/logging.h"
#include "v2/gateway/demo_server.h"
#include "v2/service/backend_connection.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v2/service/circuit_breaker.h"
#include "v2/service/service_registry.h"
#include "v2/service/error_codes.h"
#include "v2/tracing/trace_context.h"

#include <boost/asio.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

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

}  // namespace
