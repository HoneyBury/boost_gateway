#include "game/gateway/gateway_metrics_exporter.h"

#include <fmt/format.h>
#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>

namespace game::gateway {
namespace {

using json = nlohmann::json;

bool write_text_file(const std::filesystem::path& path, const std::string& content) {
    if (path.empty()) {
        return false;
    }

    if (const auto parent = path.parent_path(); !parent.empty()) {
        std::filesystem::create_directories(parent);
    }

    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    if (!output.is_open()) {
        return false;
    }

    output.write(content.data(), static_cast<std::streamsize>(content.size()));
    return static_cast<bool>(output);
}

}  // namespace

GatewayRuntimeMetricsSnapshot collect_runtime_metrics(const GatewayMetrics& metrics,
                                                      const SessionManager& session_manager,
                                                      const game::room::RoomManager& room_manager,
                                                      const game::battle::BattleManager& battle_manager) {
    const auto session_snapshot = session_manager.snapshot();
    return GatewayRuntimeMetricsSnapshot{
        .counters = metrics.snapshot(),
        .active_sessions = session_snapshot.active_sessions,
        .authenticated_sessions = session_snapshot.authenticated_sessions,
        .active_rooms = room_manager.room_count(),
        .active_battles = battle_manager.active_battle_count(),
    };
}

std::string render_prometheus_metrics(const GatewayRuntimeMetricsSnapshot& snapshot) {
    return fmt::format(
        "# TYPE gateway_sessions_accepted_total counter\n"
        "gateway_sessions_accepted_total {}\n"
        "# TYPE gateway_sessions_closed_total counter\n"
        "gateway_sessions_closed_total {}\n"
        "# TYPE gateway_packets_received_total counter\n"
        "gateway_packets_received_total {}\n"
        "# TYPE gateway_packets_sent_total counter\n"
        "gateway_packets_sent_total {}\n"
        "# TYPE gateway_bytes_received_total counter\n"
        "gateway_bytes_received_total {}\n"
        "# TYPE gateway_bytes_sent_total counter\n"
        "gateway_bytes_sent_total {}\n"
        "# TYPE gateway_packets_blocked_total counter\n"
        "gateway_packets_blocked_total {}\n"
        "# TYPE gateway_login_success_total counter\n"
        "gateway_login_success_total {}\n"
        "# TYPE gateway_room_join_success_total counter\n"
        "gateway_room_join_success_total {}\n"
        "# TYPE gateway_battle_start_success_total counter\n"
        "gateway_battle_start_success_total {}\n"
        "# TYPE gateway_active_sessions gauge\n"
        "gateway_active_sessions {}\n"
        "# TYPE gateway_authenticated_sessions gauge\n"
        "gateway_authenticated_sessions {}\n"
        "# TYPE gateway_active_rooms gauge\n"
        "gateway_active_rooms {}\n"
        "# TYPE gateway_active_battles gauge\n"
        "gateway_active_battles {}\n",
        snapshot.counters.accepted_sessions,
        snapshot.counters.closed_sessions,
        snapshot.counters.received_packets,
        snapshot.counters.sent_packets,
        snapshot.counters.received_bytes,
        snapshot.counters.sent_bytes,
        snapshot.counters.blocked_packets,
        snapshot.counters.login_successes,
        snapshot.counters.room_join_successes,
        snapshot.counters.battle_start_successes,
        snapshot.active_sessions,
        snapshot.authenticated_sessions,
        snapshot.active_rooms,
        snapshot.active_battles);
}

std::string render_json_metrics(const GatewayRuntimeMetricsSnapshot& snapshot) {
    json document = {
        {"accepted_sessions", snapshot.counters.accepted_sessions},
        {"closed_sessions", snapshot.counters.closed_sessions},
        {"received_packets", snapshot.counters.received_packets},
        {"sent_packets", snapshot.counters.sent_packets},
        {"received_bytes", snapshot.counters.received_bytes},
        {"sent_bytes", snapshot.counters.sent_bytes},
        {"blocked_packets", snapshot.counters.blocked_packets},
        {"login_successes", snapshot.counters.login_successes},
        {"room_join_successes", snapshot.counters.room_join_successes},
        {"battle_start_successes", snapshot.counters.battle_start_successes},
        {"active_sessions", snapshot.active_sessions},
        {"authenticated_sessions", snapshot.authenticated_sessions},
        {"active_rooms", snapshot.active_rooms},
        {"active_battles", snapshot.active_battles},
    };
    return document.dump(2);
}

bool write_metrics_files(const GatewayRuntimeMetricsSnapshot& snapshot,
                         const GatewayMetricsExportOptions& options) {
    bool wrote_any_file = false;
    bool all_success = true;

    if (options.prometheus_path) {
        wrote_any_file = true;
        all_success = all_success && write_text_file(*options.prometheus_path, render_prometheus_metrics(snapshot));
    }

    if (options.json_path) {
        wrote_any_file = true;
        all_success = all_success && write_text_file(*options.json_path, render_json_metrics(snapshot));
    }

    return !wrote_any_file || all_success;
}

}  // namespace game::gateway
