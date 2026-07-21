#include "app/config.h"

#include "app/logging.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cctype>
#include <charconv>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <optional>
#include <sstream>
#include <string_view>

namespace app::config {
namespace {

using json = nlohmann::json;

std::string trim(std::string value) {
    const auto not_space = [](unsigned char ch) { return !std::isspace(ch); };
    value.erase(value.begin(),
                std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

template <typename T>
std::optional<T> parse_integer(std::string_view value) {
    T parsed{};
    const auto* begin = value.data();
    const auto* end = value.data() + value.size();
    const auto result = std::from_chars(begin, end, parsed);
    if (result.ec != std::errc{} || result.ptr != end) {
        return std::nullopt;
    }
    return parsed;
}

std::optional<bool> parse_bool(std::string_view value) {
    if (value == "1" || value == "true" || value == "TRUE" || value == "on" || value == "yes") {
        return true;
    }
    if (value == "0" || value == "false" || value == "FALSE" || value == "off" || value == "no") {
        return false;
    }
    return std::nullopt;
}

template <typename T>
std::vector<T> parse_integer_list(std::string_view value) {
    std::vector<T> parsed_values;
    if (value.empty()) {
        return parsed_values;
    }

    const auto text = trim(std::string(value));
    if (text.empty()) {
        return parsed_values;
    }

    try {
        if (!text.empty() && text.front() == '[') {
            const auto doc = json::parse(text);
            if (doc.is_array()) {
                for (const auto& item : doc) {
                    if (item.is_number_unsigned()) {
                        parsed_values.push_back(item.get<T>());
                    }
                }
                return parsed_values;
            }
        }
    } catch (const std::exception&) {
    }

    std::stringstream stream(text);
    std::string token;
    while (std::getline(stream, token, ',')) {
        token = trim(token);
        if (token.empty()) {
            continue;
        }
        if (const auto parsed = parse_integer<T>(token)) {
            parsed_values.push_back(*parsed);
        }
    }
    return parsed_values;
}

void flatten_json_object(const json& value,
                         const std::string& prefix,
                         std::unordered_map<std::string, std::string>& output) {
    if (value.is_object()) {
        for (const auto& [key, child] : value.items()) {
            const auto next_prefix = prefix.empty() ? key : prefix + "." + key;
            flatten_json_object(child, next_prefix, output);
        }
        return;
    }

    if (value.is_array()) {
        output[prefix] = value.dump();
        return;
    }

    if (value.is_string()) {
        output[prefix] = value.get<std::string>();
        return;
    }

    if (value.is_boolean()) {
        output[prefix] = value.get<bool>() ? "true" : "false";
        return;
    }

    output[prefix] = value.dump();
}

bool load_key_value_file(const std::filesystem::path& path,
                         std::unordered_map<std::string, std::string>& output) {
    std::ifstream input(path);
    if (!input.is_open()) {
        return false;
    }

    std::string line;
    while (std::getline(input, line)) {
        line = trim(line);
        if (line.empty() || line.starts_with('#')) {
            continue;
        }

        const auto separator = line.find('=');
        if (separator == std::string::npos) {
            continue;
        }

        auto key = trim(line.substr(0, separator));
        auto value = trim(line.substr(separator + 1));
        if (!key.empty()) {
            output[std::move(key)] = std::move(value);
        }
    }

    return true;
}

bool load_json_file(const std::filesystem::path& path,
                    std::unordered_map<std::string, std::string>& output) {
    std::ifstream input(path);
    if (!input.is_open()) {
        return false;
    }

    json document;
    input >> document;
    flatten_json_object(document, "", output);
    return true;
}

std::optional<std::filesystem::path> get_path_value(const ConfigStore& store, const std::string& key) {
    const auto value = store.get_string(key);
    if (!value || value->empty()) {
        return std::nullopt;
    }
    return std::filesystem::path(*value);
}

std::optional<std::string> env_value(const std::string& name) {
    const char* value = std::getenv(name.c_str());
    if (value == nullptr || value[0] == '\0') {
        return std::nullopt;
    }
    return std::string(value);
}

std::uint16_t read_uint16_or(std::optional<std::string> value, std::uint16_t fallback) {
    if (!value) {
        return fallback;
    }
    if (const auto parsed = parse_integer<std::uint16_t>(*value)) {
        return *parsed;
    }
    return fallback;
}

std::optional<std::uint32_t> read_uint32(std::optional<std::string> value) {
    if (!value) {
        return std::nullopt;
    }
    return parse_integer<std::uint32_t>(*value);
}

std::chrono::milliseconds read_milliseconds_or(std::optional<std::string> value,
                                               std::chrono::milliseconds fallback) {
    if (!value) {
        return fallback;
    }
    if (const auto parsed = parse_integer<std::uint64_t>(*value)) {
        return std::chrono::milliseconds(*parsed);
    }
    return fallback;
}

std::vector<RaftPeerConfig> parse_raft_peers(std::string_view raw) {
    std::vector<RaftPeerConfig> peers;
    const auto text = trim(std::string(raw));
    if (text.empty()) {
        return peers;
    }

    try {
        if (text.front() == '[') {
            const auto doc = json::parse(text);
            if (doc.is_array()) {
                for (const auto& item : doc) {
                    if (!item.is_object()) {
                        continue;
                    }
                    const auto id = item.value("id", std::string{});
                    const auto host = item.value("host", std::string{});
                    const auto port = item.value("port", std::uint16_t{0});
                    if (!id.empty() && !host.empty() && port > 0) {
                        peers.push_back(RaftPeerConfig{.id = id, .host = host, .port = port});
                    }
                }
                return peers;
            }
        }
    } catch (const std::exception&) {
    }

    std::stringstream stream(text);
    std::string token;
    while (std::getline(stream, token, ',')) {
        token = trim(token);
        const auto at = token.find('@');
        const auto colon = token.rfind(':');
        if (at == std::string::npos || colon == std::string::npos || colon <= at + 1) {
            continue;
        }
        const auto port = parse_integer<std::uint16_t>(token.substr(colon + 1));
        if (!port) {
            continue;
        }
        peers.push_back(RaftPeerConfig{
            .id = token.substr(0, at),
            .host = token.substr(at + 1, colon - at - 1),
            .port = *port,
        });
    }
    return peers;
}

void fill_backend_from_store(const ConfigStore& store, BackendServiceConfig& config) {
    if (const auto value = store.get_string("service.name")) {
        config.service_name = *value;
    }
    if (const auto value = store.get_string("service.config_version")) {
        config.config_version = *value;
    }
    if (const auto value = store.get_uint16("service.port")) {
        config.port = *value;
    }
    if (const auto value = store.get_string("auth.mode")) {
        config.jwt.mode = *value;
    }
    if (const auto value = store.get_string("auth.jwt_secret")) {
        config.jwt.secret = *value;
    }
    if (const auto value = store.get_string("auth.jwt_public_key_pem")) {
        config.jwt.public_key_pem = *value;
    }
    if (const auto value = store.get_string("auth.jwt_private_key_pem")) {
        config.jwt.private_key_pem = *value;
    }
    if (const auto value = store.get_string("auth.jwt_issuer")) {
        config.jwt.issuer = *value;
    }
    if (const auto value = store.get_string("auth.jwt_audience")) {
        config.jwt.audience = *value;
    }
    if (const auto value = store.get_string("redis.host")) {
        config.redis.host = *value;
    }
    if (const auto value = store.get_uint16("redis.port")) {
        config.redis.port = *value;
    }
    if (const auto value = store.get_string("redis.password")) {
        config.redis.password = *value;
    }
    if (const auto value = store.get_string("redis.leaderboard_key")) {
        config.redis.leaderboard_key = *value;
    }
    if (const auto value = store.get_size("redis.pool_size")) {
        config.redis.pool_size = *value;
    }
    if (const auto value = store.get_string("raft.node_id")) {
        config.raft.node_id = *value;
    }
    if (const auto value = store.get_string("raft.storage_dir")) {
        config.raft.storage_dir = *value;
    }
    if (const auto value = store.get_milliseconds("raft.election_timeout_min_ms")) {
        config.raft.election_timeout_min = *value;
    }
    if (const auto value = store.get_milliseconds("raft.election_timeout_max_ms")) {
        config.raft.election_timeout_max = *value;
    }
    if (const auto value = store.get_milliseconds("raft.heartbeat_interval_ms")) {
        config.raft.heartbeat_interval = *value;
    }
    if (const auto value = store.get_bool("raft.protobuf_writer_enabled")) {
        config.raft.protobuf_writer_enabled = *value;
    }
    if (const auto value = store.get_string("raft.peers")) {
        config.raft.peers = parse_raft_peers(*value);
    }
    if (const auto value = store.get_uint32("battle.max_frames")) {
        config.battle_max_frames = *value;
    }
    if (const auto value = store.get_string("battle.archive_path")) {
        config.archive_path = *value;
    }
    if (store.get_bool("tls.enabled").value_or(false)) {
        auto tls = v3::cluster::default_tls_config();
        if (const auto value = store.get_string("tls.cert_chain_path")) {
            tls.cert.cert_chain_path = *value;
        }
        if (const auto value = store.get_string("tls.private_key_path")) {
            tls.cert.private_key_path = *value;
        }
        if (const auto value = store.get_string("tls.ca_cert_path")) {
            tls.cert.ca_cert_path = *value;
        }
        if (const auto value = store.get_string("tls.verify_mode")) {
            if (*value == "none") {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kNone;
            } else if (*value == "server") {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kServer;
            } else if (*value == "mutual") {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kMutual;
            }
        }
        if (const auto value = store.get_string("tls.min_version")) {
            if (*value == "1.3" || *value == "tls1.3") {
                tls.min_version = v3::cluster::TlsSessionConfig::TlsVersion::k13;
            }
        }
        config.tls_config = std::move(tls);
    }
}

void apply_backend_env_overlay(const std::string& service_name, BackendServiceConfig& config) {
    const auto upper_service = [&service_name] {
        std::string value;
        value.reserve(service_name.size());
        for (const char ch : service_name) {
            value.push_back(static_cast<char>(
                ch == '-' ? '_' : std::toupper(static_cast<unsigned char>(ch))));
        }
        return value;
    }();

    config.port = read_uint16_or(env_value(upper_service + "_PORT"), config.port);
    if (service_name == "matchmaking") {
        config.port = read_uint16_or(env_value("MATCH_PORT"), config.port);
    }
    if (service_name == "leaderboard") {
        config.port = read_uint16_or(env_value("LEADERBOARD_PORT"), config.port);
    }
    config.port = read_uint16_or(env_value("SERVICE_PORT"), config.port);
    auto tls_enabled = env_value("BACKEND_TLS_ENABLED");
    if (!tls_enabled) {
        tls_enabled = env_value("SERVICE_TLS_ENABLED");
    }
    if (tls_enabled && parse_bool(*tls_enabled).value_or(false)) {
        auto tls = config.tls_config.value_or(v3::cluster::default_tls_config());
        if (const auto value = env_value("BACKEND_TLS_CERT_CHAIN_PATH")) {
            tls.cert.cert_chain_path = *value;
        }
        if (const auto value = env_value("BACKEND_TLS_PRIVATE_KEY_PATH")) {
            tls.cert.private_key_path = *value;
        }
        if (const auto value = env_value("BACKEND_TLS_CA_CERT_PATH")) {
            tls.cert.ca_cert_path = *value;
        }
        if (const auto value = env_value("BACKEND_TLS_VERIFY_MODE")) {
            if (*value == "none") {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kNone;
            } else if (*value == "server") {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kServer;
            } else if (*value == "mutual") {
                tls.verify_mode = v3::cluster::TlsVerifyMode::kMutual;
            }
        }
        if (const auto value = env_value("BACKEND_TLS_MIN_VERSION")) {
            if (*value == "1.3" || *value == "tls1.3") {
                tls.min_version = v3::cluster::TlsSessionConfig::TlsVersion::k13;
            }
        }
        config.tls_config = std::move(tls);
    }

    if (const auto value = env_value("V2_LOGIN_AUTH_MODE")) {
        config.jwt.mode = *value;
    }
    if (const auto value = env_value("V2_LOGIN_JWT_SECRET")) {
        config.jwt.secret = *value;
    }
    if (const auto value = env_value("V2_LOGIN_JWT_PUBLIC_KEY")) {
        config.jwt.public_key_pem = *value;
    }
    if (const auto value = env_value("V2_LOGIN_JWT_PRIVATE_KEY")) {
        config.jwt.private_key_pem = *value;
    }
    if (const auto value = env_value("V2_LOGIN_JWT_ISSUER")) {
        config.jwt.issuer = *value;
    }
    if (const auto value = env_value("V2_LOGIN_JWT_AUDIENCE")) {
        config.jwt.audience = *value;
    }

    if (const auto value = env_value("REDIS_HOST")) {
        config.redis.host = *value;
    }
    config.redis.port = read_uint16_or(env_value("REDIS_PORT"), config.redis.port);
    if (const auto value = env_value("REDIS_PASSWORD")) {
        config.redis.password = *value;
    }
    if (const auto value = env_value("REDIS_LEADERBOARD_KEY")) {
        config.redis.leaderboard_key = *value;
    }
    if (const auto value = env_value("REDIS_POOL_SIZE")) {
        config.redis.pool_size = static_cast<std::size_t>(std::stoul(*value));
    }

    if (const auto value = env_value("RAFT_NODE_ID")) {
        config.raft.node_id = *value;
    }
    if (const auto value = env_value("RAFT_PEERS")) {
        config.raft.peers = parse_raft_peers(*value);
    }
    if (const auto value = env_value("RAFT_STORAGE_DIR")) {
        config.raft.storage_dir = *value;
    }
    config.raft.election_timeout_min = read_milliseconds_or(
        env_value("RAFT_ELECTION_TIMEOUT_MIN_MS"), config.raft.election_timeout_min);
    config.raft.election_timeout_max = read_milliseconds_or(
        env_value("RAFT_ELECTION_TIMEOUT_MAX_MS"), config.raft.election_timeout_max);
    config.raft.heartbeat_interval = read_milliseconds_or(
        env_value("RAFT_HEARTBEAT_INTERVAL_MS"), config.raft.heartbeat_interval);
    if (const auto value = env_value("RAFT_PROTOBUF_WRITER_ENABLED")) {
        config.raft.protobuf_writer_enabled = parse_bool(*value).value_or(false);
    }

    if (const auto value = read_uint32(env_value("V2_BATTLE_MAX_FRAMES"))) {
        config.battle_max_frames = *value;
    }
    if (const auto value = env_value("V2_BATTLE_ARCHIVE_PATH")) {
        config.archive_path = *value;
    }
}

void fill_gateway_from_store(const ConfigStore& store, GatewayAppConfig& config) {
    if (const auto value = store.get_uint16("gateway.port")) {
        config.port = *value;
    }
    if (const auto value = store.get_uint16("gateway.http_management_port")) {
        config.http_management_port = *value;
    }
    if (const auto value = store.get_size("gateway.io_threads")) {
        config.io_threads = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_string("gateway.io_listener_ports")) {
        config.io_listener_ports = parse_integer_list<std::uint16_t>(*value);
    }
    if (const auto value = store.get_string("gateway.io_listener_core_ids")) {
        config.io_listener_core_ids = parse_integer_list<std::uint32_t>(*value);
    }
    if (const auto value = store.get_size("gateway.business_threads")) {
        config.business_threads = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_milliseconds("gateway.metrics_log_interval_ms")) {
        config.metrics_log_interval = *value;
    }
    config.metrics_prometheus_path = get_path_value(store, "gateway.metrics_prometheus_path");
    config.metrics_json_path = get_path_value(store, "gateway.metrics_json_path");
    if (const auto value = store.get_string("gateway.auth.provider")) {
        config.auth_provider = *value;
    }
    config.auth_users_path = get_path_value(store, "gateway.auth.users_path");
    if (const auto value = store.get_string("gateway.auth.http_endpoint")) {
        config.auth_http_endpoint = *value;
    }
    if (const auto value = store.get_milliseconds("gateway.auth.http_timeout_ms")) {
        config.auth_http_timeout = *value;
    }
    if (const auto value = store.get_size("gateway.max_connections")) {
        config.max_connections = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_size("gateway.max_guests")) {
        config.max_guests = *value;
    }
    if (const auto value = store.get_size("gateway.per_ip_connection_limit")) {
        config.per_ip_connection_limit = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_enabled")) {
        config.v2_shadow_bridge_enabled = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_emit_responses")) {
        config.v2_shadow_bridge_emit_responses = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_login")) {
        config.v2_shadow_bridge_login = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_room")) {
        config.v2_shadow_bridge_room = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_battle")) {
        config.v2_shadow_bridge_battle = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_echo")) {
        config.v2_shadow_bridge_echo = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_emit_battle_input_push")) {
        config.v2_shadow_bridge_emit_battle_input_push = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_emit_battle_state_started")) {
        config.v2_shadow_bridge_emit_battle_state_started = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_emit_battle_state_frame")) {
        config.v2_shadow_bridge_emit_battle_state_frame = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_emit_battle_state_settlement")) {
        config.v2_shadow_bridge_emit_battle_state_settlement = *value;
    }
    if (const auto value = store.get_bool("gateway.v2_shadow_bridge_emit_battle_state_finished")) {
        config.v2_shadow_bridge_emit_battle_state_finished = *value;
    }
    if (const auto value = store.get_uint32("session.max_packet_size")) {
        config.session_max_packet_size = *value;
    }
    if (const auto value = store.get_size("session.max_pending_write_bytes")) {
        config.session_max_pending_write_bytes = *value;
    }
    if (const auto value = store.get_milliseconds("session.heartbeat_check_interval_ms")) {
        config.session_heartbeat_check_interval = *value;
    }
    if (const auto value = store.get_milliseconds("session.heartbeat_timeout_ms")) {
        config.session_heartbeat_timeout = *value;
    }
    if (const auto value = store.get_string("tls.cert_chain_path")) {
        config.tls.enabled = true;
        config.tls.cert_chain_path = *value;
    }
    if (const auto value = store.get_string("tls.private_key_path")) {
        config.tls.private_key_path = *value;
    }
}

}  // namespace

bool ConfigStore::load(const std::filesystem::path& path) {
    values_.clear();

    try {
        if (path.extension() == ".json") {
            return load_json_file(path, values_);
        }
        return load_key_value_file(path, values_);
    } catch (const std::exception& ex) {
        LOG_WARN("Failed to parse config file {}: {}", path.string(), ex.what());
        values_.clear();
        return false;
    }
}

std::optional<std::string> ConfigStore::get_string(const std::string& key) const {
    const auto it = values_.find(key);
    if (it == values_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::optional<bool> ConfigStore::get_bool(const std::string& key) const {
    const auto value = get_string(key);
    return value ? parse_bool(*value) : std::nullopt;
}

std::optional<std::uint16_t> ConfigStore::get_uint16(const std::string& key) const {
    const auto value = get_string(key);
    return value ? parse_integer<std::uint16_t>(*value) : std::nullopt;
}

std::optional<std::uint32_t> ConfigStore::get_uint32(const std::string& key) const {
    const auto value = get_string(key);
    return value ? parse_integer<std::uint32_t>(*value) : std::nullopt;
}

std::optional<std::size_t> ConfigStore::get_size(const std::string& key) const {
    const auto value = get_string(key);
    return value ? parse_integer<std::size_t>(*value) : std::nullopt;
}

std::optional<std::chrono::milliseconds> ConfigStore::get_milliseconds(const std::string& key) const {
    const auto value = get_string(key);
    const auto parsed = value ? parse_integer<std::uint64_t>(*value) : std::nullopt;
    if (!parsed) {
        return std::nullopt;
    }
    return std::chrono::milliseconds(*parsed);
}

std::filesystem::path resolve_backend_config_path(const std::string& service_name,
                                                  int argc,
                                                  char* argv[],
                                                  std::filesystem::path fallback_path) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--config") {
            return std::filesystem::path(argv[i + 1]);
        }
    }

    const auto upper_service = [&service_name] {
        std::string value;
        value.reserve(service_name.size());
        for (const char ch : service_name) {
            value.push_back(static_cast<char>(
                ch == '-' ? '_' : std::toupper(static_cast<unsigned char>(ch))));
        }
        return value;
    }();

    if (const auto path = env_value(upper_service + "_CONFIG_PATH")) {
        return std::filesystem::path(*path);
    }
    if (const auto path = env_value("CONFIG_PATH")) {
        return std::filesystem::path(*path);
    }
    return fallback_path;
}

BackendServiceConfig load_backend_service_config(const std::string& service_name,
                                                 const std::filesystem::path& path,
                                                 std::uint16_t default_port) {
    BackendServiceConfig config;
    config.service_name = service_name;
    config.config_path = path.generic_string();
    config.port = default_port;

    ConfigStore store;
    if (store.load(path)) {
        fill_backend_from_store(store, config);
        LOG_INFO("Loaded {} backend config from {} (version: {})",
                 config.service_name,
                 path.string(),
                 config.config_version);
    } else {
        LOG_WARN("{} backend config file {} not found or invalid, using defaults plus env overlay",
                 service_name,
                 path.string());
    }

    apply_backend_env_overlay(service_name, config);
    return config;
}

std::optional<GatewayAppConfig> try_load_gateway_config(const std::filesystem::path& path) {
    ConfigStore store;
    if (!store.load(path)) {
        return std::nullopt;
    }
    GatewayAppConfig config{};
    fill_gateway_from_store(store, config);
    return config;
}

GatewayAppConfig load_gateway_config(const std::filesystem::path& path) {
    if (auto cfg = try_load_gateway_config(path)) {
        LOG_INFO("Loaded gateway config from {} (TLS: {})", path.string(), cfg->tls.enabled ? "on" : "off");
        return *cfg;
    }
    LOG_WARN("Gateway config file {} not found or invalid, using defaults", path.string());
    return GatewayAppConfig{};
}

PressureAppConfig load_pressure_config(const std::filesystem::path& path) {
    PressureAppConfig config;
    ConfigStore store;
    if (!store.load(path)) {
        LOG_WARN("Pressure config file {} not found, using defaults", path.string());
        return config;
    }

    if (const auto value = store.get_string("pressure.host")) {
        config.host = *value;
    }
    if (const auto value = store.get_uint16("pressure.port")) {
        config.port = *value;
    }
    if (const auto value = store.get_size("pressure.client_count")) {
        config.client_count = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_size("pressure.echo_count_per_client")) {
        config.messages_per_client = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_size("pressure.messages_per_client")) {
        config.messages_per_client = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_string("pressure.scenario")) {
        if (const auto scenario = parse_pressure_scenario(*value)) {
            config.scenario = *scenario;
        }
    }
    if (const auto value = store.get_milliseconds("pressure.send_interval_ms")) {
        config.send_interval = *value;
    }
    if (const auto value = store.get_size("pressure.invalid_token_every")) {
        config.invalid_token_every = std::max<std::size_t>(1, *value);
    }
    if (const auto value = store.get_string("pressure.room_name")) {
        config.room_name = *value;
    }
    if (const auto value = store.get_uint32("pressure.malicious_packet_size")) {
        config.malicious_packet_size = std::max<std::uint32_t>(64, *value);
    }

    LOG_INFO("Loaded pressure config from {}", path.string());
    return config;
}

std::string to_string(PressureScenario scenario) {
    switch (scenario) {
    case PressureScenario::kEcho:
        return "echo";
    case PressureScenario::kInvalidToken:
        return "invalid_token";
    case PressureScenario::kSlowEcho:
        return "slow_echo";
    case PressureScenario::kBroadcastStorm:
        return "broadcast_storm";
    case PressureScenario::kMaliciousPacket:
        return "malicious_packet";
    case PressureScenario::kBattleBroadcast:
        return "battle_broadcast";
    case PressureScenario::kChaos:
        return "chaos";
    case PressureScenario::kStability:
        return "stability";
    case PressureScenario::kBenchmark:
        return "benchmark";
    }

    return "echo";
}

std::optional<PressureScenario> parse_pressure_scenario(const std::string& value) {
    if (value == "echo") {
        return PressureScenario::kEcho;
    }
    if (value == "invalid_token") {
        return PressureScenario::kInvalidToken;
    }
    if (value == "slow_echo") {
        return PressureScenario::kSlowEcho;
    }
    if (value == "broadcast_storm") {
        return PressureScenario::kBroadcastStorm;
    }
    if (value == "malicious_packet") {
        return PressureScenario::kMaliciousPacket;
    }
    if (value == "battle_broadcast") {
        return PressureScenario::kBattleBroadcast;
    }
    if (value == "chaos") {
        return PressureScenario::kChaos;
    }
    if (value == "stability") {
        return PressureScenario::kStability;
    }
    if (value == "benchmark") {
        return PressureScenario::kBenchmark;
    }
    return std::nullopt;
}

}  // namespace app::config
