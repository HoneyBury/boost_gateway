#include "game/match/matchmaking_service.h"

#include <chrono>
#include <thread>

#include <gtest/gtest.h>

TEST(MatchmakingTest, JoinAndLeaveQueue) {
    game::match::MatchmakingService svc(200, 2, std::chrono::seconds(30));
    EXPECT_EQ(svc.queue_size(), 0u);

    EXPECT_TRUE(svc.join_queue({.user_id = "user1", .session_id = "s1", .rating = 1000, .queued_at = {}}));
    EXPECT_EQ(svc.queue_size(), 1u);

    EXPECT_TRUE(svc.leave_queue("user1"));
    EXPECT_EQ(svc.queue_size(), 0u);
}

TEST(MatchmakingTest, MatchWithinRatingSpread) {
    int match_count = 0;
    game::match::MatchmakingService svc(200, 2, std::chrono::seconds(30));
    svc.set_match_callback([&](const auto& result) {
        match_count++;
        EXPECT_EQ(result.players.size(), 2u);
    });

    svc.join_queue({.user_id = "u1", .session_id = "s1", .rating = 1000, .queued_at = {}});
    svc.join_queue({.user_id = "u2", .session_id = "s2", .rating = 1050, .queued_at = {}});
    EXPECT_EQ(svc.queue_size(), 0u);
    EXPECT_EQ(match_count, 1);
}

TEST(MatchmakingTest, NoMatchOutsideSpread) {
    int match_count = 0;
    game::match::MatchmakingService svc(100, 2, std::chrono::seconds(30));
    svc.set_match_callback([&](const auto&) { match_count++; });

    svc.join_queue({.user_id = "u1", .session_id = "s1", .rating = 1000, .queued_at = {}});
    svc.join_queue({.user_id = "u2", .session_id = "s2", .rating = 1500, .queued_at = {}});
    EXPECT_EQ(svc.queue_size(), 2u);
    EXPECT_EQ(match_count, 0);
}

TEST(MatchmakingTest, TickRemovesTimeout) {
    game::match::MatchmakingService svc(500, 2, std::chrono::seconds(0));
    svc.join_queue({.user_id = "u1", .session_id = "s1", .rating = 1000, .queued_at = {}});
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    svc.tick();
    EXPECT_EQ(svc.queue_size(), 0u);
}
