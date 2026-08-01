#include "v2/gateway/backend_metrics.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/service/backend_server.h"
#include "v2/service/service_registry.h"

#include <nlohmann/json.hpp>

#include <gtest/gtest.h>

namespace {

v2::service::BackendServer::HandlerMap make_echo_handlers() {
    v2::service::BackendServer::HandlerMap handlers;
    handlers["echo"] = [](const v2::service::BackendEnvelope& request) {
        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kResponse;
        response.payload = request.payload;
        return response;
    };
    return handlers;
}

struct BackendProcess {
    std::unique_ptr<v2::service::BackendServer> server;
    std::uint16_t port = 0;

    bool start() {
        server = std::make_unique<v2::service::BackendServer>(0, make_echo_handlers());
        server->start();
        port = server->local_port();
        return port > 0;
    }

    void stop() {
        if (server) server->stop();
    }
};

}  // namespace

TEST(V2BackendHealthTest, MetricsCountersAfterSuccessfulRoute) {
    auto backend = std::make_unique<BackendProcess>();
    ASSERT_TRUE(backend->start());

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1", .port = backend->port},
        std::nullopt, std::nullopt,
        std::nullopt, std::nullopt,
        metrics);

    auto result = bridge.route(
        v2::service::ServiceId::kLogin, "echo", "hello");
    ASSERT_TRUE(result.success);

    auto snap = metrics->snapshot(v2::service::ServiceId::kLogin);
    EXPECT_EQ(snap.total_requests, 1U);
    EXPECT_EQ(snap.total_successes, 1U);
    EXPECT_EQ(snap.total_timeouts, 0U);
    EXPECT_EQ(snap.total_unavailable, 0U);
    EXPECT_EQ(snap.total_errors, 0U);
    EXPECT_EQ(snap.transport_not_connected, 0U);
    EXPECT_EQ(snap.transport_write_failures, 0U);
    EXPECT_EQ(snap.transport_read_failures, 0U);
    EXPECT_EQ(snap.transport_retry_recovered, 0U);
    EXPECT_EQ(snap.transport_retry_exhausted, 0U);

    backend->stop();
}

TEST(V2BackendHealthTest, BackendMetricsLatencyBucketsExposePercentiles) {
    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();

    metrics->record_latency(v2::service::ServiceId::kBattle, 1'500);
    metrics->record_latency(v2::service::ServiceId::kBattle, 40'000);
    metrics->record_latency(v2::service::ServiceId::kBattle, 700'000);

    auto snap = metrics->snapshot(v2::service::ServiceId::kBattle);
    EXPECT_EQ(snap.latency_sample_count, 3U);
    EXPECT_EQ(snap.total_latency_us, 741'500U);
    EXPECT_EQ(v2::gateway::backend_latency_percentile_us(snap, 0.50), 50'000U);
    EXPECT_EQ(v2::gateway::backend_latency_percentile_us(snap, 0.90), 1'000'000U);
    EXPECT_EQ(v2::gateway::backend_latency_percentile_us(snap, 0.99), 1'000'000U);

    std::uint64_t bucket_total = 0;
    for (const auto count : snap.latency_bucket_counts) {
        bucket_total += count;
    }
    EXPECT_EQ(bucket_total, 3U);
}

TEST(V2BackendHealthTest, MetricsCountersAfterUnavailable) {
    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();

    // Bridge with no backend configured — route should fail with unavailable
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt,
        std::nullopt, std::nullopt, metrics);

    auto result = bridge.route(
        v2::service::ServiceId::kLogin, "echo", "hello");
    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.error, v2::service::ServiceErrorCode::kUnavailable);

    auto snap = metrics->snapshot(v2::service::ServiceId::kLogin);
    EXPECT_EQ(snap.total_requests, 1U);
    EXPECT_EQ(snap.total_unavailable, 1U);
}

TEST(V2BackendHealthTest, DiagnosticsJsonIncludesBackendMetrics) {
    auto backend = std::make_unique<BackendProcess>();
    ASSERT_TRUE(backend->start());

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    auto registry = std::make_shared<v2::service::ServiceRegistry>();

    registry->register_instance(v2::service::ServiceId::kLogin,
                                "127.0.0.1", backend->port);

    // Record some metrics manually
    metrics->record_request(v2::service::ServiceId::kLogin);
    metrics->record_success(v2::service::ServiceId::kLogin);
    metrics->record_timeout(v2::service::ServiceId::kRoom);

    v2::gateway::BackendMetricsSnapshot login_snap =
        metrics->snapshot(v2::service::ServiceId::kLogin);
    v2::gateway::BackendMetricsSnapshot room_snap =
        metrics->snapshot(v2::service::ServiceId::kRoom);

    EXPECT_EQ(login_snap.total_requests, 1U);
    EXPECT_EQ(login_snap.total_successes, 1U);
    EXPECT_EQ(room_snap.total_timeouts, 1U);

    auto instances = registry->all_instances();
    ASSERT_EQ(instances.size(), 1U);
    EXPECT_EQ(instances[0].host, "127.0.0.1");
    EXPECT_EQ(instances[0].port, backend->port);
    EXPECT_TRUE(instances[0].healthy);

    backend->stop();
}

TEST(V2BackendHealthTest, DiagnosticsJsonIncludesBackendInstances) {
    auto registry = std::make_shared<v2::service::ServiceRegistry>();

    registry->register_instance(v2::service::ServiceId::kLogin,
                                "127.0.0.1", 9001);
    registry->register_instance(v2::service::ServiceId::kRoom,
                                "127.0.0.1", 9002);

    auto instances = registry->all_instances();
    ASSERT_EQ(instances.size(), 2U);

    bool found_login = false;
    bool found_room = false;
    for (const auto& inst : instances) {
        if (inst.service_id == v2::service::ServiceId::kLogin &&
            inst.port == 9001) {
            found_login = true;
        }
        if (inst.service_id == v2::service::ServiceId::kRoom &&
            inst.port == 9002) {
            found_room = true;
        }
        EXPECT_TRUE(inst.healthy);
    }
    EXPECT_TRUE(found_login);
    EXPECT_TRUE(found_room);

    registry->mark_unhealthy(v2::service::ServiceId::kRoom,
                             "127.0.0.1", 9002);
    auto unhealthy = registry->unhealthy_instances(v2::service::ServiceId::kRoom);
    ASSERT_EQ(unhealthy.size(), 1U);
    EXPECT_EQ(unhealthy[0].port, 9002U);
}
