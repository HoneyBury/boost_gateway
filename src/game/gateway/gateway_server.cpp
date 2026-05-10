#include "game/gateway/gateway_server.h"

#include "app/audit_log.h"
#include "app/logging.h"
#include "game/room/room_battle_lifecycle.h"

#include <algorithm>
#include <string_view>
#include <utility>

namespace game::gateway {

GatewayServer::GatewayServer(asio::io_context& io_context,
                             net::MessageDispatcher& dispatcher,
                             SessionManager& session_manager,
                             game::room::RoomManager& room_manager,
                             game::battle::BattleManager& battle_manager,
                             GatewayMetrics& metrics,
                             std::uint16_t port,
                             std::uint16_t http_management_port,
                             net::SessionOptions session_options,
                             std::chrono::milliseconds metrics_log_interval,
                             GatewayMetricsExportOptions metrics_export_options,
                             std::unique_ptr<v2::io::IoEngine> io_engine)
    : dispatcher_(dispatcher),
      session_manager_(session_manager),
      room_manager_(room_manager),
      battle_manager_(battle_manager),
      metrics_(metrics),
      metrics_timer_(io_context),
      session_options_(std::move(session_options)),
      metrics_log_interval_(metrics_log_interval),
      metrics_export_options_(std::move(metrics_export_options)),
      io_engine_(std::move(io_engine)) {
    if (io_engine_ != nullptr) {
        io_acceptor_ = io_engine_->listen("0.0.0.0", port, session_options_);
    } else {
        acceptor_ = std::make_unique<tcp::acceptor>(io_context, tcp::endpoint(tcp::v4(), port));
    }

    if (http_management_port > 0) {
        http_manager_ = std::make_unique<net::HttpManager>(
            io_context.get_executor(), http_management_port);
    }
}

void GatewayServer::start() {
    LOG_INFO("Gateway server listening on 0.0.0.0:{}", local_port());

    if (http_manager_) {
        http_manager_->set_metrics_provider(
            [this]() -> net::HttpMetricsSnapshot {
                const auto snapshot = collect_runtime_metrics_snapshot();
                return {
                    .prometheus_text = render_prometheus_metrics(snapshot),
                    .json_text = render_json_metrics(snapshot),
                    .diagnostics_text = render_diagnostics_metrics(snapshot),
                    .diagnostics_json_text = render_diagnostics_json_metrics(snapshot),
                };
            });
        http_manager_->start();
    }

    schedule_io_core_probe();
    arm_metrics_timer();
    if (io_acceptor_ != nullptr) {
        do_accept_with_io_engine();
        io_engine_->run();
        return;
    }

    do_accept();
}

void GatewayServer::stop() {
    error_code ignored_ec;
    metrics_timer_.cancel();

    if (http_manager_) {
        http_manager_->stop();
    }

    const auto sessions = session_manager_.all_sessions();
    for (const auto& session : sessions) {
        (void)release_session_state(session, extract_ip(session->remote_endpoint()));
        session->stop();
    }

    if (acceptor_ != nullptr) {
        acceptor_->close(ignored_ec);
    }
    if (io_engine_ != nullptr) {
        io_engine_->stop();
    }
    io_acceptor_.reset();
}

bool GatewayServer::attach_session(const std::shared_ptr<net::Session>& session) {
    return attach_session_with_core(session, std::nullopt);
}

bool GatewayServer::dispatch_to_session_core(const std::shared_ptr<net::Session>& session,
                                             std::function<void()> task) {
    if (!task) {
        return false;
    }

    const auto core_id = session_io_core(session);
    if (!core_id.has_value() || io_engine_ == nullptr) {
        dispatch_inline_fallbacks_.fetch_add(1, std::memory_order_relaxed);
        task();
        return false;
    }

    dispatch_back_tasks_.fetch_add(1, std::memory_order_relaxed);
    {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        auto& snapshot = io_core_snapshots_by_id_[*core_id];
        snapshot.core_id = *core_id;
        ++snapshot.dispatch_back_tasks;
    }
    io_engine_->dispatch_to_core(*core_id, std::move(task));
    return true;
}

std::uint32_t GatewayServer::io_core_count() const noexcept {
    return io_engine_ == nullptr ? 0U : io_engine_->num_io_cores();
}

std::optional<std::uint32_t> GatewayServer::current_io_core() const noexcept {
    return io_engine_ == nullptr ? std::nullopt : io_engine_->current_core_id();
}

std::optional<std::uint32_t> GatewayServer::session_io_core(
    const std::shared_ptr<net::Session>& session) const {
    if (!session) {
        return std::nullopt;
    }

    std::scoped_lock lock(session_core_mutex_);
    const auto it = session_cores_by_ptr_.find(session.get());
    if (it == session_cores_by_ptr_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::vector<GatewayIoCoreSnapshot> GatewayServer::io_core_snapshot() const {
    std::vector<GatewayIoCoreSnapshot> snapshots;
    const auto core_count = io_core_count();
    snapshots.reserve(core_count);
    for (std::uint32_t core_id = 0; core_id < core_count; ++core_id) {
        snapshots.push_back(GatewayIoCoreSnapshot{
            .core_id = core_id,
            .active_sessions = 0,
            .accepted_sessions = 0,
            .dispatch_back_tasks = 0,
            .maintenance_probes = 0,
        });
    }
    {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        for (const auto& [core_id, snapshot] : io_core_snapshots_by_id_) {
            if (core_id < snapshots.size()) {
                snapshots[core_id] = snapshot;
            } else {
                snapshots.push_back(snapshot);
            }
        }
    }

    std::sort(snapshots.begin(),
              snapshots.end(),
              [](const GatewayIoCoreSnapshot& lhs, const GatewayIoCoreSnapshot& rhs) {
                  return lhs.core_id < rhs.core_id;
              });
    return snapshots;
}

std::uint64_t GatewayServer::dispatch_back_task_count() const noexcept {
    return dispatch_back_tasks_.load(std::memory_order_relaxed);
}

std::uint64_t GatewayServer::dispatch_inline_fallback_count() const noexcept {
    return dispatch_inline_fallbacks_.load(std::memory_order_relaxed);
}

std::uint64_t GatewayServer::maintenance_probe_task_count() const noexcept {
    return maintenance_probe_tasks_.load(std::memory_order_relaxed);
}

bool GatewayServer::dispatch_to_all_io_cores(std::function<void(std::uint32_t core_id)> task) {
    if (io_engine_ == nullptr || !task) {
        return false;
    }
    io_engine_->dispatch_to_all_cores(std::move(task));
    return true;
}

GatewayRuntimeMetricsSnapshot GatewayServer::collect_runtime_metrics_snapshot(
    const GatewayMetricsSnapshot* previous,
    double elapsed_sec) const {
    auto snapshot = collect_runtime_metrics(
        metrics_, session_manager_, room_manager_, battle_manager_, previous, elapsed_sec);
    snapshot.dispatch_back_tasks = dispatch_back_task_count();
    snapshot.dispatch_inline_fallbacks = dispatch_inline_fallback_count();
    snapshot.maintenance_probe_tasks = maintenance_probe_task_count();
    snapshot.io_cores = io_core_snapshot();
    return snapshot;
}

void GatewayServer::schedule_io_core_probe() {
    if (io_engine_ == nullptr) {
        return;
    }

    io_engine_->dispatch_to_all_cores([this](std::uint32_t core_id) {
        maintenance_probe_tasks_.fetch_add(1, std::memory_order_relaxed);
        std::scoped_lock lock(io_core_snapshot_mutex_);
        auto& snapshot = io_core_snapshots_by_id_[core_id];
        snapshot.core_id = core_id;
        ++snapshot.maintenance_probes;
    });
}

bool GatewayServer::attach_session_with_core(const std::shared_ptr<net::Session>& session,
                                             std::optional<std::uint32_t> io_core_id) {
    const auto client_endpoint = session->remote_endpoint();
    const auto client_ip = extract_ip(client_endpoint);
    if (!try_acquire_connection_slot(client_ip)) {
        error_code ignored_ec;
        session->socket().close(ignored_ec);
        return false;
    }

    session_manager_.add_session(session);
    metrics_.on_session_accepted();

    if (io_core_id.has_value()) {
        std::scoped_lock lock(session_core_mutex_);
        session_cores_by_ptr_[session.get()] = *io_core_id;
    }
    if (io_core_id.has_value()) {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        auto& snapshot = io_core_snapshots_by_id_[*io_core_id];
        snapshot.core_id = *io_core_id;
        ++snapshot.accepted_sessions;
        ++snapshot.active_sessions;
    }

    if (io_core_id.has_value()) {
        LOG_INFO("Accepted client {} on io_core={}", client_endpoint, *io_core_id);
    } else {
        LOG_INFO("Accepted client {}", client_endpoint);
    }

    session->set_receive_observer(
        [this](const std::shared_ptr<net::Session>&, const net::Session::PacketMessage& message) {
            metrics_.on_packet_received(message.body.size());
        });

    session->set_send_observer(
        [this](const std::shared_ptr<net::Session>&, const net::Session::PacketMessage& message) {
            metrics_.on_packet_sent(message.body.size());
        });

    session->set_packet_handler(
        [this](const std::shared_ptr<net::Session>& session_ptr, net::Session::PacketMessage message) {
            if (packet_bridge_) {
                packet_bridge_->on_packet(session_ptr, message);
            }
            dispatcher_.dispatch(session_ptr,
                                 message.message_id,
                                 message.request_id,
                                 message.error_code,
                                 std::move(message.body),
                                 message.trace_id,
                                 message.flags);
        });

    session->set_close_handler(
        [this, client_ip](const std::shared_ptr<net::Session>& session_ptr, const error_code&) {
            (void)release_session_state(session_ptr, client_ip);
        });

    session->start();
    return true;
}

std::uint16_t GatewayServer::local_port() const {
    if (acceptor_ != nullptr) {
        return acceptor_->local_endpoint().port();
    }
    if (io_acceptor_ != nullptr) {
        return io_acceptor_->local_port();
    }
    return 0;
}

void GatewayServer::set_packet_bridge(std::shared_ptr<GatewayPacketBridge> packet_bridge) {
    packet_bridge_ = std::move(packet_bridge);
}

void GatewayServer::set_connection_limits(std::size_t max_total, std::size_t per_ip) {
    max_connections_ = max_total;
    per_ip_limit_ = per_ip;
}

std::size_t GatewayServer::active_connections() const {
    return active_connection_count_.load(std::memory_order_relaxed);
}

void GatewayServer::do_accept() {
    acceptor_->async_accept([this](const error_code& ec, tcp::socket socket) {
        if (ec) {
            if (ec != asio::error::operation_aborted) {
                LOG_ERROR("Accept failed: {}", ec.message());
            }
            return;
        }

        auto session = std::make_shared<net::Session>(std::move(socket), session_options_);
        (void)attach_session_with_core(session, std::nullopt);
        do_accept();
    });
}

void GatewayServer::do_accept_with_io_engine() {
    if (io_acceptor_ == nullptr) {
        return;
    }

    const auto accept_core_id = io_acceptor_->owning_core_id();
    io_acceptor_->async_accept_native([this, accept_core_id](std::shared_ptr<net::Session> session) {
        if (session == nullptr) {
            return;
        }
        (void)attach_session_with_core(session, accept_core_id);
        do_accept_with_io_engine();
    });
}

bool GatewayServer::release_session_state(const std::shared_ptr<net::Session>& session,
                                          std::string_view client_ip) {
    if (!session_manager_.contains(session)) {
        return false;
    }

    std::optional<std::uint32_t> io_core_id;
    {
        std::scoped_lock lock(session_core_mutex_);
        const auto it = session_cores_by_ptr_.find(session.get());
        if (it != session_cores_by_ptr_.end()) {
            io_core_id = it->second;
            session_cores_by_ptr_.erase(it);
        }
    }

    const auto room_id = room_manager_.room_id_of(session);
    if (packet_bridge_) {
        packet_bridge_->on_close(session);
    }
    room_manager_.remove_session(session);
    if (room_id) {
        game::room::clear_battle_if_room_empty(battle_manager_, room_manager_, *room_id);
    }
    session_manager_.remove_session(session);
    metrics_.on_session_closed();
    release_connection_slot(client_ip);
    if (io_core_id.has_value()) {
        std::scoped_lock lock(io_core_snapshot_mutex_);
        auto it = io_core_snapshots_by_id_.find(*io_core_id);
        if (it != io_core_snapshots_by_id_.end() && it->second.active_sessions > 0) {
            --it->second.active_sessions;
        }
    }
    return true;
}

bool GatewayServer::try_acquire_connection_slot(std::string_view client_ip) {
    const auto current = active_connection_count_.load(std::memory_order_relaxed);
    if (max_connections_ > 0 && current >= max_connections_) {
        LOG_WARN("Connection rejected: at max capacity ({})", max_connections_);
        AUDIT_LOG("connection_rejected", "reason=max_capacity");
        metrics_.on_packet_blocked();
        return false;
    }

    if (per_ip_limit_ > 0) {
        std::scoped_lock lock(ip_count_mutex_);
        auto& count = ip_connection_counts_[std::string(client_ip)];
        if (count >= per_ip_limit_) {
            LOG_WARN("Connection rejected: IP {} at per-IP limit ({})", client_ip, per_ip_limit_);
            AUDIT_LOG("connection_rejected", "reason=per_ip_limit ip=" + std::string(client_ip));
            metrics_.on_packet_blocked();
            return false;
        }
        ++count;
    }

    active_connection_count_.fetch_add(1, std::memory_order_relaxed);
    return true;
}

void GatewayServer::release_connection_slot(std::string_view client_ip) {
    active_connection_count_.fetch_sub(1, std::memory_order_relaxed);
    if (per_ip_limit_ == 0) {
        return;
    }

    std::scoped_lock lock(ip_count_mutex_);
    auto it = ip_connection_counts_.find(std::string(client_ip));
    if (it == ip_connection_counts_.end()) {
        return;
    }

    if (it->second > 1) {
        --it->second;
        return;
    }

    ip_connection_counts_.erase(it);
}

std::string GatewayServer::extract_ip(std::string_view remote_endpoint) {
    const auto colon = remote_endpoint.rfind(':');
    if (colon == std::string_view::npos) {
        return std::string(remote_endpoint);
    }
    return std::string(remote_endpoint.substr(0, colon));
}

void GatewayServer::arm_metrics_timer() {
    metrics_timer_.expires_after(metrics_log_interval_);
    metrics_timer_.async_wait([this](const error_code& ec) {
        if (ec == asio::error::operation_aborted) {
            return;
        }

        if (ec) {
            LOG_WARN("Metrics timer failed: {}", ec.message());
            return;
        }

        const auto now = std::chrono::steady_clock::now();
        const auto* prev_ptr = &previous_metrics_snapshot_;
        double elapsed_sec = 0.0;
        if (last_metrics_export_time_.time_since_epoch().count() > 0) {
            elapsed_sec = std::chrono::duration<double>(now - last_metrics_export_time_).count();
        }
        last_metrics_export_time_ = now;

        const auto runtime_snapshot = collect_runtime_metrics_snapshot(prev_ptr, elapsed_sec);
        previous_metrics_snapshot_ = runtime_snapshot.counters;

        std::string io_core_summary;
        if (!runtime_snapshot.io_cores.empty()) {
            io_core_summary = " io_cores=[";
            for (std::size_t index = 0; index < runtime_snapshot.io_cores.size(); ++index) {
                const auto& core = runtime_snapshot.io_cores[index];
                if (index > 0) {
                    io_core_summary += ",";
                }
                io_core_summary += std::to_string(core.core_id);
                io_core_summary += ":";
                io_core_summary += std::to_string(core.active_sessions);
            }
            io_core_summary += "]";
        }

        LOG_INFO("Gateway metrics: {}, active_sessions={}, auth_sessions={}, rooms={}, battles={}, "
                 "pkts_recv/s={:.1f}, pkts_sent/s={:.1f}{}",
                 metrics_.summary(),
                 runtime_snapshot.active_sessions,
                 runtime_snapshot.authenticated_sessions,
                 runtime_snapshot.active_rooms,
                 runtime_snapshot.active_battles,
                 runtime_snapshot.rates.received_packets_per_sec,
                 runtime_snapshot.rates.sent_packets_per_sec,
                 io_core_summary);

        if (!write_metrics_files(runtime_snapshot, metrics_export_options_)) {
            LOG_WARN("Failed to export metrics files");
        }

        schedule_io_core_probe();
        arm_metrics_timer();
    });
}

}  // namespace game::gateway
