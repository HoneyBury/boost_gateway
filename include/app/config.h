#pragma once

#include "net/tls_config.h"
#include "v3/cluster/tls_config.h"

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <vector>
#include <unordered_map>

namespace app::config {

class ConfigStore {
public:
    bool load(const std::filesystem::path& path);

    [[nodiscard]] std::optional<std::string> get_string(const std::string& key) const;
    [[nodiscard]] std::optional<bool> get_bool(const std::string& key) const;
    [[nodiscard]] std::optional<std::uint16_t> get_uint16(const std::string& key) const;
    [[nodiscard]] std::optional<std::uint32_t> get_uint32(const std::string& key) const;
    [[nodiscard]] std::optional<std::size_t> get_size(const std::string& key) const;
    [[nodiscard]] std::optional<std::chrono::milliseconds> get_milliseconds(const std::string& key) const;
    [[nodiscard]] std::unordered_map<std::string, std::string> get_prefixed(
        const std::string& prefix) const;

private:
    std::unordered_map<std::string, std::string> values_;
};

struct GatewayAppConfig {
    std::uint16_t port = 9000;
    std::uint16_t http_management_port = 9080;
    std::size_t io_threads = 2;
    std::vector<std::uint16_t> io_listener_ports;
    std::vector<std::uint32_t> io_listener_core_ids;
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
    bool v2_shadow_bridge_enabled = false;
    bool v2_shadow_bridge_emit_responses = false;
    bool v2_shadow_bridge_login = true;
    bool v2_shadow_bridge_room = true;
    bool v2_shadow_bridge_battle = true;
    bool v2_shadow_bridge_echo = false;
    bool v2_shadow_bridge_emit_battle_input_push = true;
    bool v2_shadow_bridge_emit_battle_state_started = true;
    bool v2_shadow_bridge_emit_battle_state_frame = true;
    bool v2_shadow_bridge_emit_battle_state_settlement = true;
    bool v2_shadow_bridge_emit_battle_state_finished = true;
    std::uint32_t session_max_packet_size = 1024 * 1024;
    std::size_t session_max_pending_write_bytes = 256 * 1024;
    std::chrono::milliseconds session_heartbeat_check_interval{5000};
    std::chrono::milliseconds session_heartbeat_timeout{30000};
    net::TlsConfig tls;
};

struct JwtServiceConfig {
    std::string mode = "dev";
    std::string secret;
    std::string public_key_pem;
    std::string private_key_pem;
    std::unordered_map<std::string, std::string> key_ring;
    std::string jwks_uri;
    std::vector<std::string> jwks_allowed_hosts;
    bool jwks_allow_loopback_http = false;
    std::chrono::milliseconds jwks_connect_timeout{2000};
    std::chrono::milliseconds jwks_read_timeout{3000};
    std::chrono::milliseconds jwks_ttl{300000};
    std::chrono::milliseconds jwks_stale_grace{900000};
    std::chrono::milliseconds jwks_minimum_refresh_interval{30000};
    std::size_t jwks_max_response_bytes = 1024U * 1024U;
    std::size_t jwks_max_keys = 32;
    std::string issuer = "boost-gateway";
    std::string audience;
};

struct RedisServiceConfig {
    std::string host;
    std::uint16_t port = 6379;
    std::string password;
    std::string leaderboard_key = "lb:global";
    std::size_t pool_size = 3;
};

struct RaftPeerConfig {
    std::string id;
    std::string host;
    std::uint16_t port = 0;
};

struct RaftServiceConfig {
    std::string node_id;
    std::string storage_dir;
    std::chrono::milliseconds election_timeout_min{150};
    std::chrono::milliseconds election_timeout_max{300};
    std::chrono::milliseconds heartbeat_interval{50};
    bool protobuf_writer_enabled = false;
    std::vector<RaftPeerConfig> peers;
};

struct BackendServiceConfig {
    std::string service_name;
    std::string config_path;
    std::string config_version = "local";
    std::uint16_t port = 0;
    JwtServiceConfig jwt;
    RedisServiceConfig redis;
    RaftServiceConfig raft;
    std::optional<std::uint32_t> battle_max_frames;
    std::string archive_path;
    std::optional<v3::cluster::TlsSessionConfig> tls_config;
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
    kBenchmark,
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

[[nodiscard]] std::filesystem::path resolve_backend_config_path(
    const std::string& service_name,
    int argc,
    char* argv[],
    std::filesystem::path fallback_path);
[[nodiscard]] BackendServiceConfig load_backend_service_config(
    const std::string& service_name,
    const std::filesystem::path& path,
    std::uint16_t default_port);
[[nodiscard]] std::optional<GatewayAppConfig> try_load_gateway_config(const std::filesystem::path& path);
[[nodiscard]] GatewayAppConfig load_gateway_config(const std::filesystem::path& path);
[[nodiscard]] PressureAppConfig load_pressure_config(const std::filesystem::path& path);
[[nodiscard]] std::string to_string(PressureScenario scenario);
[[nodiscard]] std::optional<PressureScenario> parse_pressure_scenario(const std::string& value);

}  // namespace app::config
