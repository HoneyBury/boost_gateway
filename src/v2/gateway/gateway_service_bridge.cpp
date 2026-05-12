#include "v2/gateway/gateway_service_bridge.h"

#include <chrono>
#include <mutex>

namespace v2::gateway {

namespace {

v2::service::BackendConnectionOptions make_options(
    const GatewayServiceBridge::BackendConfig& config) {
    return v2::service::BackendConnectionOptions{
        .host = config.host,
        .port = config.port,
    };
}

}  // namespace

GatewayServiceBridge::GatewayServiceBridge(
    std::optional<BackendConfig> login_config,
    std::optional<BackendConfig> room_config,
    std::optional<BackendConfig> battle_config,
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

v2::service::BackendConnection* GatewayServiceBridge::ensure_connection(
    v2::service::ServiceId service) {
    // Read config under lock; exit early if no config or healthy connection exists.
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
    // IO outside lock
    (void)existing;

    auto conn = std::make_unique<v2::service::BackendConnection>(
        make_options(*cfg));
    if (!conn->connect()) {
        if (registry_) {
            registry_->mark_unhealthy(service, cfg->host, cfg->port);
        }
        return nullptr;
    }

    // Install under lock
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
    const std::string& payload) {
    BackendRoutingResult result;

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

    auto* conn = ensure_connection(target);
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

void GatewayServiceBridge::shutdown() {
    std::scoped_lock lock(mutex_);
    for (auto* slot : {&login_slot_, &room_slot_, &battle_slot_}) {
        if (slot->connection) {
            slot->connection->close();
            slot->connection.reset();
        }
    }
}

}  // namespace v2::gateway
