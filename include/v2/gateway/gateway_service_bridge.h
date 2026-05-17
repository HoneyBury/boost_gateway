#pragma once

#include "v2/gateway/backend_metrics.h"
#include "v2/service/backend_connection.h"
#include "v2/service/circuit_breaker.h"
#include "v2/service/error_codes.h"
#include "v2/service/service_registry.h"
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/consistent_hash.h"
#include "v3/cluster/tls_config.h"
#include "v3/tracing/otel_exporter.h"

#include <cstdint>
#include <chrono>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace v2::config {
class FeatureFlags;
}  // namespace v2::config

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
        std::chrono::milliseconds timeout{5000};
        std::chrono::milliseconds connect_timeout{1000};
    };

    explicit GatewayServiceBridge(
        std::optional<BackendConfig> login_config = std::nullopt,
        std::optional<BackendConfig> room_config = std::nullopt,
        std::optional<BackendConfig> battle_config = std::nullopt,
        std::optional<BackendConfig> matchmaking_config = std::nullopt,
        std::optional<BackendConfig> leaderboard_config = std::nullopt,
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
    void configure_circuit_breaker(v2::service::ServiceId service,
                                   v2::service::CircuitBreakerOptions options);

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

    // v3.1.0: Security policy for TLS/mTLS inter-service communication.
    void set_security_policy(v3::cluster::SecurityPolicy policy);
    [[nodiscard]] const std::optional<v3::cluster::SecurityPolicy>&
    get_security_policy() const;

    // v3.1.0: Feature flags for gradual rollout of v3 features.
    void set_feature_flags(std::shared_ptr<v2::config::FeatureFlags> flags);
    [[nodiscard]] std::shared_ptr<v2::config::FeatureFlags>
    get_feature_flags() const;

    void shutdown();

private:
    struct ResolvedBackend {
        BackendConfig config;
        std::string connection_key;
        std::optional<v3::cluster::NodeId> node;
        bool from_cluster = false;
    };

    struct BackendSlot {
        std::optional<BackendConfig> config;
        std::unique_ptr<v2::service::BackendConnection> connection;
        std::vector<std::unique_ptr<v2::service::BackendConnection>> connection_pool;
        std::size_t next_connection_index = 0;
        std::unordered_map<std::string, std::unique_ptr<v2::service::BackendConnection>>
            cluster_connections;
        std::unordered_map<std::string, std::vector<std::unique_ptr<v2::service::BackendConnection>>>
            cluster_connection_pools;
        std::unordered_map<std::string, std::size_t> cluster_next_connection_index;
        v2::service::CircuitBreaker breaker;
    };

    v2::service::BackendConnection* ensure_connection(v2::service::ServiceId service,
                                                       const std::string& shard_key = "");
    [[nodiscard]] std::optional<ResolvedBackend> resolve_backend(
        v2::service::ServiceId service,
        const std::string& shard_key = "") const;
    BackendSlot& slot_for(v2::service::ServiceId service);

    void record_route_result(v2::service::ServiceId target,
                             const BackendRoutingResult& result);

    BackendSlot login_slot_;
    BackendSlot room_slot_;
    BackendSlot battle_slot_;
    BackendSlot matchmaking_slot_;
    BackendSlot leaderboard_slot_;
    std::shared_ptr<BackendMetrics> metrics_;
    std::shared_ptr<v2::service::ServiceRegistry> registry_;
    std::shared_ptr<v3::cluster::ClusterRouter> cluster_router_;
    std::shared_ptr<v3::cluster::ShardRouter> shard_router_;
    std::shared_ptr<v3::tracing::OtlpExporter> otel_exporter_;
    std::optional<v3::cluster::SecurityPolicy> security_policy_;
    std::shared_ptr<v2::config::FeatureFlags> feature_flags_;
    mutable std::mutex mutex_;
    std::uint64_t current_trace_id_ = 0;
    std::uint64_t current_span_id_ = 0;
};

}  // namespace v2::gateway
