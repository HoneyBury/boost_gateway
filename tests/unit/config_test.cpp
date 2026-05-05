#include "app/logging.h"
#include "app/config.h"

#include <filesystem>
#include <fstream>

#include <gtest/gtest.h>

TEST(ConfigTest, LoadsGatewayConfigFromFile) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "gateway_test.conf";
    {
        std::ofstream output(path);
        output << "gateway.port=9200\n";
        output << "gateway.io_threads=4\n";
        output << "gateway.business_threads=6\n";
        output << "gateway.metrics_log_interval_ms=7000\n";
        output << "session.max_packet_size=2048\n";
        output << "session.max_pending_write_bytes=4096\n";
        output << "session.heartbeat_check_interval_ms=150\n";
        output << "session.heartbeat_timeout_ms=600\n";
    }

    const auto config = app::config::load_gateway_config(path);
    EXPECT_EQ(config.port, 9200);
    EXPECT_EQ(config.io_threads, 4U);
    EXPECT_EQ(config.business_threads, 6U);
    EXPECT_EQ(config.metrics_log_interval, std::chrono::milliseconds(7000));
    EXPECT_EQ(config.session_max_packet_size, 2048U);
    EXPECT_EQ(config.session_max_pending_write_bytes, 4096U);
    EXPECT_EQ(config.session_heartbeat_check_interval, std::chrono::milliseconds(150));
    EXPECT_EQ(config.session_heartbeat_timeout, std::chrono::milliseconds(600));

    std::filesystem::remove(path);
}

TEST(ConfigTest, LoadsGatewayConfigFromJsonFile) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "gateway_test.json";
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": 9300,\n";
        output << "    \"io_threads\": 3,\n";
        output << "    \"business_threads\": 5,\n";
        output << "    \"metrics_log_interval_ms\": 2500,\n";
        output << "    \"metrics_prometheus_path\": \"runtime/test.prom\",\n";
        output << "    \"metrics_json_path\": \"runtime/test.json\",\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"json_file\",\n";
        output << "      \"users_path\": \"config/auth_users.json\"\n";
        output << "    }\n";
        output << "  },\n";
        output << "  \"session\": {\n";
        output << "    \"max_packet_size\": 4096,\n";
        output << "    \"max_pending_write_bytes\": 8192,\n";
        output << "    \"heartbeat_check_interval_ms\": 200,\n";
        output << "    \"heartbeat_timeout_ms\": 900\n";
        output << "  }\n";
        output << "}\n";
    }

    const auto config = app::config::load_gateway_config(path);
    EXPECT_EQ(config.port, 9300);
    EXPECT_EQ(config.io_threads, 3U);
    EXPECT_EQ(config.business_threads, 5U);
    EXPECT_EQ(config.metrics_log_interval, std::chrono::milliseconds(2500));
    ASSERT_TRUE(config.metrics_prometheus_path.has_value());
    ASSERT_TRUE(config.metrics_json_path.has_value());
    EXPECT_EQ(config.metrics_prometheus_path->generic_string(), "runtime/test.prom");
    EXPECT_EQ(config.metrics_json_path->generic_string(), "runtime/test.json");
    EXPECT_EQ(config.auth_provider, "json_file");
    ASSERT_TRUE(config.auth_users_path.has_value());
    EXPECT_EQ(config.auth_users_path->generic_string(), "config/auth_users.json");
    EXPECT_EQ(config.session_max_packet_size, 4096U);
    EXPECT_EQ(config.session_max_pending_write_bytes, 8192U);
    EXPECT_EQ(config.session_heartbeat_check_interval, std::chrono::milliseconds(200));
    EXPECT_EQ(config.session_heartbeat_timeout, std::chrono::milliseconds(900));

    std::filesystem::remove(path);
}

TEST(ConfigTest, LoadsPressureConfigFromJsonFile) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "pressure_test.json";
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"pressure\": {\n";
        output << "    \"host\": \"10.0.0.9\",\n";
        output << "    \"port\": 9400,\n";
        output << "    \"client_count\": 32,\n";
        output << "    \"echo_count_per_client\": 12,\n";
        output << "    \"scenario\": \"invalid_token\",\n";
        output << "    \"send_interval_ms\": 25,\n";
        output << "    \"invalid_token_every\": 3\n";
        output << "  }\n";
        output << "}\n";
    }

    const auto config = app::config::load_pressure_config(path);
    EXPECT_EQ(config.host, "10.0.0.9");
    EXPECT_EQ(config.port, 9400);
    EXPECT_EQ(config.client_count, 32U);
    EXPECT_EQ(config.messages_per_client, 12U);
    EXPECT_EQ(config.scenario, app::config::PressureScenario::kInvalidToken);
    EXPECT_EQ(config.send_interval, std::chrono::milliseconds(25));
    EXPECT_EQ(config.invalid_token_every, 3U);

    std::filesystem::remove(path);
}

TEST(ConfigTest, ParsesAllPressureScenarios) {
    EXPECT_EQ(app::config::parse_pressure_scenario("echo"), app::config::PressureScenario::kEcho);
    EXPECT_EQ(app::config::parse_pressure_scenario("invalid_token"), app::config::PressureScenario::kInvalidToken);
    EXPECT_EQ(app::config::parse_pressure_scenario("slow_echo"), app::config::PressureScenario::kSlowEcho);
    EXPECT_EQ(app::config::parse_pressure_scenario("broadcast_storm"), app::config::PressureScenario::kBroadcastStorm);
    EXPECT_EQ(app::config::parse_pressure_scenario("malicious_packet"), app::config::PressureScenario::kMaliciousPacket);
    EXPECT_EQ(app::config::parse_pressure_scenario("battle_broadcast"), app::config::PressureScenario::kBattleBroadcast);
    EXPECT_EQ(app::config::parse_pressure_scenario("unknown"), std::nullopt);
}

TEST(ConfigTest, LoadsPressureConfigWithRoomAndMaliciousOpts) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "pressure_full_test.json";
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"pressure\": {\n";
        output << "    \"host\": \"10.0.0.1\",\n";
        output << "    \"port\": 9500,\n";
        output << "    \"client_count\": 16,\n";
        output << "    \"messages_per_client\": 20,\n";
        output << "    \"scenario\": \"broadcast_storm\",\n";
        output << "    \"room_name\": \"test_room\",\n";
        output << "    \"malicious_packet_size\": 5242880\n";
        output << "  }\n";
        output << "}\n";
    }

    const auto config = app::config::load_pressure_config(path);
    EXPECT_EQ(config.messages_per_client, 20U);
    EXPECT_EQ(config.scenario, app::config::PressureScenario::kBroadcastStorm);
    EXPECT_EQ(config.room_name, "test_room");
    EXPECT_EQ(config.malicious_packet_size, 5242880U);

    std::filesystem::remove(path);
}
