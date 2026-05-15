// v2.3.0 G1: Matchmaking backend example — thin wrapper.
#include "v2/match/matchmaking_service.h"
#include "v3/cluster/raft.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <sstream>
#include <thread>
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
    config.peers.clear();

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
    std::uint16_t port = 9304;
    const char* env_port = std::getenv("MATCH_PORT");
    if (env_port) port = static_cast<std::uint16_t>(std::atoi(env_port));

    v2::match::MatchmakingService service(port);
    if (auto raft = raft_config_from_env(port); raft.has_value()) {
        service.set_raft_config(std::move(*raft));
        std::cout << "Matchmaking Raft enabled for node " << std::getenv("RAFT_NODE_ID")
                  << std::endl;
    }
    service.start();
    std::cout << "Matchmaking backend listening on port " << port << std::endl;

    // Run until interrupted
    std::cout << "Press Enter to stop..." << std::endl;
    std::cin.get();

    service.stop();
    return 0;
}
