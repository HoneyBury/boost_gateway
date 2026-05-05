#include "game/gateway/gateway_server.h"

#include "app/logging.h"

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
                             GatewayMetricsExportOptions metrics_export_options)
    : io_context_(io_context),
      dispatcher_(dispatcher),
      session_manager_(session_manager),
      room_manager_(room_manager),
      battle_manager_(battle_manager),
      metrics_(metrics),
      acceptor_(io_context, tcp::endpoint(tcp::v4(), port)),
      metrics_timer_(io_context),
      session_options_(std::move(session_options)),
      metrics_log_interval_(metrics_log_interval),
      metrics_export_options_(std::move(metrics_export_options)) {
    if (http_management_port > 0) {
        http_manager_ = std::make_unique<net::HttpManager>(
            acceptor_.get_executor(), http_management_port);
    }
}

void GatewayServer::start() {
    LOG_INFO("Gateway server listening on 0.0.0.0:{}", acceptor_.local_endpoint().port());

    if (http_manager_) {
        http_manager_->set_metrics_provider(
            [this]() -> net::HttpMetricsSnapshot {
                const auto snapshot =
                    collect_runtime_metrics(metrics_, session_manager_, room_manager_, battle_manager_);
                return {
                    .prometheus_text = render_prometheus_metrics(snapshot),
                    .json_text = render_json_metrics(snapshot),
                };
            });
        http_manager_->start();
    }

    arm_metrics_timer();
    do_accept();
}

void GatewayServer::stop() {
    error_code ignored_ec;
    acceptor_.close(ignored_ec);
    metrics_timer_.cancel();

    if (http_manager_) {
        http_manager_->stop();
    }

    for (auto& session : session_manager_.all_sessions()) {
        session->stop();
    }
}

std::uint16_t GatewayServer::local_port() const {
    return acceptor_.local_endpoint().port();
}

void GatewayServer::set_connection_limits(std::size_t max_total, std::size_t per_ip) {
    max_connections_ = max_total;
    per_ip_limit_ = per_ip;
}

std::size_t GatewayServer::active_connections() const {
    return active_connection_count_.load(std::memory_order_relaxed);
}

void GatewayServer::do_accept() {
    acceptor_.async_accept([this](const error_code& ec, tcp::socket socket) {
        if (ec) {
            if (ec != asio::error::operation_aborted) {
                LOG_ERROR("Accept failed: {}", ec.message());
            }
            return;
        }

        // Connection limiting
        const auto current = active_connection_count_.load(std::memory_order_relaxed);
        if (max_connections_ > 0 && current >= max_connections_) {
            LOG_WARN("Connection rejected: at max capacity ({})", max_connections_);
            metrics_.on_packet_blocked();
            error_code ignored;
            socket.close(ignored);
            do_accept();
            return;
        }

        if (per_ip_limit_ > 0) {
            const auto ip = socket.remote_endpoint().address().to_string();
            std::scoped_lock lock(ip_count_mutex_);
            if (ip_connection_counts_[ip] >= per_ip_limit_) {
                LOG_WARN("Connection rejected: IP {} at per-IP limit ({})", ip, per_ip_limit_);
                metrics_.on_packet_blocked();
                error_code ignored;
                socket.close(ignored);
                do_accept();
                return;
            }
            ip_connection_counts_[ip]++;
        }

        active_connection_count_.fetch_add(1, std::memory_order_relaxed);

        auto session = std::make_shared<net::Session>(std::move(socket), session_options_);
        session_manager_.add_session(session);
        metrics_.on_session_accepted();

        LOG_INFO("Accepted client {}", session->remote_endpoint());

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
                dispatcher_.dispatch(session_ptr,
                                     message.message_id,
                                     message.request_id,
                                     message.error_code,
                                     std::move(message.body),
                                     message.trace_id,
                                     message.flags);
            });

        session->set_close_handler(
            [this](const std::shared_ptr<net::Session>& session_ptr, const error_code&) {
                const auto room_id = room_manager_.room_id_of(session_ptr);
                room_manager_.remove_session(session_ptr);
                if (room_id && room_manager_.member_count(*room_id) == 0) {
                    battle_manager_.remove_room(*room_id);
                }
                session_manager_.remove_session(session_ptr);
                metrics_.on_session_closed();
                active_connection_count_.fetch_sub(1, std::memory_order_relaxed);
                if (per_ip_limit_ > 0) {
                    const auto ip = session_ptr->remote_endpoint();
                    const auto colon = ip.find(':');
                    const auto addr = colon != std::string::npos ? ip.substr(0, colon) : ip;
                    std::scoped_lock lock(ip_count_mutex_);
                    auto& count = ip_connection_counts_[addr];
                    if (count > 0) count--;
                }
            });

        session->start();
        do_accept();
    });
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

        const auto runtime_snapshot =
            collect_runtime_metrics(metrics_, session_manager_, room_manager_, battle_manager_,
                                    prev_ptr, elapsed_sec);
        previous_metrics_snapshot_ = runtime_snapshot.counters;

        LOG_INFO("Gateway metrics: {}, active_sessions={}, auth_sessions={}, rooms={}, battles={}, "
                 "pkts_recv/s={:.1f}, pkts_sent/s={:.1f}",
                 metrics_.summary(),
                 runtime_snapshot.active_sessions,
                 runtime_snapshot.authenticated_sessions,
                 runtime_snapshot.active_rooms,
                 runtime_snapshot.active_battles,
                 runtime_snapshot.rates.received_packets_per_sec,
                 runtime_snapshot.rates.sent_packets_per_sec);

        if (!write_metrics_files(runtime_snapshot, metrics_export_options_)) {
            LOG_WARN("Failed to export metrics files");
        }

        arm_metrics_timer();
    });
}

}  // namespace game::gateway
