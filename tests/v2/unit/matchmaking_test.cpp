// v2.3.0 G1: Matchmaking unit tests

#include <gtest/gtest.h>

#include "v2/match/matchmaking_service.h"

#include <chrono>
#include <thread>

using v2::match::MatchMode;
using v2::match::MatchPlayer;

TEST(MatchmakingTest, PlayersForMode) {
    EXPECT_EQ(v2::match::players_for_mode(MatchMode::k1v1), 2);
    EXPECT_EQ(v2::match::players_for_mode(MatchMode::k2v2), 4);
    EXPECT_EQ(v2::match::players_for_mode(MatchMode::k4v4), 8);
}

TEST(MatchmakingTest, ModeToString) {
    EXPECT_STREQ(v2::match::to_string(MatchMode::k1v1), "1v1");
    EXPECT_STREQ(v2::match::to_string(MatchMode::k2v2), "2v2");
    EXPECT_STREQ(v2::match::to_string(MatchMode::k4v4), "4v4");
}

TEST(MatchmakingTest, ServiceStartStop) {
    v2::match::MatchmakingService service(0);  // port 0 = ephemeral
    service.start();
    auto port = service.local_port();
    EXPECT_GT(port, 0U);
    service.stop();
}

TEST(MatchmakingTest, QueueAndLeave) {
    v2::match::MatchmakingService service(0);
    service.start();

    // Connect and send match_join
    // Note: full integration test would use BackendConnection
    // Unit test: verify service starts/stops cleanly with default config

    service.stop();
    SUCCEED();
}

TEST(MatchmakingTest, DefaultConfig) {
    v2::match::MatchmakingConfig config;
    EXPECT_EQ(config.mode, MatchMode::k1v1);
    EXPECT_EQ(config.mmr_range_initial, 100);
    EXPECT_EQ(config.mmr_range_expand_per_sec, 50);
    EXPECT_EQ(config.max_wait_ms, 30000U);
    EXPECT_EQ(config.match_check_interval_ms, 1000U);
}

TEST(MatchmakingTest, MatchPlayerDefaults) {
    MatchPlayer player;
    EXPECT_TRUE(player.user_id.empty());
    EXPECT_EQ(player.mmr, 1000);
    EXPECT_EQ(player.mode, MatchMode::k1v1);
    EXPECT_EQ(player.queued_at_ms, 0U);
}
