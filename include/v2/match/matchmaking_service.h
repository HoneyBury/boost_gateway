#pragma once
// v2.3.0 G1: MMR-based matchmaking service
// v3.0.0 B4: Raft consensus for leader election

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace v3::cluster {
struct RaftConfig;
}

namespace v2::match {

enum class MatchMode : std::uint8_t {
    k1v1 = 0,
    k2v2 = 1,
    k4v4 = 2,
};

inline constexpr int players_for_mode(MatchMode mode) {
    switch (mode) {
        case MatchMode::k1v1: return 2;
        case MatchMode::k2v2: return 4;
        case MatchMode::k4v4: return 8;
    }
    return 2;
}

inline constexpr const char* to_string(MatchMode mode) {
    switch (mode) {
        case MatchMode::k1v1: return "1v1";
        case MatchMode::k2v2: return "2v2";
        case MatchMode::k4v4: return "4v4";
    }
    return "unknown";
}

struct MatchPlayer {
    std::string user_id;
    std::int64_t mmr = 1000;
    std::uint64_t queued_at_ms = 0;
    MatchMode mode = MatchMode::k1v1;
};

struct MatchResult {
    std::string match_id;
    MatchMode mode = MatchMode::k1v1;
    std::vector<std::string> player_ids;
    std::int64_t avg_mmr = 0;
};

struct MatchmakingConfig {
    MatchMode mode = MatchMode::k1v1;
    std::int64_t mmr_range_initial = 100;       // initial MMR search range
    std::int64_t mmr_range_expand_per_sec = 50; // expand range per second
    std::uint64_t max_wait_ms = 30'000;         // max queue time (30s)
    std::uint64_t match_check_interval_ms = 1000; // check every 1s
};

class MatchmakingService {
public:
    explicit MatchmakingService(std::uint16_t port);
    ~MatchmakingService();

    void start();
    void stop();
    [[nodiscard]] std::uint16_t local_port() const;

    // v3.0.0: Raft consensus configuration
    void set_raft_config(v3::cluster::RaftConfig config);
    [[nodiscard]] bool is_raft_leader() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::match
