// v2.3.0 G1: Matchmaking backend example — thin wrapper.
#include "app/config.h"
#include "app/logging.h"
#include "v2/match/matchmaking_service.h"
#include "v2/platform/highres_timer.h"
#include "v3/cluster/raft.h"

#include <chrono>
#include <atomic>
#include <csignal>
#include <iostream>
#include <optional>
#include <thread>

namespace {

std::atomic<bool> g_running{true};
v2::match::MatchmakingService* g_service = nullptr;

void handle_signal(int) {
    g_running = false;
    if (g_service) {
        std::cout << "\nMatchmaking backend shutting down..." << std::endl;
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
    const v2::platform::HighResTimer hi_res_timer;
    app::logging::init("v2_match_backend");

    const auto config_path = app::config::resolve_backend_config_path(
        "matchmaking", argc, argv, "config/environments/local/matchmaking.json");
    auto config = app::config::load_backend_service_config("matchmaking", config_path, 9304);
    if (argc > 1 && std::string(argv[1]) != "--config") {
        config.port = static_cast<std::uint16_t>(std::stoi(argv[1]));
    }
    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    v2::match::MatchmakingService service(config.port);
    service.set_tls_config(config.tls_config);
    g_service = &service;
    if (auto raft = to_raft_config(config.raft, config.port); raft.has_value()) {
        service.set_raft_config(std::move(*raft));
        std::cout << "Matchmaking Raft enabled for node " << config.raft.node_id << std::endl;
    }
    service.start();
    std::cout << "Matchmaking backend listening on port " << config.port << std::endl;
    std::cout << "Matchmaking backend running (Ctrl+C to stop)" << std::endl;

    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    service.stop();
    return 0;
}
