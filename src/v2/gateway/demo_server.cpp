#include "v2/gateway/demo_server.h"

#include "app/logging.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>
#include <utility>

namespace v2::gateway {

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

    if (options_.login_backend_config.has_value() ||
        options_.room_backend_config.has_value() ||
        options_.battle_backend_config.has_value()) {
        auto bridge = std::make_unique<GatewayServiceBridge>(
            options_.login_backend_config,
            options_.room_backend_config,
            options_.battle_backend_config,
            backend_metrics_);
        bridge->set_service_registry(service_registry_);
        runtime_.set_service_bridge(std::move(bridge));
        load_gateway_config();
    }

    archive_store_ = std::make_unique<JsonFileBattleDataStore>("v2_archive");
    runtime_.set_archive_sink(archive_store_.get());
}

DemoServer::~DemoServer() = default;

void DemoServer::start() {
    if (options_.http_management_port.has_value()) {
        management_io_ = std::make_unique<boost::asio::io_context>();
        http_manager_ = std::make_unique<net::HttpManager>(
            management_io_->get_executor(), *options_.http_management_port);
        http_manager_->set_health_provider([this]() { return diagnostics_json(); });
        http_manager_->set_metrics_provider([this]() {
            const auto json = diagnostics_json();
            return net::HttpMetricsSnapshot{
                .prometheus_text = json,
                .json_text = json,
                .diagnostics_text = json,
                .diagnostics_json_text = json,
            };
        });
        http_manager_->start();
        management_thread_ = std::make_unique<std::thread>([this]() { management_io_->run(); });
        LOG_INFO("v2 demo server HTTP management listening on :{}", *options_.http_management_port);
    }
    acceptor_ = io_engine_->listen("127.0.0.1",
                                   port_,
                                   session_options_,
                                   v2::io::IoListenOptions{.fixed_core_id = options_.acceptor_core_id});
    LOG_INFO("v2 demo server listening on 127.0.0.1:{}", acceptor_->local_port());
    do_accept();
    start_config_watcher();
    io_engine_->run();
}

void DemoServer::stop() {
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

    {
        std::scoped_lock lock(sessions_mutex_);
        acceptor_.reset();
        for (auto& [session_id, session] : sessions_) {
            (void)session_id;
            session->close();
        }
        sessions_.clear();
        session_core_by_id_.clear();
    }
    {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        for (auto& [core_id, snapshot] : io_core_snapshots_by_id_) {
            (void)core_id;
            snapshot.active_sessions = 0;
        }
    }
    io_engine_->stop();
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
            session->send(envelope.protocol_message_id,
                          envelope.request_id,
                          envelope.error_code,
                          std::move(envelope.body),
                          envelope.flags);
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
        backend_metrics[service_key] = {
            {"total_requests", snap.total_requests},
            {"total_successes", snap.total_successes},
            {"total_timeouts", snap.total_timeouts},
            {"total_unavailable", snap.total_unavailable},
            {"total_errors", snap.total_errors},
            {"total_degraded", snap.total_degraded},
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

void DemoServer::do_accept() {
    if (!acceptor_) {
        return;
    }
    acceptor_->async_accept([this](std::unique_ptr<v2::io::IoSession> session) {
        if (!session) {
            return;
        }

        // Check connection limit before accepting
        if (options_.max_connections.has_value() &&
            io_engine_->total_session_count() >= *options_.max_connections) {
            // Accept but immediately close with error
            const auto core_id = session->owning_core_id();
            session->start();
            session->send(net::protocol::kErrorResponse, 0,
                          static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited),
                          "connection_limit_reached", 0);
            session->close();
            io_engine_->register_session(core_id);
            io_engine_->unregister_session(core_id);
            do_accept();
            return;
        }

        const auto session_id = next_session_id_++;
        auto session_ref = std::shared_ptr<v2::io::IoSession>(std::move(session));
        const auto session_core = session_ref->owning_core_id();
        session_ref->set_packet_handler(
            [this, session_id](v2::io::IoSession::PacketMessage message) {
                (void)adapter_.handle_incoming(ClientEnvelope{
                    .session_id = session_id,
                    .protocol_message_id = message.message_id,
                    .request_id = message.request_id,
                    .error_code = message.error_code,
                    .flags = message.flags,
                    .body = std::move(message.body),
                });
            });
        session_ref->set_close_handler([this, session_id]() {
            runtime_.on_session_closed(session_id);
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
            std::scoped_lock lock(sessions_mutex_);
            sessions_.emplace(session_id, session_ref);
            session_core_by_id_.emplace(session_id, session_core);
            sessions_.at(session_id)->start();
        }
        {
            std::scoped_lock lock(io_core_snapshot_mutex_);
            auto& snapshot = io_core_snapshots_by_id_[session_core];
            snapshot.core_id = session_core;
            ++snapshot.active_sessions;
            ++snapshot.accepted_sessions;
        }

        do_accept();
    });
}

void DemoServer::load_gateway_config() {
    const std::filesystem::path config_path("config/gateway.json");
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
        } else {
            continue;
        }

        GatewayServiceBridge::BackendConfig cfg;
        cfg.host = entry.value("host", "127.0.0.1");
        cfg.port = static_cast<std::uint16_t>(entry.value("port", 0));
        bridge->update_backend_config(service_id, std::move(cfg));
        LOG_INFO("DemoServer: reloaded {} backend -> {}:{}", key, cfg.host, cfg.port);
    }
}

void DemoServer::start_config_watcher() {
    const std::filesystem::path config_path("config/gateway.json");
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
