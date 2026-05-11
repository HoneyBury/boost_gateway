#pragma once

#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"

#include <filesystem>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace game::gateway {

struct GatewayIoCoreSnapshot {
    std::uint32_t core_id = 0;
    std::uint64_t active_sessions = 0;
    std::uint64_t accepted_sessions = 0;
    std::uint64_t dispatch_back_tasks = 0;
    std::uint64_t maintenance_probes = 0;
};

struct GatewayRuntimeMetricsSnapshot {
    GatewayMetricsSnapshot counters;
    GatewayMetricsRateSnapshot rates;
    std::uint64_t active_sessions = 0;
    std::uint64_t authenticated_sessions = 0;
    std::uint64_t active_rooms = 0;
    std::uint64_t active_battles = 0;
    std::uint64_t dispatch_back_tasks = 0;
    std::uint64_t dispatch_inline_fallbacks = 0;
    std::uint64_t maintenance_probe_tasks = 0;
    std::vector<GatewayIoCoreSnapshot> io_cores;
    std::string diagnostics_extension_text;
    std::string diagnostics_extension_json_text;
};

struct GatewayMetricsExportOptions {
    std::optional<std::filesystem::path> prometheus_path;
    std::optional<std::filesystem::path> json_path;
};

[[nodiscard]] GatewayRuntimeMetricsSnapshot collect_runtime_metrics(
    const GatewayMetrics& metrics,
    const SessionManager& session_manager,
    const game::room::RoomManager& room_manager,
    const game::battle::BattleManager& battle_manager,
    const GatewayMetricsSnapshot* previous = nullptr,
    double elapsed_sec = 0.0);

[[nodiscard]] std::string render_prometheus_metrics(const GatewayRuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_json_metrics(const GatewayRuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_diagnostics_metrics(const GatewayRuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_diagnostics_json_metrics(const GatewayRuntimeMetricsSnapshot& snapshot);

bool write_metrics_files(const GatewayRuntimeMetricsSnapshot& snapshot,
                         const GatewayMetricsExportOptions& options);

}  // namespace game::gateway
