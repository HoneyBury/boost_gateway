#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <unordered_map>

namespace app::config {

class ConfigStore {
public:
    bool load(const std::filesystem::path& path);

    [[nodiscard]] std::optional<std::string> get_string(const std::string& key) const;
    [[nodiscard]] std::optional<std::uint16_t> get_uint16(const std::string& key) const;
    [[nodiscard]] std::optional<std::uint32_t> get_uint32(const std::string& key) const;
    [[nodiscard]] std::optional<std::size_t> get_size(const std::string& key) const;
    [[nodiscard]] std::optional<std::chrono::milliseconds> get_milliseconds(const std::string& key) const;

private:
    std::unordered_map<std::string, std::string> values_;
};

struct GatewayAppConfig {
    std::uint16_t port = 9000;
    std::uint16_t http_management_port = 9080;
    std::size_t io_threads = 2;
    std::size_t business_threads = 2;
    std::chrono::milliseconds metrics_log_interval{5000};
    std::optional<std::filesystem::path> metrics_prometheus_path;
    std::optional<std::filesystem::path> metrics_json_path;
    std::string auth_provider = "dev";
    std::optional<std::filesystem::path> auth_users_path;
    std::string auth_http_endpoint = "http://127.0.0.1:8080/auth/validate";
    std::chrono::milliseconds auth_http_timeout{3000};
    std::size_t max_connections = 10000;
    std::size_t max_guests = 500;              // max concurrent guest sessions
    std::size_t per_ip_connection_limit = 0;  // 0 = disabled
    std::uint32_t session_max_packet_size = 1024 * 1024;
    std::size_t session_max_pending_write_bytes = 256 * 1024;
    std::chrono::milliseconds session_heartbeat_check_interval{5000};
    std::chrono::milliseconds session_heartbeat_timeout{30000};
};

enum class PressureScenario {
    kEcho,
    kInvalidToken,
    kSlowEcho,
    kBroadcastStorm,
    kMaliciousPacket,
    kBattleBroadcast,
    kChaos,
    kStability,
};

struct PressureAppConfig {
    std::string host = "127.0.0.1";
    std::uint16_t port = 9000;
    std::size_t client_count = 100;
    std::size_t messages_per_client = 10;
    PressureScenario scenario = PressureScenario::kEcho;
    std::chrono::milliseconds send_interval{0};
    std::size_t invalid_token_every = 5;
    std::string room_name = "pressure_room";
    std::uint32_t malicious_packet_size = 2 * 1024 * 1024;
};

[[nodiscard]] GatewayAppConfig load_gateway_config(const std::filesystem::path& path);
[[nodiscard]] PressureAppConfig load_pressure_config(const std::filesystem::path& path);
[[nodiscard]] std::string to_string(PressureScenario scenario);
[[nodiscard]] std::optional<PressureScenario> parse_pressure_scenario(const std::string& value);

}  // namespace app::config
