// v3.2.0: Redis-backed leaderboard implementation.
// Uses Redis ZSET for scores and a hash for display names.

#include "v3/persistence/redis_leaderboard.h"

namespace v3::persistence {

class RedisLeaderboard::Impl {
public:
    explicit Impl(Config config)
        : client_(std::move(config.redis))
        , zset_key_(std::move(config.key))
        , names_key_(zset_key_ + ":names") {}

    std::optional<std::int64_t> submit(const std::string& user_id,
                                       const std::string& display_name,
                                       std::int64_t score) {
        if (!ensure_available()) return std::nullopt;

        client_.zadd(zset_key_, static_cast<double>(score), user_id);
        if (!display_name.empty()) {
            client_.hset(names_key_, user_id, display_name);
        }

        auto rank = client_.zrevrank(zset_key_, user_id);
        if (!rank.has_value()) return std::nullopt;
        return *rank + 1;  // convert 0-based to 1-based
    }

    std::vector<LeaderboardEntry> top_k(std::size_t k) {
        std::vector<LeaderboardEntry> result;
        if (!ensure_available() || k == 0) return result;

        auto pairs = client_.zrevrange_with_scores(
            zset_key_, 0, static_cast<std::int64_t>(k) - 1);

        result.reserve(pairs.size());
        std::int64_t rank = 1;
        for (auto& [user_id, score] : pairs) {
            LeaderboardEntry entry;
            entry.user_id = std::move(user_id);
            entry.score = static_cast<std::int64_t>(score);
            entry.rank = rank++;
            // Resolve display name
            auto name = client_.hget(names_key_, entry.user_id);
            if (name.has_value()) entry.display_name = *name;
            result.push_back(std::move(entry));
        }
        return result;
    }

    std::optional<LeaderboardEntry> rank_of(const std::string& user_id) {
        if (!ensure_available()) return std::nullopt;

        auto rank = client_.zrevrank(zset_key_, user_id);
        if (!rank.has_value()) return std::nullopt;

        auto score = client_.zscore(zset_key_, user_id);
        if (!score.has_value()) return std::nullopt;

        LeaderboardEntry entry;
        entry.user_id = user_id;
        entry.rank = *rank + 1;
        entry.score = static_cast<std::int64_t>(*score);

        auto name = client_.hget(names_key_, user_id);
        if (name.has_value()) entry.display_name = *name;

        return entry;
    }

    std::size_t size() {
        if (!ensure_available()) return 0;
        auto n = client_.zcard(zset_key_);
        return n >= 0 ? static_cast<std::size_t>(n) : 0;
    }

    bool available() { return ensure_available(); }

private:
    RedisClient client_;
    std::string zset_key_;
    std::string names_key_;

    bool ensure_available() {
        if (client_.is_connected()) return true;
        return client_.reconnect();
    }
};

RedisLeaderboard::RedisLeaderboard(Config config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}
RedisLeaderboard::~RedisLeaderboard() = default;
RedisLeaderboard::RedisLeaderboard(RedisLeaderboard&&) noexcept = default;
RedisLeaderboard& RedisLeaderboard::operator=(RedisLeaderboard&&) noexcept = default;

std::optional<std::int64_t> RedisLeaderboard::submit(
    const std::string& user_id, const std::string& display_name, std::int64_t score) {
    return impl_->submit(user_id, display_name, score);
}

std::vector<LeaderboardEntry> RedisLeaderboard::top_k(std::size_t k) {
    return impl_->top_k(k);
}

std::optional<LeaderboardEntry> RedisLeaderboard::rank_of(const std::string& user_id) {
    return impl_->rank_of(user_id);
}

std::size_t RedisLeaderboard::size() { return impl_->size(); }
bool RedisLeaderboard::available() const { return impl_->available(); }

}  // namespace v3::persistence
