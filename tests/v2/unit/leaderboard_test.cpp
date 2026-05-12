// v2.3.0 G2: Leaderboard unit tests

#include <gtest/gtest.h>

#include "v2/leaderboard/leaderboard_service.h"

TEST(LeaderboardTest, ServiceStartStop) {
    v2::leaderboard::LeaderboardService service(0);
    service.start();
    auto port = service.local_port();
    EXPECT_GT(port, 0U);
    service.stop();
}

TEST(LeaderboardTest, EntryDefaults) {
    v2::leaderboard::LeaderboardEntry entry;
    EXPECT_TRUE(entry.user_id.empty());
    EXPECT_EQ(entry.score, 0);
    EXPECT_EQ(entry.rank, 0);
}
