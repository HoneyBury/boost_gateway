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

void GatewayServer::do_accept() {
    acceptor_.async_accept([this](const error_code& ec, tcp::socket socket) {
        if (ec) {
            if (ec != asio::error::operation_aborted) {
                LOG_ERROR("Accept failed: {}", ec.message());
            }
            return;
        }

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
                                     std::move(message.body));
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

        const auto runtime_snapshot =
            collect_runtime_metrics(metrics_, session_manager_, room_manager_, battle_manager_);
        LOG_INFO("Gateway metrics: {}, active_sessions={}, authenticated_sessions={}, rooms={}, battles={}",
                 metrics_.summary(),
                 runtime_snapshot.active_sessions,
                 runtime_snapshot.authenticated_sessions,
                 runtime_snapshot.active_rooms,
                 runtime_snapshot.active_battles);

        if (!write_metrics_files(runtime_snapshot, metrics_export_options_)) {
            LOG_WARN("Failed to export metrics files");
        }

        arm_metrics_timer();
    });
}

}  // namespace game::gateway
