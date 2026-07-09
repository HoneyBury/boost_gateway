#pragma once

#include <filesystem>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

namespace v2::diagnostics {

struct RuntimeMetricsCounterSnapshot {
    std::uint64_t accepted_sessions = 0;
    std::uint64_t closed_sessions = 0;
    std::uint64_t received_packets = 0;
    std::uint64_t sent_packets = 0;
    std::uint64_t received_bytes = 0;
    std::uint64_t sent_bytes = 0;
    std::uint64_t blocked_packets = 0;
    std::uint64_t login_successes = 0;
    std::uint64_t room_join_successes = 0;
    std::uint64_t battle_start_successes = 0;
};

struct RuntimeMetricsRateSnapshot {
    double accepted_sessions_per_sec = 0.0;
    double closed_sessions_per_sec = 0.0;
    double received_packets_per_sec = 0.0;
    double sent_packets_per_sec = 0.0;
    double received_bytes_per_sec = 0.0;
    double sent_bytes_per_sec = 0.0;
    double blocked_packets_per_sec = 0.0;
    double login_successes_per_sec = 0.0;
    double room_join_successes_per_sec = 0.0;
    double battle_start_successes_per_sec = 0.0;
};

struct RuntimeIoCoreSnapshot {
    std::uint32_t core_id = 0;
    std::uint64_t active_sessions = 0;
    std::uint64_t accepted_sessions = 0;
    std::uint64_t dispatch_back_tasks = 0;
    std::uint64_t maintenance_probes = 0;
};

struct RuntimeMetricsSnapshot {
    RuntimeMetricsCounterSnapshot counters;
    RuntimeMetricsRateSnapshot rates;
    std::uint64_t active_sessions = 0;
    std::uint64_t authenticated_sessions = 0;
    std::uint64_t active_rooms = 0;
    std::uint64_t active_battles = 0;
    std::uint64_t dispatch_back_tasks = 0;
    std::uint64_t dispatch_inline_fallbacks = 0;
    std::uint64_t maintenance_probe_tasks = 0;
    std::vector<RuntimeIoCoreSnapshot> io_cores;
    std::string prometheus_extension_text;
    std::string json_extension_text;
    std::string diagnostics_extension_text;
    std::string diagnostics_extension_json_text;
};

struct RuntimeMetricsExportOptions {
    std::optional<std::filesystem::path> prometheus_path;
    std::optional<std::filesystem::path> json_path;
};

[[nodiscard]] std::string render_prometheus_metrics(const RuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_json_metrics(const RuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_diagnostics_metrics(const RuntimeMetricsSnapshot& snapshot);
[[nodiscard]] std::string render_diagnostics_json_metrics(const RuntimeMetricsSnapshot& snapshot);

bool write_metrics_files(const RuntimeMetricsSnapshot& snapshot,
                         const RuntimeMetricsExportOptions& options);

}  // namespace v2::diagnostics
