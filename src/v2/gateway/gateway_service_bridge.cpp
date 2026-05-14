#include "v2/gateway/gateway_service_bridge.h"

#include "v2/config/feature_flags.h"
#include "v2/service/service_id.h"
#include "v2/tracing/trace_context.h"
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/consistent_hash.h"

#include <chrono>
#include <mutex>

namespace v2::gateway {

namespace {

// v3.0.0 B5: RAII guard that auto-exports a span via OtlpExporter on scope exit.
struct SpanExportGuard {
    v2::tracing::Span span;
    v3::tracing::OtlpExporter* exporter = nullptr;
    std::string service_name;

    void mark_ok() { span.finish(); }

    ~SpanExportGuard() {
        span.finish();
        if (exporter) {
            exporter->export_span(span, service_name);
        }
    }

    SpanExportGuard(const SpanExportGuard&) = delete;
    SpanExportGuard& operator=(const SpanExportGuard&) = delete;
    SpanExportGuard(SpanExportGuard&&) = default;
    SpanExportGuard& operator=(SpanExportGuard&&) = default;
    SpanExportGuard() = default;
};

}  // namespace

namespace {

std::string service_name_for(v2::service::ServiceId service) {
    switch (service) {
        case v2::service::ServiceId::kLogin:       return "login";
        case v2::service::ServiceId::kRoom:        return "room";
        case v2::service::ServiceId::kBattle:      return "battle";
        case v2::service::ServiceId::kMatchmaking: return "match";
        case v2::service::ServiceId::kLeaderboard: return "leaderboard";
        default: return "login";
    }
}

v2::service::BackendConnectionOptions make_options(
    const GatewayServiceBridge::BackendConfig& config,
    const std::optional<v3::cluster::SecurityPolicy>& security_policy,
    v2::service::ServiceId service) {
    v2::service::BackendConnectionOptions opts{
        .host = config.host,
        .port = config.port,
    };
    if (security_policy.has_value() && security_policy->require_tls) {
        auto tls = security_policy->tls_config;
        // Apply per-service mTLS override if policy exists for this service.
        auto svc_name = service_name_for(service);
        if (auto* pol = security_policy->policy_for(svc_name)) {
            if (pol->mtls_required) {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kMutual;
            }
        }
        opts.tls_config = std::move(tls);
    }
    return opts;
}

}  // namespace

GatewayServiceBridge::GatewayServiceBridge(
    std::optional<BackendConfig> login_config,
    std::optional<BackendConfig> room_config,
    std::optional<BackendConfig> battle_config,
    std::optional<BackendConfig> matchmaking_config,
    std::optional<BackendConfig> leaderboard_config,
    std::shared_ptr<BackendMetrics> metrics)
    : metrics_(std::move(metrics)) {
    if (login_config) {
        login_slot_.config = std::move(*login_config);
    }
    if (room_config) {
        room_slot_.config = std::move(*room_config);
    }
    if (battle_config) {
        battle_slot_.config = std::move(*battle_config);
    }
    if (matchmaking_config) {
        matchmaking_slot_.config = std::move(*matchmaking_config);
    }
    if (leaderboard_config) {
        leaderboard_slot_.config = std::move(*leaderboard_config);
    }
}

GatewayServiceBridge::~GatewayServiceBridge() { shutdown(); }

GatewayServiceBridge::BackendSlot& GatewayServiceBridge::slot_for(
    v2::service::ServiceId service) {
    switch (service) {
        case v2::service::ServiceId::kLogin:
            return login_slot_;
        case v2::service::ServiceId::kRoom:
            return room_slot_;
        case v2::service::ServiceId::kBattle:
            return battle_slot_;
        case v2::service::ServiceId::kMatchmaking:
            return matchmaking_slot_;
        case v2::service::ServiceId::kLeaderboard:
            return leaderboard_slot_;
        default:
            return login_slot_;
    }
}

void GatewayServiceBridge::update_backend_config(
    v2::service::ServiceId service,
    std::optional<BackendConfig> config) {
    std::scoped_lock lock(mutex_);
    auto& slot = slot_for(service);
    if (config.has_value()) {
        slot.config = std::move(*config);
    } else {
        slot.config.reset();
    }
    if (slot.connection) {
        slot.connection->close();
        slot.connection.reset();
    }
    slot.breaker.reset();
}

namespace {

/// Build a temporary ConsistentHashRing from a set of instances and
/// look up which node owns the shard key. Returns node_name.
std::string hash_to_node(const std::vector<v3::cluster::ServiceInstance>& instances,
                         const std::string& shard_key,
                         std::uint32_t virtual_nodes) {
    if (instances.empty()) return {};
    if (instances.size() == 1) return instances[0].node.node_name;

    v3::cluster::ConsistentHashRing ring(
        v3::cluster::ConsistentHashRing::Config{.virtual_nodes = virtual_nodes});
    for (const auto& inst : instances) {
        ring.add_node(inst.node.node_name);
    }
    return ring.lookup(shard_key);
}

/// Find an instance by node_name in the discovered list.
const v3::cluster::ServiceInstance* find_by_node(
    const std::vector<v3::cluster::ServiceInstance>& instances,
    const std::string& node_name) {
    for (const auto& inst : instances) {
        if (inst.node.node_name == node_name) return &inst;
    }
    return nullptr;
}

}  // namespace

v2::service::BackendConnection* GatewayServiceBridge::ensure_connection(
    v2::service::ServiceId service,
    const std::string& shard_key) {
    // v3.1.0: If security policy requires TLS but feature flag is off, refuse.
    if (security_policy_.has_value() && feature_flags_) {
        auto svc_name = service_name_for(service);
        if (auto* pol = security_policy_->policy_for(svc_name)) {
            if (pol->tls_required &&
                !feature_flags_->is_enabled("v3_tls_enabled", svc_name)) {
                return nullptr;
            }
        }
    }

    // ── Cluster router path with optional consistent hashing ──────────
    if (cluster_router_) {
        const auto svc_name = service_name_for(service);

        // Resolve the target host:port — optionally via consistent hashing.
        std::optional<v3::cluster::NodeId> chosen_node;
        if (!shard_key.empty() && shard_router_) {
            auto all_healthy = cluster_router_->discover_all(svc_name);
            if (!all_healthy.empty()) {
                std::string node_name;
                if (service == v2::service::ServiceId::kRoom) {
                    node_name = shard_router_->route_room(shard_key);
                } else if (service == v2::service::ServiceId::kBattle) {
                    node_name = shard_router_->route_battle(shard_key);
                } else {
                    node_name = hash_to_node(all_healthy, shard_key, 150);
                }

                const auto* chosen = find_by_node(all_healthy, node_name);
                if (!chosen) {
                    node_name = hash_to_node(all_healthy, shard_key, 150);
                    chosen = find_by_node(all_healthy, node_name);
                }
                if (!chosen) chosen = &all_healthy[0];
                chosen_node = chosen->node;
            }
        } else {
            auto discovered = cluster_router_->discover(svc_name);
            if (discovered) chosen_node = discovered->node;
        }

        // Only use cluster-discovered address when the router had an entry;
        // otherwise fall through to the static BackendConfig below.
        if (chosen_node.has_value()) {
            std::scoped_lock lock(mutex_);
            auto& slot = slot_for(service);
            if (slot.connection && slot.connection->is_connected()) {
                if (registry_) {
                    registry_->heartbeat(service,
                        chosen_node->host, chosen_node->port);
                }
                return slot.connection.get();
            }

            BackendConfig cfg{chosen_node->host, chosen_node->port};
            slot.config = cfg;

            auto conn = std::make_unique<v2::service::BackendConnection>(
                make_options(cfg, security_policy_, service));
            if (!conn->connect()) {
                cluster_router_->mark_unhealthy(svc_name, *chosen_node);
                if (registry_) {
                    registry_->mark_unhealthy(service, cfg.host, cfg.port);
                }
                return nullptr;
            }

            slot.connection = std::move(conn);
            if (registry_) {
                registry_->heartbeat(service, cfg.host, cfg.port);
            }
            return slot.connection.get();
        }
    }

    // ── Fallback: static BackendConfig path ──────────────────────────
    std::optional<BackendConfig> cfg;
    v2::service::BackendConnection* existing = nullptr;
    {
        std::scoped_lock lock(mutex_);
        auto& slot = slot_for(service);
        if (!slot.config) return nullptr;
        if (slot.connection && slot.connection->is_connected()) {
            if (registry_) {
                registry_->heartbeat(service, slot.config->host, slot.config->port);
            }
            return slot.connection.get();
        }
        cfg = slot.config;
        existing = slot.connection.get();
    }
    (void)existing;

    auto conn = std::make_unique<v2::service::BackendConnection>(
        make_options(*cfg, security_policy_, service));
    if (!conn->connect()) {
        if (registry_) {
            registry_->mark_unhealthy(service, cfg->host, cfg->port);
        }
        return nullptr;
    }

    {
        std::scoped_lock lock(mutex_);
        auto& slot = slot_for(service);
        if (slot.connection) {
            slot.connection->close();
        }
        slot.connection = std::move(conn);
        if (registry_) {
            registry_->heartbeat(service, slot.config->host, slot.config->port);
        }
        return slot.connection.get();
    }
}

void GatewayServiceBridge::record_route_result(
    v2::service::ServiceId target,
    const BackendRoutingResult& result) {
    if (!metrics_) return;

    if (result.success) {
        metrics_->record_success(target);
    } else if (result.error == v2::service::ServiceErrorCode::kTimeout) {
        metrics_->record_timeout(target);
    } else if (result.error == v2::service::ServiceErrorCode::kUnavailable) {
        metrics_->record_unavailable(target);
    } else {
        metrics_->record_error(target);
    }
}

GatewayServiceBridge::BackendRoutingResult GatewayServiceBridge::route(
    v2::service::ServiceId target,
    const std::string& message_type,
    const std::string& payload,
    const std::string& shard_key) {
    BackendRoutingResult result;

    // v3.0.0 B5: Create span for distributed tracing, auto-export on return.
    SpanExportGuard span_guard;
    if (otel_exporter_) {
        if (current_trace_id_ != 0) {
            span_guard.span = v2::tracing::Span::from_trace(
                current_trace_id_, current_span_id_,
                "route." + message_type);
        } else {
            span_guard.span = v2::tracing::Span::root("route." + message_type);
        }
        span_guard.exporter = otel_exporter_.get();
        span_guard.service_name = v2::service::to_string(target);
        // Propagate span IDs into the backend request envelope.
        current_trace_id_ = span_guard.span.trace_id;
        current_span_id_ = span_guard.span.span_id;
    }

    if (metrics_) {
        metrics_->record_request(target);
    }

    auto& slot = slot_for(target);
    if (!slot.breaker.allow_request()) {
        result.error = v2::service::ServiceErrorCode::kCircuitOpen;
        record_route_result(target, result);
        if (metrics_) metrics_->record_degraded(target);
        return result;
    }

    auto* conn = ensure_connection(target, shard_key);
    if (!conn) {
        slot.breaker.on_failure();
        result.error = v2::service::ServiceErrorCode::kUnavailable;
        record_route_result(target, result);
        return result;
    }

    v2::service::BackendEnvelope request{
        .target_service = target,
        .kind = v2::service::MessageKind::kRequest,
        .payload = payload,
        .message_type = message_type,
        .trace_id = current_trace_id_,
        .span_id = current_span_id_,
    };

    const auto send_start = std::chrono::steady_clock::now();
    auto response = conn->send_request(request);
    const auto latency_us = static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - send_start)
            .count());

    if (!response) {
        slot.breaker.on_failure();
        result.error = v2::service::ServiceErrorCode::kTimeout;
        record_route_result(target, result);
        return result;
    }

    result.correlation_id = response->correlation_id;

    if (response->kind == v2::service::MessageKind::kError) {
        slot.breaker.on_failure();
        result.error = static_cast<v2::service::ServiceErrorCode>(response->error_code);
        record_route_result(target, result);
        return result;
    }

    slot.breaker.on_success();
    result.success = true;
    result.response_payload = std::move(response->payload);
    record_route_result(target, result);
    if (metrics_) {
        metrics_->record_latency(target, latency_us);
    }
    span_guard.mark_ok();
    return result;
}

void GatewayServiceBridge::set_service_registry(
    std::shared_ptr<v2::service::ServiceRegistry> registry) {
    registry_ = std::move(registry);
}

std::shared_ptr<BackendMetrics> GatewayServiceBridge::get_metrics() const {
    return metrics_;
}

std::shared_ptr<v2::service::ServiceRegistry> GatewayServiceBridge::get_registry() const {
    return registry_;
}

bool GatewayServiceBridge::is_backend_available(
    v2::service::ServiceId service) const {
    // Attempt lazy connect first
    auto* conn = const_cast<GatewayServiceBridge*>(this)->ensure_connection(service);
    return conn != nullptr;
}

void GatewayServiceBridge::set_cluster_router(
    std::shared_ptr<v3::cluster::ClusterRouter> router) {
    cluster_router_ = std::move(router);
}

std::shared_ptr<v3::cluster::ClusterRouter>
GatewayServiceBridge::get_cluster_router() const {
    return cluster_router_;
}

void GatewayServiceBridge::set_shard_router(
    std::shared_ptr<v3::cluster::ShardRouter> router) {
    shard_router_ = std::move(router);
}

std::shared_ptr<v3::cluster::ShardRouter>
GatewayServiceBridge::get_shard_router() const {
    return shard_router_;
}

void GatewayServiceBridge::set_otel_exporter(
    std::shared_ptr<v3::tracing::OtlpExporter> exporter) {
    otel_exporter_ = std::move(exporter);
}

std::shared_ptr<v3::tracing::OtlpExporter>
GatewayServiceBridge::get_otel_exporter() const {
    return otel_exporter_;
}

void GatewayServiceBridge::set_security_policy(
    v3::cluster::SecurityPolicy policy) {
    security_policy_ = std::move(policy);
}

const std::optional<v3::cluster::SecurityPolicy>&
GatewayServiceBridge::get_security_policy() const {
    return security_policy_;
}

void GatewayServiceBridge::set_feature_flags(
    std::shared_ptr<v2::config::FeatureFlags> flags) {
    feature_flags_ = std::move(flags);
}

std::shared_ptr<v2::config::FeatureFlags>
GatewayServiceBridge::get_feature_flags() const {
    return feature_flags_;
}

void GatewayServiceBridge::shutdown() {
    std::scoped_lock lock(mutex_);
    for (auto* slot : {&login_slot_, &room_slot_, &battle_slot_,
                       &matchmaking_slot_, &leaderboard_slot_}) {
        if (slot->connection) {
            slot->connection->close();
            slot->connection.reset();
        }
    }
}

}  // namespace v2::gateway
