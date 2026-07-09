#include "v2/diagnostics/runtime_metrics_exporter.h"

#include <fmt/format.h>
#include <nlohmann/json.hpp>

#include <cmath>
#include <filesystem>
#include <fstream>
#include <limits>

namespace v2::diagnostics {
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

std::string render_prometheus_metrics(const RuntimeMetricsSnapshot& snapshot) {
    const auto r = snapshot.rates;
    auto text = fmt::format(
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
        "gateway_active_battles {}\n"
        "# TYPE gateway_dispatch_back_tasks_total counter\n"
        "gateway_dispatch_back_tasks_total {}\n"
        "# TYPE gateway_dispatch_inline_fallbacks_total counter\n"
        "gateway_dispatch_inline_fallbacks_total {}\n"
        "# TYPE gateway_io_core_maintenance_probes_total counter\n"
        "gateway_io_core_maintenance_probes_total {}\n"
        "# TYPE gateway_sessions_accepted_rate gauge\n"
        "gateway_sessions_accepted_rate {:.2f}\n"
        "# TYPE gateway_packets_received_rate gauge\n"
        "gateway_packets_received_rate {:.2f}\n"
        "# TYPE gateway_packets_sent_rate gauge\n"
        "gateway_packets_sent_rate {:.2f}\n"
        "# TYPE gateway_bytes_received_rate gauge\n"
        "gateway_bytes_received_rate {:.2f}\n"
        "# TYPE gateway_bytes_sent_rate gauge\n"
        "gateway_bytes_sent_rate {:.2f}\n"
        "# TYPE gateway_login_success_rate gauge\n"
        "gateway_login_success_rate {:.2f}\n",
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
        snapshot.active_battles,
        snapshot.dispatch_back_tasks,
        snapshot.dispatch_inline_fallbacks,
        snapshot.maintenance_probe_tasks,
        r.accepted_sessions_per_sec,
        r.received_packets_per_sec,
        r.sent_packets_per_sec,
        r.received_bytes_per_sec,
        r.sent_bytes_per_sec,
        r.login_successes_per_sec);

    if (!snapshot.io_cores.empty()) {
        text += "# TYPE gateway_io_core_active_sessions gauge\n";
        text += "# TYPE gateway_io_core_accepted_sessions_total counter\n";
        for (const auto& core : snapshot.io_cores) {
            text += fmt::format("gateway_io_core_active_sessions{{core=\"{}\"}} {}\n",
                                core.core_id,
                                core.active_sessions);
            text += fmt::format("gateway_io_core_accepted_sessions_total{{core=\"{}\"}} {}\n",
                                core.core_id,
                                core.accepted_sessions);
            text += fmt::format("gateway_io_core_dispatch_back_tasks_total{{core=\"{}\"}} {}\n",
                                core.core_id,
                                core.dispatch_back_tasks);
            text += fmt::format("gateway_io_core_maintenance_probes_total{{core=\"{}\"}} {}\n",
                                core.core_id,
                                core.maintenance_probes);
        }
    }

    if (!snapshot.prometheus_extension_text.empty()) {
        text += snapshot.prometheus_extension_text;
        if (!text.empty() && text.back() != '\n') {
            text += '\n';
        }
    }

    return text;
}

std::string render_json_metrics(const RuntimeMetricsSnapshot& snapshot) {
    json io_cores = json::array();
    for (const auto& core : snapshot.io_cores) {
        io_cores.push_back({
            {"core_id", core.core_id},
            {"active_sessions", core.active_sessions},
            {"accepted_sessions", core.accepted_sessions},
            {"dispatch_back_tasks", core.dispatch_back_tasks},
            {"maintenance_probes", core.maintenance_probes},
        });
    }

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
        {"dispatch_back_tasks", snapshot.dispatch_back_tasks},
        {"dispatch_inline_fallbacks", snapshot.dispatch_inline_fallbacks},
        {"maintenance_probe_tasks", snapshot.maintenance_probe_tasks},
        {"io_cores", std::move(io_cores)},
        {"sessions_accepted_per_sec", std::round(snapshot.rates.accepted_sessions_per_sec * 100.0) / 100.0},
        {"packets_received_per_sec", std::round(snapshot.rates.received_packets_per_sec * 100.0) / 100.0},
        {"packets_sent_per_sec", std::round(snapshot.rates.sent_packets_per_sec * 100.0) / 100.0},
        {"bytes_received_per_sec", std::round(snapshot.rates.received_bytes_per_sec * 100.0) / 100.0},
        {"bytes_sent_per_sec", std::round(snapshot.rates.sent_bytes_per_sec * 100.0) / 100.0},
        {"login_successes_per_sec", std::round(snapshot.rates.login_successes_per_sec * 100.0) / 100.0},
    };

    if (!snapshot.json_extension_text.empty()) {
        const auto extension = json::parse(snapshot.json_extension_text, nullptr, false);
        if (!extension.is_discarded()) {
            document["extensions"] = extension;
        }
    }
    return document.dump(2);
}

std::string render_diagnostics_metrics(const RuntimeMetricsSnapshot& snapshot) {
    auto text = fmt::format(
        "gateway_diagnostics\n"
        "active_sessions={}\n"
        "authenticated_sessions={}\n"
        "active_rooms={}\n"
        "active_battles={}\n"
        "dispatch_back_tasks={}\n"
        "dispatch_inline_fallbacks={}\n"
        "maintenance_probe_tasks={}\n"
        "io_core_count={}\n",
        snapshot.active_sessions,
        snapshot.authenticated_sessions,
        snapshot.active_rooms,
        snapshot.active_battles,
        snapshot.dispatch_back_tasks,
        snapshot.dispatch_inline_fallbacks,
        snapshot.maintenance_probe_tasks,
        snapshot.io_cores.size());

    if (snapshot.io_cores.empty()) {
        text += "io_cores=none\n";
        return text;
    }

    std::uint64_t total_active_sessions = 0;
    std::uint64_t total_accepted_sessions = 0;
    std::uint64_t max_active_sessions = 0;
    std::uint64_t min_active_sessions = std::numeric_limits<std::uint64_t>::max();
    std::uint32_t busiest_core = 0;
    std::uint32_t idle_cores = 0;

    for (const auto& core : snapshot.io_cores) {
        total_active_sessions += core.active_sessions;
        total_accepted_sessions += core.accepted_sessions;
        if (core.active_sessions > max_active_sessions) {
            max_active_sessions = core.active_sessions;
            busiest_core = core.core_id;
        }
        min_active_sessions = std::min(min_active_sessions, core.active_sessions);
        if (core.active_sessions == 0) {
            ++idle_cores;
        }
    }

    text += fmt::format(
        "io_balance total_active_sessions={} total_accepted_sessions={} busiest_core={} max_active_sessions={} "
        "min_active_sessions={} idle_cores={}\n",
        total_active_sessions,
        total_accepted_sessions,
        busiest_core,
        max_active_sessions,
        min_active_sessions == std::numeric_limits<std::uint64_t>::max() ? 0 : min_active_sessions,
        idle_cores);

    for (const auto& core : snapshot.io_cores) {
        const auto active_ratio = total_active_sessions == 0
            ? 0.0
            : (static_cast<double>(core.active_sessions) * 100.0 /
               static_cast<double>(total_active_sessions));
        text += fmt::format(
            "io_core id={} active_sessions={} accepted_sessions={} dispatch_back_tasks={} maintenance_probes={} "
            "active_ratio_pct={:.1f}\n",
            core.core_id,
            core.active_sessions,
            core.accepted_sessions,
            core.dispatch_back_tasks,
            core.maintenance_probes,
            active_ratio);
    }

    if (!snapshot.diagnostics_extension_text.empty()) {
        text += snapshot.diagnostics_extension_text;
        if (!text.empty() && text.back() != '\n') {
            text += '\n';
        }
    }

    return text;
}

std::string render_diagnostics_json_metrics(const RuntimeMetricsSnapshot& snapshot) {
    json cores = json::array();
    std::uint64_t total_active_sessions = 0;
    std::uint64_t total_accepted_sessions = 0;
    std::uint64_t max_active_sessions = 0;
    std::uint64_t min_active_sessions = snapshot.io_cores.empty()
        ? 0
        : std::numeric_limits<std::uint64_t>::max();
    std::uint32_t busiest_core = 0;
    std::uint32_t idle_cores = 0;

    for (const auto& core : snapshot.io_cores) {
        total_active_sessions += core.active_sessions;
        total_accepted_sessions += core.accepted_sessions;
        if (core.active_sessions > max_active_sessions) {
            max_active_sessions = core.active_sessions;
            busiest_core = core.core_id;
        }
        min_active_sessions = std::min(min_active_sessions, core.active_sessions);
        if (core.active_sessions == 0) {
            ++idle_cores;
        }
        cores.push_back({
            {"core_id", core.core_id},
            {"active_sessions", core.active_sessions},
            {"accepted_sessions", core.accepted_sessions},
            {"dispatch_back_tasks", core.dispatch_back_tasks},
            {"maintenance_probes", core.maintenance_probes},
            {"active_ratio_pct", total_active_sessions == 0 ? 0.0 : 0.0},
        });
    }

    for (auto& core : cores) {
        const auto active_sessions = core.at("active_sessions").get<std::uint64_t>();
        core["active_ratio_pct"] = total_active_sessions == 0
            ? 0.0
            : std::round((static_cast<double>(active_sessions) * 10000.0 /
                          static_cast<double>(total_active_sessions))) / 100.0;
    }

    json document = {
        {"summary", {
            {"active_sessions", snapshot.active_sessions},
            {"authenticated_sessions", snapshot.authenticated_sessions},
            {"active_rooms", snapshot.active_rooms},
            {"active_battles", snapshot.active_battles},
            {"dispatch_back_tasks", snapshot.dispatch_back_tasks},
            {"dispatch_inline_fallbacks", snapshot.dispatch_inline_fallbacks},
            {"maintenance_probe_tasks", snapshot.maintenance_probe_tasks},
            {"io_core_count", snapshot.io_cores.size()},
        }},
        {"io_balance", {
            {"total_active_sessions", total_active_sessions},
            {"total_accepted_sessions", total_accepted_sessions},
            {"busiest_core", busiest_core},
            {"max_active_sessions", max_active_sessions},
            {"min_active_sessions", min_active_sessions},
            {"idle_cores", idle_cores},
        }},
        {"io_cores", std::move(cores)},
    };

    if (!snapshot.diagnostics_extension_json_text.empty()) {
        const auto extension = json::parse(snapshot.diagnostics_extension_json_text, nullptr, false);
        if (!extension.is_discarded()) {
            document["extensions"] = extension;
        }
    }

    return document.dump(2);
}

bool write_metrics_files(const RuntimeMetricsSnapshot& snapshot,
                         const RuntimeMetricsExportOptions& options) {
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

}  // namespace v2::diagnostics
