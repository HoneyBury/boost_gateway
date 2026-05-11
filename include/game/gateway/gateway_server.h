#pragma once

#include "game/gateway/gateway_metrics_exporter.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"
#include "game/battle/battle_manager.h"
#include "net/http_manager.h"
#include "net/message_dispatcher.h"
#include "net/session.h"
#include "v2/io/io_engine.h"

#include <boost/asio.hpp>

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

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
    struct DiagnosticsExtensionSnapshot {
        std::string text;
        std::string json_text;
    };
    using DiagnosticsExtensionProvider = std::function<DiagnosticsExtensionSnapshot()>;

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
                  GatewayMetricsExportOptions metrics_export_options = {},
                  std::unique_ptr<v2::io::IoEngine> io_engine = nullptr);

    void start();
    void stop();
    bool attach_session(const std::shared_ptr<net::Session>& session);
    bool dispatch_to_session_core(const std::shared_ptr<net::Session>& session,
                                  std::function<void()> task);
    void set_diagnostics_extension_provider(DiagnosticsExtensionProvider provider);
    bool add_io_listener(std::uint16_t port,
                         v2::io::IoListenOptions options = {});
    void set_connection_limits(std::size_t max_total, std::size_t per_ip);
    void set_packet_bridge(std::shared_ptr<GatewayPacketBridge> packet_bridge);
    [[nodiscard]] std::size_t active_connections() const;
    [[nodiscard]] std::uint16_t local_port() const;
    [[nodiscard]] std::vector<std::uint16_t> local_ports() const;
    [[nodiscard]] std::uint32_t io_core_count() const noexcept;
    [[nodiscard]] std::optional<std::uint32_t> current_io_core() const noexcept;
    [[nodiscard]] std::optional<std::uint32_t> session_io_core(
        const std::shared_ptr<net::Session>& session) const;
    [[nodiscard]] std::vector<GatewayIoCoreSnapshot> io_core_snapshot() const;
    [[nodiscard]] std::uint64_t dispatch_back_task_count() const noexcept;
    [[nodiscard]] std::uint64_t dispatch_inline_fallback_count() const noexcept;
    [[nodiscard]] std::uint64_t maintenance_probe_task_count() const noexcept;
    bool dispatch_to_all_io_cores(std::function<void(std::uint32_t core_id)> task);

private:
    void do_accept();
    void do_accept_with_io_engine(std::size_t listener_index);
    void arm_metrics_timer();
    void schedule_io_core_probe();
    [[nodiscard]] GatewayRuntimeMetricsSnapshot collect_runtime_metrics_snapshot(
        const GatewayMetricsSnapshot* previous = nullptr,
        double elapsed_sec = 0.0) const;
    bool attach_session_with_core(const std::shared_ptr<net::Session>& session,
                                  std::optional<std::uint32_t> io_core_id);
    bool release_session_state(const std::shared_ptr<net::Session>& session,
                               std::string_view client_ip);
    [[nodiscard]] bool try_acquire_connection_slot(std::string_view client_ip);
    void release_connection_slot(std::string_view client_ip);
    [[nodiscard]] static std::string extract_ip(std::string_view remote_endpoint);

    net::MessageDispatcher& dispatcher_;
    SessionManager& session_manager_;
    game::room::RoomManager& room_manager_;
    game::battle::BattleManager& battle_manager_;
    GatewayMetrics& metrics_;
    std::unique_ptr<tcp::acceptor> acceptor_;
    asio::steady_timer metrics_timer_;
    net::SessionOptions session_options_;
    std::chrono::milliseconds metrics_log_interval_;
    GatewayMetricsExportOptions metrics_export_options_;
    std::unique_ptr<net::HttpManager> http_manager_;
    std::unique_ptr<v2::io::IoEngine> io_engine_;
    std::vector<std::unique_ptr<v2::io::IoAcceptor>> io_acceptors_;
    GatewayMetricsSnapshot previous_metrics_snapshot_;
    std::chrono::steady_clock::time_point last_metrics_export_time_;
    std::size_t max_connections_ = 0;
    std::size_t per_ip_limit_ = 0;
    std::atomic<std::size_t> active_connection_count_{0};
    std::mutex ip_count_mutex_;
    std::unordered_map<std::string, std::size_t> ip_connection_counts_;
    mutable std::mutex session_core_mutex_;
    std::unordered_map<const net::Session*, std::uint32_t> session_cores_by_ptr_;
    mutable std::mutex io_core_snapshot_mutex_;
    std::unordered_map<std::uint32_t, GatewayIoCoreSnapshot> io_core_snapshots_by_id_;
    std::atomic<std::uint64_t> dispatch_back_tasks_{0};
    std::atomic<std::uint64_t> dispatch_inline_fallbacks_{0};
    std::atomic<std::uint64_t> maintenance_probe_tasks_{0};
    DiagnosticsExtensionProvider diagnostics_extension_provider_;
    std::shared_ptr<GatewayPacketBridge> packet_bridge_;
};

}  // namespace game::gateway
