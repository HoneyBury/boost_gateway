#pragma once
// v2.3.0 G2: In-memory sorted-set leaderboard service

#include <cstdint>
#include <memory>
#include <string>
#include <vector>
#include <utility>

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

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::leaderboard
