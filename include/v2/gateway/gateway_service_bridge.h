#pragma once

#include "v2/gateway/backend_metrics.h"
#include "v2/service/backend_connection.h"
#include "v2/service/circuit_breaker.h"
#include "v2/service/error_codes.h"
#include "v2/service/service_registry.h"

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

    [[nodiscard]] BackendRoutingResult route(
        v2::service::ServiceId target,
        const std::string& message_type,
        const std::string& payload);

    [[nodiscard]] bool is_backend_available(v2::service::ServiceId service) const;

    void set_service_registry(
        std::shared_ptr<v2::service::ServiceRegistry> registry);
    [[nodiscard]] std::shared_ptr<BackendMetrics> get_metrics() const;
    [[nodiscard]] std::shared_ptr<v2::service::ServiceRegistry> get_registry() const;

    void update_backend_config(v2::service::ServiceId service,
                                std::optional<BackendConfig> config);

    void shutdown();

private:
    struct BackendSlot {
        std::optional<BackendConfig> config;
        std::unique_ptr<v2::service::BackendConnection> connection;
        v2::service::CircuitBreaker breaker;
    };

    v2::service::BackendConnection* ensure_connection(v2::service::ServiceId service);
    BackendSlot& slot_for(v2::service::ServiceId service);

    void record_route_result(v2::service::ServiceId target,
                             const BackendRoutingResult& result);

    BackendSlot login_slot_;
    BackendSlot room_slot_;
    BackendSlot battle_slot_;
    std::shared_ptr<BackendMetrics> metrics_;
    std::shared_ptr<v2::service::ServiceRegistry> registry_;
    mutable std::mutex mutex_;
};

}  // namespace v2::gateway
