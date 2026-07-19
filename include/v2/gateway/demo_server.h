#pragma once

#include "net/session.h"
#include "net/http_manager.h"
#include "v2/config/config_watcher.h"
#include "v2/config/feature_flags.h"
#include "v2/data/cached_data_store.h"
#include "v2/io/io_engine.h"
#include "v2/diagnostics/health_check.h"
#include "v2/gateway/backend_metrics.h"
#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"
#include "v2/service/service_registry.h"
#include "v2/service/service_registrar.h"
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/tls_config.h"

#include <condition_variable>
#include <atomic>
#include <cstdint>
#include <deque>
#include <optional>
#include <mutex>
#include <memory>
#include <thread>
#include <unordered_map>
#include <functional>
#include <vector>

namespace v2::gateway {

struct DemoServerOptions {
    std::optional<std::uint32_t> acceptor_core_id;
    std::optional<std::uint16_t> http_management_port;
    std::optional<std::uint32_t> max_connections;
    std::optional<GatewayServiceBridge::BackendConfig> login_backend_config;
    std::optional<GatewayServiceBridge::BackendConfig> room_backend_config;
    std::optional<GatewayServiceBridge::BackendConfig> battle_backend_config;
    std::optional<GatewayServiceBridge::BackendConfig> matchmaking_backend_config;
    std::optional<GatewayServiceBridge::BackendConfig> leaderboard_backend_config;
};

struct DemoServerIoCoreSnapshot {
    std::uint32_t core_id = 0;
    std::uint64_t active_sessions = 0;
    std::uint64_t accepted_sessions = 0;
    std::uint64_t outbound_dispatches = 0;
};

struct DemoServerDiagnostics {
    std::uint16_t local_port = 0;
    std::uint32_t io_core_count = 0;
    std::optional<std::uint32_t> acceptor_core_id;
    std::uint64_t total_active_sessions = 0;
    std::uint64_t total_accepted_sessions = 0;
    std::uint64_t total_outbound_dispatches = 0;
    std::vector<DemoServerIoCoreSnapshot> io_cores;
    Runtime::BattleRouteDiagnostics battle_route;
    std::optional<v3::tracing::OtlpExporter::Metrics> otel_exporter_metrics;
    std::unordered_map<std::string, BackendMetricsSnapshot> backend_metrics;
    std::vector<v2::service::ServiceInstance> backend_instances;
};

class DemoServer final : public DownstreamSessionWriteSink {
public:
    using SessionWriteTask = std::function<void()>;
    using SessionWriteScheduler = std::function<bool(SessionId, SessionWriteTask)>;

    DemoServer(std::uint16_t port,
               net::SessionOptions session_options = {},
               DemoServerOptions options = {},
               std::unique_ptr<v2::io::IoEngine> io_engine = std::make_unique<v2::io::AsioIoEngine>(1));
    ~DemoServer();

    void start();
    void stop();
    void deliver(SessionWrite write) override;
    void set_write_scheduler(SessionWriteScheduler scheduler);
    [[nodiscard]] std::uint16_t local_port() const;
    [[nodiscard]] std::uint32_t io_core_count() const;
    [[nodiscard]] std::optional<std::uint32_t> acceptor_core_id() const noexcept;
    [[nodiscard]] std::optional<std::uint32_t> session_io_core(SessionId session_id) const;
    [[nodiscard]] GatewayServiceBridge* service_bridge() const noexcept;
    [[nodiscard]] std::vector<DemoServerIoCoreSnapshot> io_core_snapshot() const;
    [[nodiscard]] DemoServerDiagnostics diagnostics() const;
    [[nodiscard]] std::string diagnostics_json() const;

    // Health check (v2.2.0: wiring HealthCheck to HTTP endpoints)
    [[nodiscard]] std::string health_json() const;
    [[nodiscard]] std::string ready_json() const;
    [[nodiscard]] net::HttpMetricsSnapshot metrics_snapshot() const;

private:
    struct GatewayQueueItem {
        SessionId session_id = 0;
        std::optional<v2::io::IoSession::PacketMessage> message;
    };

    void do_accept();
    void dispatch_write(SessionId session_id, SessionWriteTask task);
    void enqueue_packet(SessionId session_id, v2::io::IoSession::PacketMessage message);
    void enqueue_session_closed(SessionId session_id);
    void start_gateway_worker();
    void stop_gateway_worker();
    void load_gateway_config();
    void start_config_watcher();
    void reload_backend_configs();

    std::uint16_t port_ = 0;
    net::SessionOptions session_options_;
    DemoServerOptions options_;
    std::unique_ptr<v2::io::IoEngine> io_engine_;
    std::unique_ptr<v2::io::IoAcceptor> acceptor_;
    v2::runtime::ActorSystem actor_system_;
    SessionAdapter adapter_;
    Runtime runtime_;
    std::unique_ptr<v2::data::CachedBattleDataStore> archive_store_;
    std::shared_ptr<BackendMetrics> backend_metrics_;
    std::shared_ptr<v2::service::ServiceRegistry> service_registry_;
    v2::diagnostics::HealthCheck health_check_;
    v2::actor::ActorRef gateway_actor_;
    std::unordered_map<SessionId, std::shared_ptr<v2::io::IoSession>> sessions_;
    mutable std::mutex sessions_mutex_;
    mutable std::mutex io_core_snapshot_mutex_;
    std::unordered_map<SessionId, std::uint32_t> session_core_by_id_;
    std::unordered_map<std::uint32_t, DemoServerIoCoreSnapshot> io_core_snapshots_by_id_;
    mutable std::mutex scheduler_mutex_;
    SessionWriteScheduler write_scheduler_;
    SessionId next_session_id_ = 1;

    // Config hot-reload
    std::unique_ptr<v2::config::ConfigWatcher> config_watcher_;

    // HTTP management (optional, created when http_management_port is set)
    std::unique_ptr<boost::asio::io_context> management_io_;
    std::unique_ptr<net::HttpManager> http_manager_;
    std::unique_ptr<std::thread> management_thread_;

    // v3.1.0: Feature flags and security policy
    std::shared_ptr<v2::config::FeatureFlags> feature_flags_;
    std::optional<v3::cluster::SecurityPolicy> security_policy_;

    // v3.0.0: Cluster router integration — health check thread and registrars
    std::shared_ptr<v3::cluster::ClusterRouter> cluster_router_;
    std::unique_ptr<std::thread> health_check_thread_;
    std::atomic<bool> health_check_running_{false};
    std::mutex health_check_mutex_;
    std::condition_variable health_check_cv_;
    std::vector<std::shared_ptr<v2::service::ServiceRegistrar>> service_registrars_;
    std::mutex gateway_queue_mutex_;
    std::condition_variable gateway_queue_cv_;
    // Runtime and ActorSystem state are owned exclusively by gateway_worker_.
    // I/O callbacks enqueue both packets and close notifications here.
    std::deque<GatewayQueueItem> gateway_queue_;
    std::unique_ptr<std::thread> gateway_worker_;
    bool gateway_worker_stopping_ = false;
    std::mutex gateway_handle_mutex_;
    std::atomic<bool> stop_requested_{false};
};

}  // namespace v2::gateway
