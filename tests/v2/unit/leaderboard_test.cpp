// v2.3.0 G2: Leaderboard unit tests
// v3.2.0: RedisLeaderboard tests added.

#include <gtest/gtest.h>

#include <chrono>

#include "v2/leaderboard/leaderboard_service.h"
#include "v3/persistence/redis_leaderboard.h"

// ── Redis availability check ──────────────────────────────────────────────

static bool is_redis_running() {
    v3::persistence::RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(200);
    v3::persistence::RedisClient client(cfg);
    return client.reconnect();
}

static bool redis_available = is_redis_running();

// ── LeaderboardService tests ──────────────────────────────────────────────

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

// ── RedisLeaderboard graceful degradation (no Redis) ──────────────────────

class RedisLeaderboardDegradedTest : public ::testing::Test {
protected:
    void SetUp() override {
        v3::persistence::RedisLeaderboard::Config cfg;
        cfg.redis.host = "127.0.0.1";
        cfg.redis.port = 1;
        cfg.redis.timeout = std::chrono::milliseconds(100);
        cfg.key = "test:lb:degraded";
        lb_ = std::make_unique<v3::persistence::RedisLeaderboard>(std::move(cfg));
    }

    std::unique_ptr<v3::persistence::RedisLeaderboard> lb_;
};

TEST_F(RedisLeaderboardDegradedTest, AvailableReturnsFalse) {
    EXPECT_FALSE(lb_->available());
}

TEST_F(RedisLeaderboardDegradedTest, SubmitReturnsNullopt) {
    EXPECT_EQ(lb_->submit("user1", "Alice", 100), std::nullopt);
}

TEST_F(RedisLeaderboardDegradedTest, TopKReturnsEmpty) {
    EXPECT_TRUE(lb_->top_k(10).empty());
}

TEST_F(RedisLeaderboardDegradedTest, RankOfReturnsNullopt) {
    EXPECT_EQ(lb_->rank_of("user1"), std::nullopt);
}

TEST_F(RedisLeaderboardDegradedTest, SizeReturnsZero) {
    EXPECT_EQ(lb_->size(), 0U);
}

// ── RedisLeaderboard live tests (requires Redis) ──────────────────────────

class RedisLeaderboardLiveTest : public ::testing::Test {
protected:
    void SetUp() override {
        if (!redis_available) GTEST_SKIP() << "Redis not running";
        v3::persistence::RedisLeaderboard::Config cfg;
        cfg.key = unique_key();
        lb_ = std::make_unique<v3::persistence::RedisLeaderboard>(std::move(cfg));
        ASSERT_TRUE(lb_->available());
    }

    void TearDown() override {
        // Best-effort cleanup — key is unique per test so no cross-test pollution.
    }

    static std::string unique_key() {
        static int counter = 0;
        return "test:lb:" + std::to_string(++counter);
    }

    std::unique_ptr<v3::persistence::RedisLeaderboard> lb_;
};

TEST_F(RedisLeaderboardLiveTest, SubmitReturnsRank) {
    auto r1 = lb_->submit("alice", "Alice", 100);
    ASSERT_TRUE(r1.has_value());
    EXPECT_EQ(*r1, 1);

    auto r2 = lb_->submit("bob", "Bob", 200);
    ASSERT_TRUE(r2.has_value());
    EXPECT_EQ(*r2, 1);

    auto r3 = lb_->submit("carol", "Carol", 50);
    ASSERT_TRUE(r3.has_value());
    EXPECT_EQ(*r3, 3);
}

TEST_F(RedisLeaderboardLiveTest, RankOfReturnsCorrectEntry) {
    lb_->submit("alice", "Alice", 100);
    lb_->submit("bob", "Bob", 200);
    lb_->submit("carol", "Carol", 50);

    auto entry = lb_->rank_of("bob");
    ASSERT_TRUE(entry.has_value());
    EXPECT_EQ(entry->user_id, "bob");
    EXPECT_EQ(entry->score, 200);
    EXPECT_EQ(entry->rank, 1);

    auto entry2 = lb_->rank_of("carol");
    ASSERT_TRUE(entry2.has_value());
    EXPECT_EQ(entry2->user_id, "carol");
    EXPECT_EQ(entry2->score, 50);
    EXPECT_EQ(entry2->rank, 3);
}

TEST_F(RedisLeaderboardLiveTest, TopKReturnsDescending) {
    lb_->submit("alice", "Alice", 100);
    lb_->submit("bob", "Bob", 200);
    lb_->submit("carol", "Carol", 50);
    lb_->submit("dave", "Dave", 150);

    auto top = lb_->top_k(3);
    ASSERT_EQ(top.size(), 3U);
    EXPECT_EQ(top[0].user_id, "bob");
    EXPECT_EQ(top[0].score, 200);
    EXPECT_EQ(top[0].rank, 1);
    EXPECT_EQ(top[1].user_id, "dave");
    EXPECT_EQ(top[1].score, 150);
    EXPECT_EQ(top[1].rank, 2);
    EXPECT_EQ(top[2].user_id, "alice");
    EXPECT_EQ(top[2].score, 100);
    EXPECT_EQ(top[2].rank, 3);
}

TEST_F(RedisLeaderboardLiveTest, TopKCap) {
    lb_->submit("alice", "Alice", 100);
    lb_->submit("bob", "Bob", 200);

    auto top = lb_->top_k(1);
    EXPECT_EQ(top.size(), 1U);
}

TEST_F(RedisLeaderboardLiveTest, TopKZeroReturnsEmpty) {
    lb_->submit("alice", "Alice", 100);
    EXPECT_TRUE(lb_->top_k(0).empty());
}

TEST_F(RedisLeaderboardLiveTest, SizeReflectsEntries) {
    EXPECT_EQ(lb_->size(), 0U);
    lb_->submit("alice", "Alice", 100);
    EXPECT_EQ(lb_->size(), 1U);
    lb_->submit("bob", "Bob", 200);
    EXPECT_EQ(lb_->size(), 2U);
}

TEST_F(RedisLeaderboardLiveTest, UpdateScore) {
    lb_->submit("alice", "Alice", 100);
    lb_->submit("bob", "Bob", 200);

    auto r = lb_->submit("alice", "Alice", 300);
    ASSERT_TRUE(r.has_value());
    EXPECT_EQ(*r, 1);

    auto entry = lb_->rank_of("bob");
    ASSERT_TRUE(entry.has_value());
    EXPECT_EQ(entry->rank, 2);
}

TEST_F(RedisLeaderboardLiveTest, DisplayNamePreserved) {
    lb_->submit("alice", "Alice Wonderland", 100);

    auto entry = lb_->rank_of("alice");
    ASSERT_TRUE(entry.has_value());
    EXPECT_EQ(entry->display_name, "Alice Wonderland");

    auto top = lb_->top_k(1);
    ASSERT_EQ(top.size(), 1U);
    EXPECT_EQ(top[0].display_name, "Alice Wonderland");
}

TEST_F(RedisLeaderboardLiveTest, RankOfNonexistentReturnsNullopt) {
    EXPECT_EQ(lb_->rank_of("nobody"), std::nullopt);
}

TEST_F(RedisLeaderboardLiveTest, EmptyLeaderboardTopK) {
    EXPECT_TRUE(lb_->top_k(10).empty());
}
