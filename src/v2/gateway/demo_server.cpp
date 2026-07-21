#include "v2/gateway/demo_server.h"

#include "app/logging.h"
#include "net/protocol.h"
#include "v3/cluster/cluster_router.h"
#include "v3/tracing/otel_exporter.h"

#include <boost/asio.hpp>

#include <nlohmann/json.hpp>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <limits>
#include <string_view>
#include <utility>
#include <vector>

namespace v2::gateway {
namespace {

std::filesystem::path gateway_config_path() {
    const char* raw = std::getenv("CONFIG_PATH");
    if (raw != nullptr && raw[0] != '\0') {
        return std::filesystem::path(raw);
    }
    raw = std::getenv("GATEWAY_CONFIG_PATH");
    if (raw != nullptr && raw[0] != '\0') {
        return std::filesystem::path(raw);
    }
    return std::filesystem::path("config/gateway.json");
}

bool is_push_message(std::uint16_t message_id) {
    switch (message_id) {
        case net::protocol::kSessionKickedPush:
        case net::protocol::kSessionResumedPush:
        case net::protocol::kRoomStatePush:
        case net::protocol::kBattleInputPush:
        case net::protocol::kBattleStatePush:
        case net::protocol::kMatchFoundPush:
            return true;
        default:
            return false;
    }
}

}  // namespace

DemoServer::DemoServer(std::uint16_t port,
                       net::SessionOptions session_options,
                       DemoServerOptions options,
                       std::unique_ptr<v2::io::IoEngine> io_engine)
    : port_(port),
      session_options_(std::move(session_options)),
      options_(std::move(options)),
      io_engine_(std::move(io_engine)),
      adapter_(actor_system_, this),
      runtime_(actor_system_, adapter_) {
    runtime_.set_battle_route_completion_dispatcher(
        [this](std::function<void()> task) {
            return enqueue_runtime_task(std::move(task));
        });
    set_write_scheduler([this](SessionId session_id, SessionWriteTask task) {
        std::shared_ptr<v2::io::IoSession> session;
        {
            std::scoped_lock lock(sessions_mutex_);
            const auto it = sessions_.find(session_id);
            if (it == sessions_.end()) {
                return false;
            }
            session = it->second;
        }
        if (io_engine_ == nullptr || !session || !task) {
            return false;
        }
        io_engine_->dispatch_to_core(session->owning_core_id(), std::move(task));
        return true;
    });
    gateway_actor_ = runtime_.create_gateway_actor();
    adapter_.bind_gateway(gateway_actor_);

    backend_metrics_ = std::make_shared<BackendMetrics>();
    service_registry_ = std::make_shared<v2::service::ServiceRegistry>();

    health_check_.set_backend_metrics(backend_metrics_);
    health_check_.set_service_registry(service_registry_);

    runtime_.set_backend_metrics_for_diagnostics(backend_metrics_);
    runtime_.set_service_registry_for_diagnostics(service_registry_);

    if (options_.login_backend_config.has_value() ||
        options_.room_backend_config.has_value() ||
        options_.battle_backend_config.has_value() ||
        options_.matchmaking_backend_config.has_value() ||
        options_.leaderboard_backend_config.has_value()) {
        auto bridge = std::make_unique<GatewayServiceBridge>(
            options_.login_backend_config,
            options_.room_backend_config,
            options_.battle_backend_config,
            options_.matchmaking_backend_config,
            options_.leaderboard_backend_config,
            backend_metrics_);
        bridge->set_service_registry(service_registry_);
        runtime_.set_service_bridge(std::move(bridge));

        // P1a: Wire ClusterRouter for dynamic service discovery.
        // Instances are registered in load_gateway_config() as backends
        // are loaded, so the router is seeded with real host:port pairs.
        runtime_.service_bridge()->set_cluster_router(
            std::make_shared<v3::cluster::ClusterRouter>());

        // P1b: Wire OtlpExporter for distributed tracing (env-opt-in)
        const char* otel_endpoint = std::getenv("OTEL_EXPORT_ENDPOINT");
        if (otel_endpoint && otel_endpoint[0] != '\0') {
            v3::tracing::OtlpExporter::Config otel_cfg;
            otel_cfg.service_name = "boost-gateway";
            otel_cfg.export_endpoint = otel_endpoint;
            runtime_.service_bridge()->set_otel_exporter(
                std::make_shared<v3::tracing::OtlpExporter>(std::move(otel_cfg)));
            LOG_INFO("DemoServer: OTLP export enabled → {}", otel_endpoint);
        }

        load_gateway_config();

        // P1c: Seed cluster_router_ member and set up health checks
        cluster_router_ = runtime_.service_bridge()->get_cluster_router();
        if (cluster_router_) {
            cluster_router_->set_health_check(
                [](const v3::cluster::NodeId& node) -> bool {
                    try {
                        boost::asio::io_context io;
                        boost::asio::ip::tcp::socket sock(io);
                        boost::asio::ip::tcp::endpoint ep(
                            boost::asio::ip::make_address(node.host),
                            static_cast<std::uint16_t>(node.port));
                        boost::system::error_code ec;
                        sock.connect(ep, ec);
                        sock.close();
                        return !ec;
                    } catch (...) {
                        return false;
                    }
                });

            auto register_backend = [&](const std::string& svc_name,
                                        std::optional<GatewayServiceBridge::BackendConfig>& cfg) {
                if (!cfg.has_value()) return;
                auto registrar = std::make_shared<v2::service::ServiceRegistrar>(
                    *cluster_router_, svc_name, cfg->host, cfg->port);
                registrar->start();
                service_registrars_.push_back(std::move(registrar));
            };

            register_backend("login", options_.login_backend_config);
            register_backend("room", options_.room_backend_config);
            register_backend("battle", options_.battle_backend_config);
            register_backend("match", options_.matchmaking_backend_config);
            register_backend("leaderboard", options_.leaderboard_backend_config);

            health_check_running_ = true;
            health_check_thread_ = std::make_unique<std::thread>([this]() {
                while (health_check_running_) {
                    std::unique_lock<std::mutex> lock(health_check_mutex_);
                    const bool stop_requested = health_check_cv_.wait_for(
                        lock,
                        std::chrono::seconds(5),
                        [this]() { return !health_check_running_.load(); });
                    lock.unlock();
                    if (stop_requested || !health_check_running_) {
                        break;
                    }
                    cluster_router_->run_health_checks();
                }
            });
        }
    }

    auto archive_delegate = std::make_shared<JsonFileBattleDataStore>("v2_archive");
    archive_store_ = std::make_unique<v2::data::CachedBattleDataStore>(
        std::move(archive_delegate), 1000);
    runtime_.set_archive_sink(archive_store_.get());
}

DemoServer::~DemoServer() {
    stop();
}

void DemoServer::start() {
    stop_requested_.store(false, std::memory_order_release);
    start_gateway_worker();
    if (options_.http_management_port.has_value()) {
        management_io_ = std::make_unique<boost::asio::io_context>();
        http_manager_ = std::make_unique<net::HttpManager>(
            management_io_->get_executor(), *options_.http_management_port);
        http_manager_->set_health_provider([this]() { return health_json(); });
        http_manager_->set_ready_provider([this]() { return ready_json(); });
        http_manager_->set_metrics_provider([this]() { return metrics_snapshot(); });
        // v3.4.0: Audit log query endpoint
        http_manager_->register_route("/audit-log", [this](const net::HttpRequest&) -> net::HttpResponse {
            std::ifstream file("logs/audit.log");
            if (!file) return net::HttpResponse{404, "{\"error\":\"audit_log_not_found\"}", "text/plain"};
            std::vector<std::string> lines;
            std::string line;
            while (std::getline(file, line)) {
                lines.push_back(line);
                if (lines.size() > 200) lines.erase(lines.begin());
            }
            std::string result = "[";
            for (size_t i = 0; i < lines.size(); ++i) {
                if (i > 0) result += ",";
                result += lines[i];
            }
            result += "]";
            return net::HttpResponse{200, result, "application/json"};
        });
        http_manager_->start();
        management_thread_ = std::make_unique<std::thread>([this]() { management_io_->run(); });
        LOG_INFO("v2 demo server HTTP management listening on :{}", *options_.http_management_port);
    }
    v2::io::IoListenOptions listen_options{
        .fixed_core_id = options_.acceptor_core_id,
        .accept_policy = options_.acceptor_core_id.has_value()
            ? v2::io::AcceptPolicy::kFixed
            : v2::io::AcceptPolicy::kRoundRobin,
        .reuse_port = io_engine_->num_io_cores() > 1 && !options_.acceptor_core_id.has_value(),
    };
    acceptor_ = io_engine_->listen("0.0.0.0", port_, session_options_, listen_options);
    LOG_INFO("v2 demo server listening on 0.0.0.0:{}", acceptor_->local_port());
    do_accept();
    start_config_watcher();
    io_engine_->run();
}

void DemoServer::stop() {
    if (stop_requested_.exchange(true, std::memory_order_acq_rel)) {
        return;
    }

    if (config_watcher_) {
        config_watcher_->stop();
        config_watcher_.reset();
    }

    // Stop health check thread and service registrars first
    health_check_running_ = false;
    health_check_cv_.notify_all();
    if (health_check_thread_ && health_check_thread_->joinable()) {
        health_check_thread_->join();
    }
    health_check_thread_.reset();
    for (auto& registrar : service_registrars_) {
        registrar->stop();
    }
    service_registrars_.clear();

    // Stop HTTP management before tearing down sessions
    if (http_manager_) {
        http_manager_->stop();
    }
    if (management_io_) {
        management_io_->stop();
    }
    if (management_thread_ && management_thread_->joinable()) {
        management_thread_->join();
    }
    management_thread_.reset();
    http_manager_.reset();
    management_io_.reset();

    std::vector<std::shared_ptr<v2::io::IoSession>> sessions_to_close;
    {
        std::scoped_lock lock(sessions_mutex_);
        if (acceptor_) {
            acceptor_->close();
        }
        for (auto& [session_id, session] : sessions_) {
            (void)session_id;
            sessions_to_close.push_back(session);
        }
        sessions_.clear();
        session_core_by_id_.clear();
    }
    for (auto& session : sessions_to_close) {
        session->close();
    }
    {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        for (auto& [core_id, snapshot] : io_core_snapshots_by_id_) {
            (void)core_id;
            snapshot.active_sessions = 0;
        }
    }
    runtime_.shutdown_battle_route_workers();
    stop_gateway_worker();
    io_engine_->stop();
    acceptor_.reset();
}

void DemoServer::set_write_scheduler(SessionWriteScheduler scheduler) {
    std::scoped_lock lock(scheduler_mutex_);
    write_scheduler_ = std::move(scheduler);
}

void DemoServer::dispatch_write(SessionId session_id, SessionWriteTask task) {
    if (!task) {
        return;
    }

    SessionWriteScheduler scheduler;
    {
        std::scoped_lock lock(scheduler_mutex_);
        scheduler = write_scheduler_;
    }

    if (scheduler && scheduler(session_id, task)) {
        const auto core_id = session_io_core(session_id);
        if (core_id.has_value()) {
            std::scoped_lock lock(io_core_snapshot_mutex_);
            auto& snapshot = io_core_snapshots_by_id_[*core_id];
            snapshot.core_id = *core_id;
            ++snapshot.outbound_dispatches;
        }
        return;
    }

    task();
}

void DemoServer::deliver(SessionWrite write) {
    std::shared_ptr<v2::io::IoSession> session;
    {
        std::scoped_lock lock(sessions_mutex_);
        const auto it = sessions_.find(write.envelope.session_id);
        if (it == sessions_.end()) {
            return;
        }
        session = it->second;
    }

    dispatch_write(
        write.envelope.session_id,
        [session, envelope = std::move(write.envelope)]() mutable {
            const bool high_priority = !is_push_message(envelope.protocol_message_id);
            session->send(envelope.protocol_message_id,
                          envelope.request_id,
                          envelope.error_code,
                          std::move(envelope.body),
                          envelope.flags,
                          high_priority);
        });
}

std::uint16_t DemoServer::local_port() const {
    return acceptor_ == nullptr ? 0 : acceptor_->local_port();
}

std::uint32_t DemoServer::io_core_count() const {
    return io_engine_ == nullptr ? 0U : io_engine_->num_io_cores();
}

std::optional<std::uint32_t> DemoServer::acceptor_core_id() const noexcept {
    return acceptor_ == nullptr ? std::nullopt : std::optional<std::uint32_t>(acceptor_->owning_core_id());
}

std::optional<std::uint32_t> DemoServer::session_io_core(SessionId session_id) const {
    std::scoped_lock lock(sessions_mutex_);
    const auto it = session_core_by_id_.find(session_id);
    if (it == session_core_by_id_.end()) {
        return std::nullopt;
    }
    return it->second;
}

GatewayServiceBridge* DemoServer::service_bridge() const noexcept {
    return runtime_.service_bridge();
}

std::vector<DemoServerIoCoreSnapshot> DemoServer::io_core_snapshot() const {
    std::vector<DemoServerIoCoreSnapshot> snapshots;
    const auto core_count = io_core_count();
    snapshots.reserve(core_count);
    for (std::uint32_t core_id = 0; core_id < core_count; ++core_id) {
        snapshots.push_back(DemoServerIoCoreSnapshot{
            .core_id = core_id,
            .active_sessions = 0,
            .accepted_sessions = 0,
            .outbound_dispatches = 0,
        });
    }

    std::scoped_lock lock(io_core_snapshot_mutex_);
    for (const auto& [core_id, snapshot] : io_core_snapshots_by_id_) {
        if (core_id < snapshots.size()) {
            snapshots[core_id] = snapshot;
        } else {
            snapshots.push_back(snapshot);
        }
    }
    return snapshots;
}

DemoServerDiagnostics DemoServer::diagnostics() const {
    DemoServerDiagnostics result;
    result.local_port = local_port();
    result.io_core_count = io_core_count();
    result.acceptor_core_id = acceptor_core_id();
    result.io_cores = io_core_snapshot();
    for (const auto& snapshot : result.io_cores) {
        result.total_active_sessions += snapshot.active_sessions;
        result.total_accepted_sessions += snapshot.accepted_sessions;
        result.total_outbound_dispatches += snapshot.outbound_dispatches;
    }
    result.battle_route = runtime_.battle_route_diagnostics();
    if (const auto* bridge = runtime_.service_bridge()) {
        result.otel_exporter_metrics = bridge->otel_exporter_metrics();
    }

    if (backend_metrics_) {
        auto all = backend_metrics_->all_snapshots();
        for (const auto& [service, snap] : all) {
            result.backend_metrics[service_id_to_key(service)] = snap;
        }
    }

    if (service_registry_) {
        result.backend_instances = service_registry_->all_instances();
    }

    return result;
}

std::string DemoServer::diagnostics_json() const {
    const auto snapshot = diagnostics();
    nlohmann::json doc;
    doc["local_port"] = snapshot.local_port;
    doc["io_core_count"] = snapshot.io_core_count;
    doc["acceptor_core_id"] = snapshot.acceptor_core_id.has_value()
                                  ? nlohmann::json(*snapshot.acceptor_core_id)
                                  : nlohmann::json(nullptr);
    doc["total_active_sessions"] = snapshot.total_active_sessions;
    doc["total_accepted_sessions"] = snapshot.total_accepted_sessions;
    doc["total_outbound_dispatches"] = snapshot.total_outbound_dispatches;
    const auto completed_battle_routes = snapshot.battle_route.completed_tasks;
    doc["battle_route"] = {
        {"completed_tasks", completed_battle_routes},
        {"queued_tasks", snapshot.battle_route.queued_tasks},
        {"rejected_tasks", snapshot.battle_route.rejected_tasks},
        {"dropped_completions", snapshot.battle_route.dropped_completions},
        {"queue_capacity", snapshot.battle_route.queue_capacity},
        {"average_queue_wait_us", completed_battle_routes == 0
                                        ? 0
                                        : snapshot.battle_route.total_queue_wait_us /
                                              completed_battle_routes},
        {"max_queue_wait_us", snapshot.battle_route.max_queue_wait_us},
        {"average_task_execution_us", completed_battle_routes == 0
                                            ? 0
                                            : snapshot.battle_route.total_task_execution_us /
                                                  completed_battle_routes},
        {"max_task_execution_us", snapshot.battle_route.max_task_execution_us},
        {"average_backend_route_us", completed_battle_routes == 0
                                         ? 0
                                         : snapshot.battle_route.total_backend_route_us /
                                               completed_battle_routes},
        {"max_backend_route_us", snapshot.battle_route.max_backend_route_us},
        {"average_response_dispatch_us", completed_battle_routes == 0
                                              ? 0
                                              : snapshot.battle_route.total_response_dispatch_us /
                                                    completed_battle_routes},
        {"max_response_dispatch_us", snapshot.battle_route.max_response_dispatch_us},
    };
    const auto otel_metrics =
        snapshot.otel_exporter_metrics.value_or(v3::tracing::OtlpExporter::Metrics{});
    doc["otel_exporter_metrics"] = {
        {"configured", snapshot.otel_exporter_metrics.has_value()},
        {"enqueued_spans", otel_metrics.enqueued_spans},
        {"exported_spans", otel_metrics.exported_spans},
        {"successful_batches", otel_metrics.successful_batches},
        {"failed_batches", otel_metrics.failed_batches},
        {"buffered_spans", otel_metrics.buffered_spans},
    };

    nlohmann::json io_cores = nlohmann::json::array();
    for (const auto& core : snapshot.io_cores) {
        io_cores.push_back({
            {"core_id", core.core_id},
            {"active_sessions", core.active_sessions},
            {"accepted_sessions", core.accepted_sessions},
            {"outbound_dispatches", core.outbound_dispatches},
        });
    }
    doc["io_cores"] = std::move(io_cores);

    nlohmann::json backend_metrics = nlohmann::json::object();
    for (const auto& [service_key, snap] : snapshot.backend_metrics) {
        const auto avg_latency_us =
            snap.latency_sample_count > 0 ? (snap.total_latency_us / snap.latency_sample_count) : 0;
        nlohmann::json latency_buckets = nlohmann::json::array();
        for (std::size_t i = 0; i < snap.latency_bucket_counts.size(); ++i) {
            const auto upper_bound = kBackendLatencyBucketUpperBoundsUs[i];
            latency_buckets.push_back({
                {"le_us", upper_bound == std::numeric_limits<std::uint64_t>::max()
                              ? nlohmann::json("+Inf")
                              : nlohmann::json(upper_bound)},
                {"count", snap.latency_bucket_counts[i]},
            });
        }
        backend_metrics[service_key] = {
            {"total_requests", snap.total_requests},
            {"total_successes", snap.total_successes},
            {"total_timeouts", snap.total_timeouts},
            {"total_unavailable", snap.total_unavailable},
            {"total_errors", snap.total_errors},
            {"total_degraded", snap.total_degraded},
            {"total_latency_us", snap.total_latency_us},
            {"latency_sample_count", snap.latency_sample_count},
            {"avg_latency_us", avg_latency_us},
            {"p50_latency_us", backend_latency_percentile_us(snap, 0.50)},
            {"p90_latency_us", backend_latency_percentile_us(snap, 0.90)},
            {"p99_latency_us", backend_latency_percentile_us(snap, 0.99)},
            {"latency_buckets", std::move(latency_buckets)},
        };
    }
    doc["backend_metrics"] = std::move(backend_metrics);

    nlohmann::json backend_instances = nlohmann::json::array();
    for (const auto& inst : snapshot.backend_instances) {
        backend_instances.push_back({
            {"service_id", service_id_to_key(inst.service_id)},
            {"host", inst.host},
            {"port", inst.port},
            {"healthy", inst.healthy},
        });
    }
    doc["backend_instances"] = std::move(backend_instances);

    return doc.dump();
}

std::string DemoServer::health_json() const {
    auto status = health_check_.check();
    return v2::diagnostics::HealthCheck::to_json(status);
}

std::string DemoServer::ready_json() const {
    auto status = health_check_.check();
    nlohmann::json doc;
    bool ready = status.is_healthy();
    // Include backend reachability details
    nlohmann::json checks = nlohmann::json::array();
    for (const auto& check : status.checks) {
        checks.push_back({
            {"name", check.name},
            {"status", check.status},
            {"message", check.message},
        });
    }

    if (const auto* bridge = runtime_.service_bridge()) {
        const struct {
            std::optional<GatewayServiceBridge::BackendConfig> DemoServerOptions::*config;
            v2::service::ServiceId service_id;
            const char* service_name;
        } kConfiguredServices[] = {
            {&DemoServerOptions::login_backend_config, v2::service::ServiceId::kLogin, "login"},
            {&DemoServerOptions::room_backend_config, v2::service::ServiceId::kRoom, "room"},
            {&DemoServerOptions::battle_backend_config, v2::service::ServiceId::kBattle, "battle"},
            {&DemoServerOptions::matchmaking_backend_config, v2::service::ServiceId::kMatchmaking, "matchmaking"},
            {&DemoServerOptions::leaderboard_backend_config, v2::service::ServiceId::kLeaderboard, "leaderboard"},
        };

        for (const auto& configured : kConfiguredServices) {
            if (!(options_.*(configured.config)).has_value()) {
                continue;
            }
            const bool available = bridge->is_backend_available(configured.service_id);
            if (!available) {
                ready = false;
            }
            checks.push_back({
                {"name", std::string("bridge:") + configured.service_name},
                {"status", available ? "pass" : "fail"},
                {"message", available ? "backend reachable" : "backend unavailable"},
            });
        }
    }

    doc["status"] = ready ? status.status : "fail";
    doc["ready"] = ready;
    doc["checks"] = std::move(checks);
    return doc.dump();
}

net::HttpMetricsSnapshot DemoServer::metrics_snapshot() const {
    const auto diag = diagnostics();
    net::HttpMetricsSnapshot snap;

    // Build Prometheus text format
    std::string prom;
    auto add_counter = [&](const char* name, const char* help, uint64_t val) {
        prom += "# HELP " + std::string(name) + " " + std::string(help) + "\n";
        prom += "# TYPE " + std::string(name) + " counter\n";
        prom += std::string(name) + " " + std::to_string(val) + "\n";
    };
    auto add_gauge = [&](const char* name, const char* help, uint64_t val) {
        prom += "# HELP " + std::string(name) + " " + std::string(help) + "\n";
        prom += "# TYPE " + std::string(name) + " gauge\n";
        prom += std::string(name) + " " + std::to_string(val) + "\n";
    };

    add_gauge("gateway_active_sessions", "Active sessions", diag.total_active_sessions);
    add_counter("gateway_accepted_sessions_total", "Total accepted sessions", diag.total_accepted_sessions);
    add_counter("gateway_outbound_dispatches_total", "Total outbound dispatches", diag.total_outbound_dispatches);
    add_gauge("gateway_battle_route_queued_tasks",
              "Battle backend route tasks waiting for a worker",
              diag.battle_route.queued_tasks);
    add_gauge("gateway_battle_route_queue_capacity",
              "Maximum number of queued battle backend route tasks",
              diag.battle_route.queue_capacity);
    add_counter("gateway_battle_route_rejected_tasks_total",
                "Battle backend route tasks rejected because the queue was full",
                diag.battle_route.rejected_tasks);
    add_counter("gateway_battle_route_dropped_completions_total",
                "Battle backend route completions rejected by the owner dispatcher",
                diag.battle_route.dropped_completions);

    for (const auto& [svc, metrics] : diag.backend_metrics) {
        std::string prefix = "gateway_backend_" + svc + "_";
        add_counter((prefix + "requests_total").c_str(), "Backend requests", metrics.total_requests);
        add_counter((prefix + "successes_total").c_str(), "Backend successes", metrics.total_successes);
        add_counter((prefix + "errors_total").c_str(), "Backend errors", metrics.total_errors);
        add_counter((prefix + "timeouts_total").c_str(), "Backend timeouts", metrics.total_timeouts);
        add_gauge((prefix + "avg_latency_us").c_str(),
                  "Backend average route latency in microseconds",
                  metrics.latency_sample_count > 0
                      ? metrics.total_latency_us / metrics.latency_sample_count
                      : 0);
        add_gauge((prefix + "p50_latency_us").c_str(),
                  "Backend route latency p50 upper bound in microseconds",
                  backend_latency_percentile_us(metrics, 0.50));
        add_gauge((prefix + "p90_latency_us").c_str(),
                  "Backend route latency p90 upper bound in microseconds",
                  backend_latency_percentile_us(metrics, 0.90));
        add_gauge((prefix + "p99_latency_us").c_str(),
                  "Backend route latency p99 upper bound in microseconds",
                  backend_latency_percentile_us(metrics, 0.99));
        prom += "# HELP gateway_backend_route_latency_us Backend route latency histogram in microseconds\n";
        prom += "# TYPE gateway_backend_route_latency_us histogram\n";
        std::uint64_t cumulative = 0;
        for (std::size_t i = 0; i < metrics.latency_bucket_counts.size(); ++i) {
            cumulative += metrics.latency_bucket_counts[i];
            const auto upper_bound = kBackendLatencyBucketUpperBoundsUs[i];
            const auto le = upper_bound == std::numeric_limits<std::uint64_t>::max()
                                ? std::string("+Inf")
                                : std::to_string(upper_bound);
            prom += "gateway_backend_route_latency_us_bucket{service=\"" + svc +
                    "\",le=\"" + le + "\"} " + std::to_string(cumulative) + "\n";
        }
        prom += "gateway_backend_route_latency_us_sum{service=\"" + svc + "\"} " +
                std::to_string(metrics.total_latency_us) + "\n";
        prom += "gateway_backend_route_latency_us_count{service=\"" + svc + "\"} " +
                std::to_string(metrics.latency_sample_count) + "\n";
    }

    snap.prometheus_text = prom;
    snap.json_text = diagnostics_json();
    snap.diagnostics_text = diagnostics_json();
    snap.diagnostics_json_text = diagnostics_json();
    return snap;
}

void DemoServer::do_accept() {
    if (!acceptor_ || stop_requested_.load(std::memory_order_acquire)) {
        return;
    }
    acceptor_->async_accept([this](std::unique_ptr<v2::io::IoSession> session) {
        if (stop_requested_.load(std::memory_order_acquire)) {
            return;
        }
        if (!session) {
            do_accept();
            return;
        }

        // Check connection limit before accepting.
        if (options_.max_connections.has_value() &&
            io_engine_->total_session_count() > *options_.max_connections) {
            const auto core_id = session->owning_core_id();
            session->start();
            session->send(net::protocol::kErrorResponse,
                          0,
                          static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited),
                          "connection_limit_reached",
                          0);
            session->close();
            io_engine_->register_session(core_id);
            io_engine_->unregister_session(core_id);
            do_accept();
            return;
        }

        const auto session_id = next_session_id_.fetch_add(1, std::memory_order_relaxed);
        auto session_ref = std::shared_ptr<v2::io::IoSession>(std::move(session));
        const std::weak_ptr<v2::io::IoSession> weak_session = session_ref;
        const auto session_core = session_ref->owning_core_id();
        session_ref->set_packet_handler(
            [this, session_id, weak_session](v2::io::IoSession::PacketMessage message) {
                if (stop_requested_.load(std::memory_order_acquire)) {
                    return;
                }
                if (message.message_id == net::protocol::kHeartbeatRequest) {
                    const auto session_ref = weak_session.lock();
                    if (!session_ref) {
                        return;
                    }
                    session_ref->send(net::protocol::kHeartbeatResponse,
                                      message.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                      "pong",
                                      message.flags,
                                      true);
                    return;
                }
                if (message.message_id == net::protocol::kEchoRequest) {
                    const auto session_ref = weak_session.lock();
                    if (!session_ref) {
                        return;
                    }
                    session_ref->send(net::protocol::kEchoResponse,
                                      message.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                      std::move(message.body),
                                      message.flags,
                                      true);
                    return;
                }
                enqueue_packet(session_id, std::move(message));
            });
        bool registered = false;
        std::string registration_error;
        {
            std::scoped_lock lock(sessions_mutex_);
            if (stop_requested_.load(std::memory_order_acquire)) {
                registration_error = "server stopping";
            } else {
                try {
                    auto [session_it, session_inserted] =
                        sessions_.emplace(session_id, session_ref);
                    if (!session_inserted) {
                        registration_error = "duplicate session id";
                    } else {
                        try {
                            const auto [core_it, core_inserted] =
                                session_core_by_id_.emplace(session_id, session_core);
                            (void)core_it;
                            if (core_inserted) {
                                registered = true;
                            } else {
                                sessions_.erase(session_it);
                                registration_error = "duplicate session core mapping";
                            }
                        } catch (...) {
                            sessions_.erase(session_it);
                            throw;
                        }
                    }
                } catch (const std::exception& ex) {
                    registration_error = ex.what();
                } catch (...) {
                    registration_error = "unknown registration exception";
                }
            }
        }
        if (!registered) {
            LOG_ERROR("v2 demo server rejected session registration: session_id={} core_id={} reason={}",
                      session_id,
                      session_core,
                      registration_error);
            session_ref->set_packet_handler({});
            session_ref->set_close_handler({});
            session_ref->close();
            do_accept();
            return;
        }

        session_ref->set_close_handler([this, session_id]() {
            if (stop_requested_.load(std::memory_order_acquire)) {
                return;
            }
            enqueue_session_closed(session_id);
            std::optional<std::uint32_t> session_core_id;
            {
                std::scoped_lock lock(sessions_mutex_);
                sessions_.erase(session_id);
                const auto core_it = session_core_by_id_.find(session_id);
                if (core_it != session_core_by_id_.end()) {
                    session_core_id = core_it->second;
                    session_core_by_id_.erase(core_it);
                }
            }
            if (session_core_id.has_value()) {
                std::scoped_lock lock(io_core_snapshot_mutex_);
                auto it = io_core_snapshots_by_id_.find(*session_core_id);
                if (it != io_core_snapshots_by_id_.end() && it->second.active_sessions > 0) {
                    --it->second.active_sessions;
                }
            }
        });
        {
            std::scoped_lock lock(io_core_snapshot_mutex_);
            auto& snapshot = io_core_snapshots_by_id_[session_core];
            snapshot.core_id = session_core;
            ++snapshot.active_sessions;
            ++snapshot.accepted_sessions;
        }

        try {
            session_ref->start();
        } catch (const std::exception& ex) {
            {
                std::scoped_lock lock(sessions_mutex_);
                sessions_.erase(session_id);
                session_core_by_id_.erase(session_id);
            }
            {
                std::scoped_lock lock(io_core_snapshot_mutex_);
                auto& snapshot = io_core_snapshots_by_id_[session_core];
                if (snapshot.active_sessions > 0) {
                    --snapshot.active_sessions;
                }
                if (snapshot.accepted_sessions > 0) {
                    --snapshot.accepted_sessions;
                }
            }
            LOG_ERROR("v2 demo server failed to start session: session_id={} core_id={} reason={}",
                      session_id,
                      session_core,
                      ex.what());
            session_ref->set_packet_handler({});
            session_ref->set_close_handler({});
            session_ref->close();
            do_accept();
            return;
        } catch (...) {
            {
                std::scoped_lock lock(sessions_mutex_);
                sessions_.erase(session_id);
                session_core_by_id_.erase(session_id);
            }
            {
                std::scoped_lock lock(io_core_snapshot_mutex_);
                auto& snapshot = io_core_snapshots_by_id_[session_core];
                if (snapshot.active_sessions > 0) {
                    --snapshot.active_sessions;
                }
                if (snapshot.accepted_sessions > 0) {
                    --snapshot.accepted_sessions;
                }
            }
            LOG_ERROR("v2 demo server failed to start session: session_id={} core_id={} reason=unknown exception",
                      session_id,
                      session_core);
            session_ref->set_packet_handler({});
            session_ref->set_close_handler({});
            session_ref->close();
            do_accept();
            return;
        }
        do_accept();
    });
}

void DemoServer::enqueue_packet(SessionId session_id,
                                v2::io::IoSession::PacketMessage message) {
    if (stop_requested_.load(std::memory_order_acquire)) {
        return;
    }
    {
        std::scoped_lock lock(gateway_queue_mutex_);
        gateway_queue_.push_back(GatewayQueueItem{
            .session_id = session_id,
            .message = std::move(message),
        });
    }
    gateway_queue_cv_.notify_one();
}

void DemoServer::enqueue_session_closed(SessionId session_id) {
    if (stop_requested_.load(std::memory_order_acquire)) {
        return;
    }
    {
        std::scoped_lock lock(gateway_queue_mutex_);
        gateway_queue_.push_back(GatewayQueueItem{.session_id = session_id});
    }
    gateway_queue_cv_.notify_one();
}

bool DemoServer::enqueue_runtime_task(std::function<void()> task) {
    if (!task || stop_requested_.load(std::memory_order_acquire)) {
        return false;
    }
    {
        std::scoped_lock lock(gateway_queue_mutex_);
        if (gateway_worker_stopping_) {
            return false;
        }
        gateway_queue_.push_back(GatewayQueueItem{.runtime_task = std::move(task)});
    }
    gateway_queue_cv_.notify_one();
    return true;
}

void DemoServer::start_gateway_worker() {
    {
        std::scoped_lock lock(gateway_queue_mutex_);
        gateway_worker_stopping_ = false;
    }
    gateway_worker_ = std::make_unique<std::thread>([this]() {
        for (;;) {
            try {
                GatewayQueueItem item;
                {
                    std::unique_lock lock(gateway_queue_mutex_);
                    gateway_queue_cv_.wait(lock, [this]() {
                        return gateway_worker_stopping_ || !gateway_queue_.empty();
                    });
                    if (gateway_queue_.empty()) {
                        if (gateway_worker_stopping_) {
                            return;
                        }
                        continue;
                    }
                    item = std::move(gateway_queue_.front());
                    gateway_queue_.pop_front();
                }

                std::scoped_lock handle_lock(gateway_handle_mutex_);
                if (item.runtime_task) {
                    item.runtime_task();
                    continue;
                }
                if (!item.message.has_value()) {
                    runtime_.on_session_closed(item.session_id);
                    continue;
                }

                auto& message = *item.message;
                (void)adapter_.handle_incoming(ClientEnvelope{
                    .session_id = item.session_id,
                    .protocol_message_id = message.message_id,
                    .request_id = message.request_id,
                    .error_code = message.error_code,
                    .flags = message.flags,
                    .body = std::move(message.body),
                });
            } catch (const std::exception& ex) {
                LOG_WARN("DemoServer: gateway worker recovered from exception: {}", ex.what());
            } catch (...) {
                LOG_WARN("DemoServer: gateway worker recovered from unknown exception");
            }
        }
    });
}

void DemoServer::stop_gateway_worker() {
    {
        std::scoped_lock lock(gateway_queue_mutex_);
        gateway_worker_stopping_ = true;
    }
    gateway_queue_cv_.notify_all();
    if (gateway_worker_ && gateway_worker_->joinable()) {
        gateway_worker_->join();
    }
    gateway_worker_.reset();
    {
        std::scoped_lock lock(gateway_queue_mutex_);
        gateway_queue_.clear();
    }
}

void DemoServer::load_gateway_config() {
    const std::filesystem::path config_path = gateway_config_path();
    std::ifstream file(config_path);
    if (!file.is_open()) {
        LOG_WARN("DemoServer: cannot open gateway config {}", config_path.string());
        return;
    }

    nlohmann::json doc;
    try {
        file >> doc;
    } catch (const std::exception& e) {
        LOG_WARN("DemoServer: failed to parse gateway config: {}", e.what());
        return;
    }

    auto* bridge = runtime_.service_bridge();
    if (!bridge) return;

    const auto& backends = doc.value("backends", nlohmann::json::object());
    for (const auto& [key, entry] : backends.items()) {
        v2::service::ServiceId service_id;
        if (key == "login") {
            service_id = v2::service::ServiceId::kLogin;
        } else if (key == "room") {
            service_id = v2::service::ServiceId::kRoom;
        } else if (key == "battle") {
            service_id = v2::service::ServiceId::kBattle;
        } else if (key == "match") {
            service_id = v2::service::ServiceId::kMatchmaking;
        } else if (key == "leaderboard") {
            service_id = v2::service::ServiceId::kLeaderboard;
        } else {
            continue;
        }

        GatewayServiceBridge::BackendConfig cfg;
        cfg.host = entry.value("host", "127.0.0.1");
        cfg.port = static_cast<std::uint16_t>(entry.value("port", 0));
        auto cfg_host = cfg.host;
        auto cfg_port = cfg.port;
        bridge->update_backend_config(service_id, std::move(cfg));

        // P1a: Register instance in cluster router for service discovery
        if (auto* cr = bridge->get_cluster_router().get()) {
            v3::cluster::ServiceInstance inst;
            inst.node.host = cfg_host;
            inst.node.port = cfg_port;
            inst.node.node_name = cfg_host + ":" + std::to_string(cfg_port);
            inst.service_name = key;
            inst.state = v3::cluster::ServiceState::kHealthy;
            inst.registered_at = std::chrono::steady_clock::now();
            inst.last_heartbeat = inst.registered_at;
            cr->register_service(std::move(inst));
        }

        LOG_INFO("DemoServer: reloaded {} backend -> {}:{}", key, cfg_host, cfg_port);
    }

    // v3.1.0: Feature flags
    if (doc.contains("feature_flags")) {
        feature_flags_ = std::make_shared<v2::config::FeatureFlags>();
        feature_flags_->load_from_json(doc["feature_flags"]);
        feature_flags_->apply_env_overrides();
        bridge->set_feature_flags(feature_flags_);
    }

    // v3.1.0: TLS config + security policy
    if (doc.contains("tls") || doc.contains("security_policy")) {
        v3::cluster::SecurityPolicy policy;

        if (doc.contains("tls")) {
            const auto& tls = doc["tls"];
            policy.tls_config.cert.cert_chain_path =
                tls.value("cert_chain_path", "certs/server.crt");
            policy.tls_config.cert.private_key_path =
                tls.value("private_key_path", "certs/server.key");
            policy.tls_config.cert.ca_cert_path =
                tls.value("ca_cert_path", "certs/ca.crt");
            auto mode = tls.value("verify_mode", "mutual");
            if (mode == "mutual") {
                policy.tls_config.verify_mode =
                    v3::cluster::TlsVerifyMode::kMutual;
            } else if (mode == "server") {
                policy.tls_config.verify_mode =
                    v3::cluster::TlsVerifyMode::kServer;
            }
        }

        if (doc.contains("security_policy")) {
            const auto& sp = doc["security_policy"];
            policy.require_tls = sp.value("require_tls", false);

            auto load_svc = [&](const char* key,
                                v3::cluster::SecurityPolicy::ServiceTlsPolicy& out) {
                if (sp.contains(key)) {
                    const auto& svc = sp[key];
                    out.tls_required = svc.value("tls_required", true);
                    out.mtls_required = svc.value("mtls_required", false);
                }
            };
            load_svc("login", policy.login_policy);
            load_svc("room", policy.room_policy);
            load_svc("battle", policy.battle_policy);
            load_svc("match", policy.match_policy);
            load_svc("leaderboard", policy.leaderboard_policy);
        }

        security_policy_ = std::move(policy);
        bridge->set_security_policy(*security_policy_);
    }
}

void DemoServer::start_config_watcher() {
    const std::filesystem::path config_path = gateway_config_path();
    if (!std::filesystem::exists(config_path)) {
        LOG_INFO("DemoServer: no gateway config at {}, skipping config watcher",
                 config_path.string());
        return;
    }

    config_watcher_ = std::make_unique<v2::config::ConfigWatcher>(
        config_path,
        [this]() { load_gateway_config(); });
    config_watcher_->start();
    LOG_INFO("DemoServer: config watcher started for {}", config_path.string());
}

void DemoServer::reload_backend_configs() {
    load_gateway_config();
}

}  // namespace v2::gateway
