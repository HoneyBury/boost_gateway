#pragma once

#include "game/gateway/gateway_metrics_exporter.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"
#include "game/battle/battle_manager.h"
#include "net/http_manager.h"
#include "net/message_dispatcher.h"
#include "net/session.h"

#include <boost/asio.hpp>

#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

namespace game::gateway {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;
using error_code = boost::system::error_code;

class GatewayPacketBridge {
public:
    virtual ~GatewayPacketBridge() = default;

    virtual void on_packet(const std::shared_ptr<net::Session>& session,
                           const net::Session::PacketMessage& message) = 0;
    virtual void on_close(const std::shared_ptr<net::Session>& session) = 0;
};

class GatewayServer {
public:
    GatewayServer(asio::io_context& io_context,
                  net::MessageDispatcher& dispatcher,
                  SessionManager& session_manager,
                  game::room::RoomManager& room_manager,
                  game::battle::BattleManager& battle_manager,
                  GatewayMetrics& metrics,
                  std::uint16_t port,
                  std::uint16_t http_management_port = 0,
                  net::SessionOptions session_options = {},
                  std::chrono::milliseconds metrics_log_interval = std::chrono::milliseconds(5000),
                  GatewayMetricsExportOptions metrics_export_options = {});

    void start();
    void stop();
    void set_connection_limits(std::size_t max_total, std::size_t per_ip);
    void set_packet_bridge(std::shared_ptr<GatewayPacketBridge> packet_bridge);
    [[nodiscard]] std::size_t active_connections() const;
    [[nodiscard]] std::uint16_t local_port() const;

private:
    void do_accept();
    void arm_metrics_timer();

    asio::io_context& io_context_;
    net::MessageDispatcher& dispatcher_;
    SessionManager& session_manager_;
    game::room::RoomManager& room_manager_;
    game::battle::BattleManager& battle_manager_;
    GatewayMetrics& metrics_;
    tcp::acceptor acceptor_;
    asio::steady_timer metrics_timer_;
    net::SessionOptions session_options_;
    std::chrono::milliseconds metrics_log_interval_;
    GatewayMetricsExportOptions metrics_export_options_;
    std::unique_ptr<net::HttpManager> http_manager_;
    GatewayMetricsSnapshot previous_metrics_snapshot_;
    std::chrono::steady_clock::time_point last_metrics_export_time_;
    std::size_t max_connections_ = 0;
    std::size_t per_ip_limit_ = 0;
    std::atomic<std::size_t> active_connection_count_{0};
    std::mutex ip_count_mutex_;
    std::unordered_map<std::string, std::size_t> ip_connection_counts_;
    std::shared_ptr<GatewayPacketBridge> packet_bridge_;
};

}  // namespace game::gateway
