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

    const auto prometheus = game::gateway::render_prometheus_metrics(snapshot);
    EXPECT_NE(prometheus.find("gateway_sessions_accepted_total 10"), std::string::npos);
    EXPECT_NE(prometheus.find("gateway_active_battles 1"), std::string::npos);

    const auto json_text = game::gateway::render_json_metrics(snapshot);
    EXPECT_NE(json_text.find("\"accepted_sessions\": 10"), std::string::npos);
    EXPECT_NE(json_text.find("\"active_rooms\": 2"), std::string::npos);
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
