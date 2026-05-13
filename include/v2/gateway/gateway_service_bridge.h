#pragma once

#include "v2/gateway/backend_metrics.h"
#include "v2/service/backend_connection.h"
#include "v2/service/circuit_breaker.h"
#include "v2/service/error_codes.h"
#include "v2/service/service_registry.h"
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/consistent_hash.h"
#include "v3/tracing/otel_exporter.h"

#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

namespace v2::gateway {

class GatewayServiceBridge {
public:
    struct BackendRoutingResult {
        bool success = false;
        std::string response_payload;
        v2::service::ServiceErrorCode error = v2::service::ServiceErrorCode::kOk;
        std::uint64_t correlation_id = 0;
    };

    struct BackendConfig {
        std::string host = "127.0.0.1";
        std::uint16_t port = 0;
    };

    explicit GatewayServiceBridge(
        std::optional<BackendConfig> login_config = std::nullopt,
        std::optional<BackendConfig> room_config = std::nullopt,
        std::optional<BackendConfig> battle_config = std::nullopt,
        std::shared_ptr<BackendMetrics> metrics = nullptr);
    ~GatewayServiceBridge();

    GatewayServiceBridge(const GatewayServiceBridge&) = delete;
    GatewayServiceBridge& operator=(const GatewayServiceBridge&) = delete;
    GatewayServiceBridge(GatewayServiceBridge&&) = delete;
    GatewayServiceBridge& operator=(GatewayServiceBridge&&) = delete;

    /// Route a message to a backend. When shard_key is non-empty and
    /// a ShardRouter is configured, consistent hashing picks which
    /// backend node to use (session-affinity for rooms/battles).
    [[nodiscard]] BackendRoutingResult route(
        v2::service::ServiceId target,
        const std::string& message_type,
        const std::string& payload,
        const std::string& shard_key = "");

    [[nodiscard]] bool is_backend_available(v2::service::ServiceId service) const;

    void set_service_registry(
        std::shared_ptr<v2::service::ServiceRegistry> registry);
    [[nodiscard]] std::shared_ptr<BackendMetrics> get_metrics() const;
    [[nodiscard]] std::shared_ptr<v2::service::ServiceRegistry> get_registry() const;

    // v3.0.0: Set ClusterRouter for dynamic service discovery.
    // When set, ensure_connection() discovers backend addresses from the
    // router instead of using the static BackendConfig.
    void set_cluster_router(
        std::shared_ptr<v3::cluster::ClusterRouter> router);
    [[nodiscard]] std::shared_ptr<v3::cluster::ClusterRouter>
    get_cluster_router() const;

    // v3.0.0: Set ShardRouter for consistent-hash-based session affinity.
    // When set together with a ClusterRouter, room_id/battle_id are used as
    // shard keys to pin rooms and battles to specific backend nodes.
    void set_shard_router(
        std::shared_ptr<v3::cluster::ShardRouter> router);
    [[nodiscard]] std::shared_ptr<v3::cluster::ShardRouter>
    get_shard_router() const;

    void update_backend_config(v2::service::ServiceId service,
                                std::optional<BackendConfig> config);

    // v2.2.0: Set trace context for cross-service distributed tracing.
    void set_trace_context(std::uint64_t trace_id, std::uint64_t span_id) {
        current_trace_id_ = trace_id;
        current_span_id_ = span_id;
    }

    // v3.0.0 B5: OpenTelemetry span export for distributed tracing.
    void set_otel_exporter(
        std::shared_ptr<v3::tracing::OtlpExporter> exporter);
    [[nodiscard]] std::shared_ptr<v3::tracing::OtlpExporter>
    get_otel_exporter() const;

    void shutdown();

private:
    struct BackendSlot {
        std::optional<BackendConfig> config;
        std::unique_ptr<v2::service::BackendConnection> connection;
        v2::service::CircuitBreaker breaker;
    };

    v2::service::BackendConnection* ensure_connection(v2::service::ServiceId service,
                                                       const std::string& shard_key = "");
    BackendSlot& slot_for(v2::service::ServiceId service);

    void record_route_result(v2::service::ServiceId target,
                             const BackendRoutingResult& result);

    BackendSlot login_slot_;
    BackendSlot room_slot_;
    BackendSlot battle_slot_;
    std::shared_ptr<BackendMetrics> metrics_;
    std::shared_ptr<v2::service::ServiceRegistry> registry_;
    std::shared_ptr<v3::cluster::ClusterRouter> cluster_router_;
    std::shared_ptr<v3::cluster::ShardRouter> shard_router_;
    std::shared_ptr<v3::tracing::OtlpExporter> otel_exporter_;
    mutable std::mutex mutex_;
    std::uint64_t current_trace_id_ = 0;
    std::uint64_t current_span_id_ = 0;
};

}  // namespace v2::gateway
