#include "game/gateway/gateway_metrics_exporter.h"

#include <filesystem>
#include <fstream>
#include <string>

#include <gtest/gtest.h>

TEST(GatewayMetricsExporterTest, RendersPrometheusAndJsonOutputs) {
    game::gateway::GatewayRuntimeMetricsSnapshot snapshot;
    snapshot.counters.accepted_sessions = 10;
    snapshot.counters.closed_sessions = 4;
    snapshot.counters.received_packets = 120;
    snapshot.counters.sent_packets = 118;
    snapshot.counters.received_bytes = 2048;
    snapshot.counters.sent_bytes = 1980;
    snapshot.counters.blocked_packets = 3;
    snapshot.counters.login_successes = 7;
    snapshot.counters.room_join_successes = 5;
    snapshot.counters.battle_start_successes = 2;
    snapshot.active_sessions = 6;
    snapshot.authenticated_sessions = 5;
    snapshot.active_rooms = 2;
    snapshot.active_battles = 1;
    snapshot.dispatch_back_tasks = 9;
    snapshot.dispatch_inline_fallbacks = 1;
    snapshot.maintenance_probe_tasks = 4;
    snapshot.prometheus_extension_text =
        "gateway_shadow_bridge_tracked_sessions 2\n";
    snapshot.json_extension_text =
        R"({"shadow_bridge":{"tracked_sessions":2,"active_sessions":1}})";
    snapshot.diagnostics_extension_text =
        "shadow_bridge emit_responses=true tracked_sessions=2 active_sessions=1 mirrored_packets=3\n";
    snapshot.diagnostics_extension_json_text =
        R"({"shadow_bridge":{"emit_responses":true,"tracked_sessions":2,"active_sessions":1}})";
    snapshot.io_cores.push_back({
        .core_id = 0,
        .active_sessions = 4,
        .accepted_sessions = 7,
        .dispatch_back_tasks = 8,
        .maintenance_probes = 4,
    });

    const auto prometheus = game::gateway::render_prometheus_metrics(snapshot);
    EXPECT_NE(prometheus.find("gateway_sessions_accepted_total 10"), std::string::npos);
    EXPECT_NE(prometheus.find("gateway_active_battles 1"), std::string::npos);
    EXPECT_NE(prometheus.find("gateway_io_core_active_sessions{core=\"0\"} 4"), std::string::npos);
    EXPECT_NE(prometheus.find("gateway_dispatch_back_tasks_total 9"), std::string::npos);
    EXPECT_NE(prometheus.find("gateway_io_core_maintenance_probes_total{core=\"0\"} 4"), std::string::npos);

    const auto json_text = game::gateway::render_json_metrics(snapshot);
    EXPECT_NE(json_text.find("\"accepted_sessions\": 10"), std::string::npos);
    EXPECT_NE(json_text.find("\"active_rooms\": 2"), std::string::npos);
    EXPECT_NE(json_text.find("\"core_id\": 0"), std::string::npos);
    EXPECT_NE(json_text.find("\"dispatch_back_tasks\": 9"), std::string::npos);
    EXPECT_NE(json_text.find("\"maintenance_probe_tasks\": 4"), std::string::npos);
    EXPECT_NE(json_text.find("\"extensions\""), std::string::npos);
    EXPECT_NE(json_text.find("\"shadow_bridge\""), std::string::npos);

    const auto diagnostics = game::gateway::render_diagnostics_metrics(snapshot);
    EXPECT_NE(diagnostics.find("gateway_diagnostics"), std::string::npos);
    EXPECT_NE(diagnostics.find("io_core id=0 active_sessions=4 accepted_sessions=7"), std::string::npos);
    EXPECT_NE(diagnostics.find("shadow_bridge emit_responses=true tracked_sessions=2"), std::string::npos);

    const auto diagnostics_json = game::gateway::render_diagnostics_json_metrics(snapshot);
    EXPECT_NE(diagnostics_json.find("\"summary\""), std::string::npos);
    EXPECT_NE(diagnostics_json.find("\"io_balance\""), std::string::npos);
    EXPECT_NE(diagnostics_json.find("\"maintenance_probes\": 4"), std::string::npos);
    EXPECT_NE(diagnostics_json.find("\"extensions\""), std::string::npos);
    EXPECT_NE(diagnostics_json.find("\"shadow_bridge\""), std::string::npos);
}

TEST(GatewayMetricsExporterTest, WritesMetricsFilesToDisk) {
    game::gateway::GatewayRuntimeMetricsSnapshot snapshot;
    snapshot.counters.accepted_sessions = 1;
    snapshot.active_sessions = 1;

    const auto temp_dir = std::filesystem::temp_directory_path() / "gateway_metrics_exporter_test";
    const auto prom_path = temp_dir / "gateway.prom";
    const auto json_path = temp_dir / "gateway.json";

    const auto ok = game::gateway::write_metrics_files(
        snapshot,
        {
            .prometheus_path = prom_path,
            .json_path = json_path,
        });
    EXPECT_TRUE(ok);
    EXPECT_TRUE(std::filesystem::exists(prom_path));
    EXPECT_TRUE(std::filesystem::exists(json_path));

    std::ifstream prom_input(prom_path);
    std::string prom_text((std::istreambuf_iterator<char>(prom_input)), std::istreambuf_iterator<char>());
    EXPECT_NE(prom_text.find("gateway_sessions_accepted_total 1"), std::string::npos);
    prom_input.close();

    std::filesystem::remove_all(temp_dir);
}
