#pragma once
// v2.3.0 G2: In-memory sorted-set leaderboard service.
// v3.2.0: Optional Redis backend via set_redis_leaderboard().

#include <cstdint>
#include <memory>
#include <string>
#include <vector>
#include <utility>

namespace v3::cluster {
struct RaftConfig;
}

namespace v3::persistence {
class RedisLeaderboard;
}  // namespace v3::persistence

namespace v2::leaderboard {

struct LeaderboardEntry {
    std::string user_id;
    std::string display_name;
    std::int64_t score = 0;
    std::int64_t rank = 0;  // 1-based rank
};

class LeaderboardService {
public:
    explicit LeaderboardService(std::uint16_t port);
    ~LeaderboardService();

    void start();
    void stop();
    [[nodiscard]] std::uint16_t local_port() const;

    // v3.2.0: Set Redis-backed leaderboard. Falls back to in-memory if unset.
    void set_redis_leaderboard(
        std::shared_ptr<v3::persistence::RedisLeaderboard> redis_lb);

    // v3.4.0: Optional Raft configuration for singleton/leadered deployments.
    void set_raft_config(v3::cluster::RaftConfig config);
    [[nodiscard]] bool is_raft_leader() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::leaderboard
