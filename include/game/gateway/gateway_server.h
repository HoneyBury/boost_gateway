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

namespace game::gateway {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;
using error_code = boost::system::error_code;

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
};

}  // namespace game::gateway
