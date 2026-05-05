#pragma once

#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"

#include <filesystem>
#include <optional>
#include <string>

namespace game::gateway {

struct GatewayRuntimeMetricsSnapshot {
    GatewayMetricsSnapshot counters;
    std::uint64_t active_sessions = 0;
    std::uint64_t authenticated_sessions = 0;
    std::uint64_t active_rooms = 0;
    std::uint64_t active_battles = 0;
};

struct GatewayMetricsExportOptions {
    std::optional<std::filesystem::path> prometheus_path;
    std::optional<std::filesystem::path> json_path;
};

[[nodiscard]] GatewayRuntimeMetricsSnapshot collect_runtime_metrics(const GatewayMetrics& metrics,
                                                                   const SessionManager& session_manager,
                                                                   const game::room::RoomManager& room_manager,
                                                                   const game::battle::BattleManager& battle_manager);

[[nodiscard]] std::string render_prometheus_metrics(const GatewayRuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_json_metrics(const GatewayRuntimeMetricsSnapshot& snapshot);

bool write_metrics_files(const GatewayRuntimeMetricsSnapshot& snapshot,
                         const GatewayMetricsExportOptions& options);

}  // namespace game::gateway
