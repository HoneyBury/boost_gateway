#pragma once

#include <chrono>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <vector>

namespace game::match {

struct MatchPlayer {
    std::string user_id;
    std::string session_id;
    std::int64_t rating = 1000;
    std::chrono::steady_clock::time_point queued_at;
};

struct MatchResult {
    std::vector<MatchPlayer> players;
    std::string room_id;
};

class MatchmakingService {
public:
    using MatchCallback = std::function<void(const MatchResult&)>;

    explicit MatchmakingService(std::int64_t rating_spread = 200,
                                 std::size_t players_per_match = 2,
                                 std::chrono::seconds queue_timeout = std::chrono::seconds(30))
        : rating_spread_(rating_spread), players_per_match_(players_per_match),
          queue_timeout_(queue_timeout) {}

    void set_match_callback(MatchCallback cb) { on_match_ = std::move(cb); }

    bool join_queue(MatchPlayer player) {
        std::scoped_lock lock(mutex_);
        player.queued_at = std::chrono::steady_clock::now();
        queue_.push_back(std::move(player));
        try_match();
        return true;
    }

    bool leave_queue(const std::string& user_id) {
        std::scoped_lock lock(mutex_);
        auto it = std::find_if(queue_.begin(), queue_.end(),
                               [&](const auto& p) { return p.user_id == user_id; });
        if (it != queue_.end()) {
            queue_.erase(it);
            return true;
        }
        return false;
    }

    void tick() {
        std::scoped_lock lock(mutex_);
        const auto now = std::chrono::steady_clock::now();

        // Remove timed-out players
        queue_.erase(std::remove_if(queue_.begin(), queue_.end(),
                                     [&](const auto& p) {
                                         return now - p.queued_at > queue_timeout_;
                                     }),
                     queue_.end());

        try_match();
    }

    [[nodiscard]] std::size_t queue_size() const {
        std::scoped_lock lock(mutex_);
        return queue_.size();
    }

private:
    void try_match() {
        while (queue_.size() >= players_per_match_) {
            // Find players within rating spread of the first player
            const auto anchor_rating = queue_.front().rating;
            std::vector<MatchPlayer> matched;
            std::vector<std::size_t> matched_indices;

            for (std::size_t i = 0; i < queue_.size() && matched.size() < players_per_match_; ++i) {
                if (std::abs(queue_[i].rating - anchor_rating) <= rating_spread_) {
                    matched.push_back(queue_[i]);
                    matched_indices.push_back(i);
                }
            }

            if (matched.size() < players_per_match_) break;

            // Remove matched players (reverse order to preserve indices)
            for (auto it = matched_indices.rbegin(); it != matched_indices.rend(); ++it) {
                queue_.erase(queue_.begin() + static_cast<std::ptrdiff_t>(*it));
            }

            if (on_match_) {
                on_match_(MatchResult{
                    .players = std::move(matched),
                    .room_id = "match_" + std::to_string(match_counter_++),
                });
            }
        }
    }

    std::int64_t rating_spread_;
    std::size_t players_per_match_;
    std::chrono::seconds queue_timeout_;
    std::uint64_t match_counter_ = 0;
    mutable std::mutex mutex_;
    std::deque<MatchPlayer> queue_;
    MatchCallback on_match_;
};

}  // namespace game::match
