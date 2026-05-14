#pragma once
// v3.2.0: Redis-backed leaderboard using ZSET operations.
// Drop-in replacement for the in-memory SortedSet in LeaderboardService.

#include "v3/persistence/redis_client.h"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace v3::persistence {

struct LeaderboardEntry {
    std::string user_id;
    std::string display_name;
    std::int64_t score = 0;
    std::int64_t rank = 0;  // 1-based
};

class RedisLeaderboard {
public:
    struct Config {
        RedisClient::Config redis;
        std::string key = "lb:default";  // Redis ZSET key
    };

    explicit RedisLeaderboard(Config config);
    ~RedisLeaderboard();

    RedisLeaderboard(const RedisLeaderboard&) = delete;
    RedisLeaderboard& operator=(const RedisLeaderboard&) = delete;
    RedisLeaderboard(RedisLeaderboard&&) noexcept;
    RedisLeaderboard& operator=(RedisLeaderboard&&) noexcept;

    /// Submit or update a score. Returns the new rank (1-based).
    std::optional<std::int64_t> submit(const std::string& user_id,
                                       const std::string& display_name,
                                       std::int64_t score);

    /// Top-K entries by score (descending).
    std::vector<LeaderboardEntry> top_k(std::size_t k);

    /// Look up a user's rank and score.
    std::optional<LeaderboardEntry> rank_of(const std::string& user_id);

    /// Number of entries in the leaderboard.
    std::size_t size();

    /// Whether Redis is available.
    [[nodiscard]] bool available() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v3::persistence
