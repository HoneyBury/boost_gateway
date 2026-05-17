// v2.3.0 G2: Leaderboard backend example — thin wrapper.
// v3.3.0 P0b: Optional Redis-backed persistence via REDIS_HOST env var.
#include "app/config.h"
#include "app/logging.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v3/cluster/raft.h"
#include "v3/persistence/redis_client.h"
#include "v3/persistence/redis_leaderboard.h"
#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <memory>
#include <optional>
#include <thread>

namespace {

std::atomic<bool> g_running{true};
v2::leaderboard::LeaderboardService* g_service = nullptr;

void handle_signal(int) {
    g_running = false;
    if (g_service) {
        std::cout << "\nLeaderboard backend shutting down..." << std::endl;
        g_service->stop();
    }
}

std::optional<v3::cluster::RaftConfig> to_raft_config(
    const app::config::RaftServiceConfig& source,
    std::uint16_t port) {
    if (source.node_id.empty() || source.peers.empty()) {
        return std::nullopt;
    }

    v3::cluster::RaftConfig config;
    config.node_id = source.node_id;
    config.storage_dir = source.storage_dir;
    config.election_timeout_min = source.election_timeout_min;
    config.election_timeout_max = source.election_timeout_max;
    config.heartbeat_interval = source.heartbeat_interval;

    for (const auto& peer : source.peers) {
        config.peers.push_back(v3::cluster::RaftNodeId{
            .id = peer.id,
            .host = peer.host,
            .port = peer.port,
        });
    }

    if (config.peers.empty()) {
        config.peers.push_back(v3::cluster::RaftNodeId{
            .id = source.node_id,
            .host = "127.0.0.1",
            .port = port,
        });
    }
    return config;
}

}  // namespace

int main(int argc, char* argv[]) {
    app::logging::init("v2_leaderboard_backend");

    const auto config_path = app::config::resolve_backend_config_path(
        "leaderboard", argc, argv, "config/environments/local/leaderboard.json");
    auto config = app::config::load_backend_service_config("leaderboard", config_path, 9305);
    if (argc > 1 && std::string(argv[1]) != "--config") {
        config.port = static_cast<std::uint16_t>(std::stoi(argv[1]));
    }
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    v2::leaderboard::LeaderboardService service(config.port);
    g_service = &service;
    if (auto raft = to_raft_config(config.raft, config.port); raft.has_value()) {
        service.set_raft_config(std::move(*raft));
        std::cout << "Leaderboard Raft enabled for node " << config.raft.node_id << std::endl;
    }

    if (!config.redis.host.empty()) {
        v3::persistence::RedisClient::Config redis_config;
        redis_config.host = config.redis.host;
        redis_config.port = config.redis.port;
        redis_config.password = config.redis.password;

        v3::persistence::RedisLeaderboard::Config lb_config;
        lb_config.redis = std::move(redis_config);
        lb_config.key = config.redis.leaderboard_key;

        auto display_port = lb_config.redis.port;
        auto redis_lb = std::make_shared<v3::persistence::RedisLeaderboard>(
            std::move(lb_config));
        if (redis_lb->available()) {
            service.set_redis_leaderboard(std::move(redis_lb));
            std::cout << "Redis leaderboard enabled ("
                      << config.redis.host << ":" << display_port << ")"
                      << std::endl;
        } else {
            std::cerr << "Redis unavailable at " << config.redis.host
                      << ", falling back to in-memory" << std::endl;
        }
    } else {
        std::cout << "Redis not configured, using in-memory leaderboard"
                  << std::endl;
    }

    service.start();
    std::cout << "Leaderboard backend on port " << config.port << std::endl;
    std::cout << "Leaderboard backend running (Ctrl+C to stop)" << std::endl;
    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    service.stop();
    return 0;
}
