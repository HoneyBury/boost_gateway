#include "app/config.h"

#include "app/logging.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <cctype>
#include <charconv>
#include <filesystem>
#include <fstream>
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

GatewayAppConfig load_gateway_config(const std::filesystem::path& path) {
    GatewayAppConfig config;
    ConfigStore store;
    if (!store.load(path)) {
        LOG_WARN("Gateway config file {} not found, using defaults", path.string());
        return config;
    }

    if (const auto value = store.get_uint16("gateway.port")) {
        config.port = *value;
    }
    if (const auto value = store.get_uint16("gateway.http_management_port")) {
        config.http_management_port = *value;
    }
    if (const auto value = store.get_size("gateway.io_threads")) {
        config.io_threads = std::max<std::size_t>(1, *value);
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

    LOG_INFO("Loaded gateway config from {}", path.string());
    return config;
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
    return std::nullopt;
}

}  // namespace app::config
