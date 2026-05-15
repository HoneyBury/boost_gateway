// v2.3.0 G2: Leaderboard backend example — thin wrapper.
// v3.3.0 P0b: Optional Redis-backed persistence via REDIS_HOST env var.
#include "v2/leaderboard/leaderboard_service.h"
#include "v3/cluster/raft.h"
#include "v3/persistence/redis_client.h"
#include "v3/persistence/redis_leaderboard.h"
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <vector>

namespace {

std::vector<std::string> split_csv(const std::string& input) {
    std::vector<std::string> items;
    std::stringstream stream(input);
    std::string item;
    while (std::getline(stream, item, ',')) {
        if (!item.empty()) {
            items.push_back(item);
        }
    }
    return items;
}

std::optional<v3::cluster::RaftConfig> raft_config_from_env(std::uint16_t port) {
    const char* node_id = std::getenv("RAFT_NODE_ID");
    const char* peers = std::getenv("RAFT_PEERS");
    if (!node_id || !peers || node_id[0] == '\0' || peers[0] == '\0') {
        return std::nullopt;
    }

    v3::cluster::RaftConfig config;
    config.node_id = node_id;

    for (const auto& peer : split_csv(peers)) {
        auto at = peer.find('@');
        auto colon = peer.rfind(':');
        if (at == std::string::npos || colon == std::string::npos || colon <= at + 1) {
            continue;
        }
        config.peers.push_back(v3::cluster::RaftNodeId{
            .id = peer.substr(0, at),
            .host = peer.substr(at + 1, colon - at - 1),
            .port = static_cast<std::uint16_t>(std::atoi(peer.substr(colon + 1).c_str())),
        });
    }

    if (config.peers.empty()) {
        config.peers.push_back(v3::cluster::RaftNodeId{
            .id = config.node_id,
            .host = "127.0.0.1",
            .port = port,
        });
    }

    if (const char* min_ms = std::getenv("RAFT_ELECTION_TIMEOUT_MIN_MS")) {
        config.election_timeout_min = std::chrono::milliseconds(std::atoi(min_ms));
    }
    if (const char* max_ms = std::getenv("RAFT_ELECTION_TIMEOUT_MAX_MS")) {
        config.election_timeout_max = std::chrono::milliseconds(std::atoi(max_ms));
    }
    if (const char* hb_ms = std::getenv("RAFT_HEARTBEAT_INTERVAL_MS")) {
        config.heartbeat_interval = std::chrono::milliseconds(std::atoi(hb_ms));
    }
    if (const char* storage_dir = std::getenv("RAFT_STORAGE_DIR")) {
        config.storage_dir = storage_dir;
    }
    return config;
}

}  // namespace

int main() {
    std::uint16_t port = 9305;
    const char* env_port = std::getenv("LEADERBOARD_PORT");
    if (env_port) port = static_cast<std::uint16_t>(std::atoi(env_port));

    v2::leaderboard::LeaderboardService service(port);
    if (auto raft = raft_config_from_env(port); raft.has_value()) {
        service.set_raft_config(std::move(*raft));
        std::cout << "Leaderboard Raft enabled for node " << std::getenv("RAFT_NODE_ID")
                  << std::endl;
    }

    // v3.3.0 P0b: Optional Redis leaderboard backend
    const char* redis_host = std::getenv("REDIS_HOST");
    if (redis_host && redis_host[0] != '\0') {
        v3::persistence::RedisClient::Config redis_config;
        redis_config.host = redis_host;
        const char* redis_port = std::getenv("REDIS_PORT");
        if (redis_port) redis_config.port =
            static_cast<std::uint16_t>(std::atoi(redis_port));
        const char* redis_password = std::getenv("REDIS_PASSWORD");
        if (redis_password) redis_config.password = redis_password;

        v3::persistence::RedisLeaderboard::Config lb_config;
        lb_config.redis = std::move(redis_config);
        lb_config.key = "lb:global";

        auto display_port = lb_config.redis.port;
        auto redis_lb = std::make_shared<v3::persistence::RedisLeaderboard>(
            std::move(lb_config));
        if (redis_lb->available()) {
            service.set_redis_leaderboard(std::move(redis_lb));
            std::cout << "Redis leaderboard enabled ("
                      << redis_host << ":" << display_port << ")"
                      << std::endl;
        } else {
            std::cerr << "Redis unavailable at " << redis_host
                      << ", falling back to in-memory" << std::endl;
        }
    } else {
        std::cout << "Redis not configured, using in-memory leaderboard"
                  << std::endl;
    }

    service.start();
    std::cout << "Leaderboard backend on port " << port << std::endl;
    std::cout << "Press Enter to stop..." << std::endl;
    std::cin.get();
    service.stop();
    return 0;
}
