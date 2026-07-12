#include "v2/gateway/gateway_service_bridge.h"

#include "app/audit_log.h"
#include "app/logging.h"
#include "v2/config/feature_flags.h"
#include "v2/service/service_id.h"
#include "v2/service/service_manifest.h"
#include "v2/tracing/trace_context.h"
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/consistent_hash.h"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <mutex>

namespace v2::gateway {

namespace {

std::size_t backend_connection_pool_size() {
    const char* raw = std::getenv("V2_BACKEND_CONNECTION_POOL_SIZE");
    if (raw == nullptr || raw[0] == '\0') {
        return 1;
    }
    const auto parsed = std::strtoul(raw, nullptr, 10);
    return parsed > 0 ? static_cast<std::size_t>(parsed) : 1U;
}

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
    v2::service::BackendConnectionOptions opts;
    opts.host = config.host;
    opts.port = config.port;
    opts.timeout = config.timeout;
    opts.connect_timeout = config.connect_timeout;
    if (security_policy.has_value() && security_policy->require_tls) {
        auto tls = security_policy->tls_config;
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

std::string connection_key_for(const v3::cluster::NodeId& node) {
    if (!node.node_name.empty()) {
        return node.node_name;
    }
    return node.host + ":" + std::to_string(node.port);
}

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

const v3::cluster::ServiceInstance* find_by_node(
    const std::vector<v3::cluster::ServiceInstance>& instances,
    const std::string& node_name) {
    for (const auto& inst : instances) {
        if (inst.node.node_name == node_name) return &inst;
    }
    return nullptr;
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

    // Log all known service manifests and validate configured backends
    const auto& manifests = v2::service::all_manifests();
    LOG_INFO("GatewayServiceBridge: loaded {} service manifests", manifests.size());
    for (const auto& m : manifests) {
        LOG_INFO("GatewayServiceBridge: manifest service={} desc=\"{}\"",
                 v2::service::to_string(m.service_id), m.description);
        AUDIT_LOG("service_manifest_loaded",
                  "service=" + std::string(v2::service::to_string(m.service_id)) +
                      " desc=" + m.description);
    }
    // Validate each configured backend against the known manifest list
    auto validate_backend = [&](const char* name,
                                v2::service::ServiceId sid,
                                const std::optional<BackendConfig>& cfg) {
        if (!cfg.has_value()) return;
        bool matched = false;
        for (const auto& m : manifests) {
            if (m.service_id == sid) {
                matched = true;
                LOG_INFO("GatewayServiceBridge: backend '{}' matches manifest: {}",
                         name, m.description);
                break;
            }
        }
        if (!matched) {
            LOG_INFO("GatewayServiceBridge: backend '{}' has no dedicated manifest",
                     name);
        }
    };
    validate_backend("login",     v2::service::ServiceId::kLogin,       login_config);
    validate_backend("room",      v2::service::ServiceId::kRoom,        room_config);
    validate_backend("battle",    v2::service::ServiceId::kBattle,      battle_config);
    validate_backend("match",     v2::service::ServiceId::kMatchmaking, matchmaking_config);
    validate_backend("leaderboard", v2::service::ServiceId::kLeaderboard, leaderboard_config);
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
    for (auto& conn : slot.connection_pool) {
        if (conn) {
            conn->close();
        }
    }
    slot.connection_pool.clear();
    slot.next_connection_index = 0;
    for (auto& [_, conn] : slot.cluster_connections) {
        if (conn) {
            conn->close();
        }
    }
    slot.cluster_connections.clear();
    for (auto& [_, pool] : slot.cluster_connection_pools) {
        for (auto& conn : pool) {
            if (conn) {
                conn->close();
            }
        }
    }
    slot.cluster_connection_pools.clear();
    slot.cluster_next_connection_index.clear();
    slot.breaker.reset();
}

void GatewayServiceBridge::configure_circuit_breaker(
    v2::service::ServiceId service,
    v2::service::CircuitBreakerOptions options) {
    std::scoped_lock lock(mutex_);
    slot_for(service).breaker.configure(options);
}

std::optional<GatewayServiceBridge::ResolvedBackend>
GatewayServiceBridge::resolve_backend(
    v2::service::ServiceId service,
    const std::string& shard_key) const {
    if (security_policy_.has_value() && feature_flags_) {
        const auto svc_name = service_name_for(service);
        if (const auto* pol = security_policy_->policy_for(svc_name)) {
            if (security_policy_->require_tls &&
                pol->tls_required &&
                !feature_flags_->is_enabled("v3_tls_enabled", svc_name)) {
                return std::nullopt;
            }
        }
    }

    if (cluster_router_) {
        const auto svc_name = service_name_for(service);
        std::optional<v3::cluster::NodeId> chosen_node;
        if (!shard_key.empty() && shard_router_) {
            auto healthy = cluster_router_->discover_all(svc_name);
            if (!healthy.empty()) {
                std::string node_name;
                if (service == v2::service::ServiceId::kRoom) {
                    node_name = shard_router_->route_room(shard_key);
                } else if (service == v2::service::ServiceId::kBattle) {
                    node_name = shard_router_->route_battle(shard_key);
                } else {
                    node_name = hash_to_node(healthy, shard_key, 150);
                }

                const auto* chosen = find_by_node(healthy, node_name);
                if (!chosen) {
                    node_name = hash_to_node(healthy, shard_key, 150);
                    chosen = find_by_node(healthy, node_name);
                }
                if (!chosen) {
                    chosen = &healthy.front();
                }
                chosen_node = chosen->node;
            }
        } else {
            auto discovered = cluster_router_->discover(svc_name);
            if (discovered) {
                chosen_node = discovered->node;
            }
        }

        if (chosen_node.has_value()) {
            LOG_INFO("GatewayServiceBridge: resolved {} via cluster router → {}:{}",
                     svc_name, chosen_node->host, chosen_node->port);

            // Cross-reference resolved backend against known service manifests
            const auto& manifests = v2::service::all_manifests();
            for (const auto& m : manifests) {
                if (m.service_id == service) {
                    AUDIT_LOG("service_discovery_resolved",
                              "service=" + svc_name +
                                  " node=" + chosen_node->node_name +
                                  " manifest_desc=" + m.description);
                    break;
                }
            }

            return ResolvedBackend{
                .config = BackendConfig{.host = chosen_node->host, .port = chosen_node->port},
                .connection_key = connection_key_for(*chosen_node),
                .node = chosen_node,
                .from_cluster = true,
            };
        }
        LOG_INFO("GatewayServiceBridge: cluster router returned no healthy instance for {}",
                 svc_name);
    }

    std::scoped_lock lock(mutex_);
    const auto& slot = const_cast<GatewayServiceBridge*>(this)->slot_for(service);
    if (!slot.config) {
        return std::nullopt;
    }
    LOG_WARN("GatewayServiceBridge: falling back to static config for {} → {}:{}",
             service_name_for(service), slot.config->host, slot.config->port);
    return ResolvedBackend{
        .config = *slot.config,
        .connection_key = "__static__",
        .node = std::nullopt,
        .from_cluster = false,
    };
}

v2::service::BackendConnection* GatewayServiceBridge::ensure_connection(
    v2::service::ServiceId service,
    const std::string& shard_key) {
    auto resolved = resolve_backend(service, shard_key);
    if (!resolved.has_value()) {
        return nullptr;
    }

    const auto& target = *resolved;
    const auto desired_pool_size = backend_connection_pool_size();
    {
        std::scoped_lock lock(mutex_);
        auto& slot = slot_for(service);
        v2::service::BackendConnection* busy_connection = nullptr;
        auto* existing = target.from_cluster
            ? [&]() -> v2::service::BackendConnection* {
                  auto& pool = slot.cluster_connection_pools[target.connection_key];
                  if (pool.empty()) {
                      return nullptr;
                  }
                  auto& next = slot.cluster_next_connection_index[target.connection_key];
                  for (std::size_t attempt = 0; attempt < pool.size(); ++attempt) {
                      const auto index = (next + attempt) % pool.size();
                      if (!pool[index]) {
                          continue;
                      }
                      const auto availability = pool[index]->availability();
                      if (availability == v2::service::BackendConnection::Availability::kBusy) {
                          if (busy_connection == nullptr) {
                              busy_connection = pool[index].get();
                          }
                          continue;
                      }
                      if (availability == v2::service::BackendConnection::Availability::kConnected) {
                          next = (index + 1) % pool.size();
                          return pool[index].get();
                      }
                  }
                  if (busy_connection != nullptr) {
                      next = (next + 1) % pool.size();
                  }
                  return nullptr;
              }()
            : [&]() -> v2::service::BackendConnection* {
                  if (slot.connection_pool.empty()) {
                      return nullptr;
                  }
                  for (std::size_t attempt = 0; attempt < slot.connection_pool.size(); ++attempt) {
                      const auto index = (slot.next_connection_index + attempt) % slot.connection_pool.size();
                      if (!slot.connection_pool[index]) {
                          continue;
                      }
                      const auto availability = slot.connection_pool[index]->availability();
                      if (availability == v2::service::BackendConnection::Availability::kBusy) {
                          if (busy_connection == nullptr) {
                              busy_connection = slot.connection_pool[index].get();
                          }
                          continue;
                      }
                      if (availability == v2::service::BackendConnection::Availability::kConnected) {
                          slot.next_connection_index = (index + 1) % slot.connection_pool.size();
                          return slot.connection_pool[index].get();
                      }
                  }
                  if (busy_connection != nullptr) {
                      slot.next_connection_index =
                          (slot.next_connection_index + 1) % slot.connection_pool.size();
                  }
                  return nullptr;
              }();
        const auto pool_size = target.from_cluster
            ? slot.cluster_connection_pools[target.connection_key].size()
            : slot.connection_pool.size();
        const auto has_disconnected_connection = target.from_cluster
            ? [&]() {
                  const auto& pool = slot.cluster_connection_pools[target.connection_key];
                  return std::any_of(pool.begin(), pool.end(), [](const auto& conn) {
                      return !conn || conn->availability() ==
                                          v2::service::BackendConnection::Availability::kDisconnected;
                  });
              }()
            : std::any_of(slot.connection_pool.begin(),
                          slot.connection_pool.end(),
                          [](const auto& conn) {
                              return !conn || conn->availability() ==
                                                  v2::service::BackendConnection::Availability::kDisconnected;
                          });
        // A non-empty pool used to return its first healthy connection here,
        // which prevented it from ever reaching its configured size. Grow the
        // pool before reusing connections so concurrent battle routes do not
        // serialize behind one BackendConnection mutex.
        if (existing && pool_size >= desired_pool_size && !has_disconnected_connection) {
            if (registry_) {
                registry_->heartbeat(service, target.config.host, target.config.port);
            }
            return existing;
        }
        // All pooled connections may be busy with their synchronous request. Do not
        // block while probing each socket or replace a healthy busy connection; the
        // caller will wait only on this selected connection's request mutex.
        if (busy_connection && pool_size >= desired_pool_size &&
            !has_disconnected_connection) {
            if (registry_) {
                registry_->heartbeat(service, target.config.host, target.config.port);
            }
            return busy_connection;
        }
    }

    auto conn = std::make_unique<v2::service::BackendConnection>(
        make_options(target.config, security_policy_, service));
    if (!conn->connect()) {
        if (target.from_cluster && cluster_router_ && target.node.has_value()) {
            cluster_router_->mark_unhealthy(service_name_for(service), *target.node);
        }
        if (registry_) {
            registry_->mark_unhealthy(service, target.config.host, target.config.port);
        }
        return nullptr;
    }

    std::scoped_lock lock(mutex_);
    auto& slot = slot_for(service);
    v2::service::BackendConnection* stored = nullptr;
    if (target.from_cluster) {
        auto& pool = slot.cluster_connection_pools[target.connection_key];
        auto& next = slot.cluster_next_connection_index[target.connection_key];
        if (pool.size() < desired_pool_size) {
            pool.push_back(std::move(conn));
            stored = pool.back().get();
            next = pool.empty() ? 0 : next % pool.size();
        } else {
            for (std::size_t attempt = 0; attempt < pool.size(); ++attempt) {
                const auto index = (next + attempt) % pool.size();
                if (!pool[index] || !pool[index]->is_connected()) {
                    if (pool[index]) {
                        pool[index]->close();
                    }
                    pool[index] = std::move(conn);
                    stored = pool[index].get();
                    next = (index + 1) % pool.size();
                    break;
                }
            }
            if (stored == nullptr) {
                for (std::size_t attempt = 0; attempt < pool.size(); ++attempt) {
                    const auto index = (next + attempt) % pool.size();
                    if (pool[index] && pool[index]->is_connected()) {
                        next = (index + 1) % pool.size();
                        stored = pool[index].get();
                        break;
                    }
                }
            }
            if (stored == nullptr) {
                auto& entry = pool[next % pool.size()];
                if (entry) {
                    entry->close();
                }
                entry = std::move(conn);
                stored = entry.get();
                next = (next + 1) % pool.size();
            }
        }
    } else {
        if (slot.connection_pool.size() < desired_pool_size) {
            slot.connection_pool.push_back(std::move(conn));
            stored = slot.connection_pool.back().get();
            slot.next_connection_index %= slot.connection_pool.size();
        } else {
            for (std::size_t attempt = 0; attempt < slot.connection_pool.size(); ++attempt) {
                const auto index = (slot.next_connection_index + attempt) %
                                   slot.connection_pool.size();
                if (!slot.connection_pool[index] ||
                    !slot.connection_pool[index]->is_connected()) {
                    if (slot.connection_pool[index]) {
                        slot.connection_pool[index]->close();
                    }
                    slot.connection_pool[index] = std::move(conn);
                    stored = slot.connection_pool[index].get();
                    slot.next_connection_index =
                        (index + 1) % slot.connection_pool.size();
                    break;
                }
            }
            if (stored == nullptr) {
                for (std::size_t attempt = 0; attempt < slot.connection_pool.size(); ++attempt) {
                    const auto index = (slot.next_connection_index + attempt) %
                                       slot.connection_pool.size();
                    if (slot.connection_pool[index] && slot.connection_pool[index]->is_connected()) {
                        slot.next_connection_index =
                            (index + 1) % slot.connection_pool.size();
                        stored = slot.connection_pool[index].get();
                        break;
                    }
                }
            }
            if (stored == nullptr) {
                auto& entry = slot.connection_pool[
                    slot.next_connection_index % slot.connection_pool.size()];
                if (entry) {
                    entry->close();
                }
                entry = std::move(conn);
                stored = entry.get();
                slot.next_connection_index =
                    (slot.next_connection_index + 1) % slot.connection_pool.size();
            }
        }
    }
    if (registry_) {
        registry_->heartbeat(service, target.config.host, target.config.port);
    }
    return stored;
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
    if (!response) {
        conn->close();
        conn = ensure_connection(target, shard_key);
        if (conn) {
            response = conn->send_request(request);
        }
    }
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
        result.error = static_cast<v2::service::ServiceErrorCode>(response->error_code);
        result.response_payload = std::move(response->payload);
        if (result.error == v2::service::ServiceErrorCode::kRejected) {
            slot.breaker.on_success();
        } else {
            slot.breaker.on_failure();
        }
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
        for (auto& conn : slot->connection_pool) {
            if (conn) {
                conn->close();
            }
        }
        slot->connection_pool.clear();
        slot->next_connection_index = 0;
        for (auto& [_, conn] : slot->cluster_connections) {
            if (conn) {
                conn->close();
            }
        }
        slot->cluster_connections.clear();
        for (auto& [_, pool] : slot->cluster_connection_pools) {
            for (auto& conn : pool) {
                if (conn) {
                    conn->close();
                }
            }
        }
        slot->cluster_connection_pools.clear();
        slot->cluster_next_connection_index.clear();
    }
}

}  // namespace v2::gateway
