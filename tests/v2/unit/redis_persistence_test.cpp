// v3.2.0: Comprehensive unit tests for Redis persistence classes:
//   RedisClient, RedisConnectionPool, RedisEventStore, RedisLeaderboard.
//
// When Redis is unavailable at 127.0.0.1:6379, live-connection tests are
// skipped with GTEST_SKIP. Graceful-degradation tests exercise the no-Redis
// code paths unconditionally.

#include <gtest/gtest.h>
#include "v3/persistence/redis_client.h"
#include "v3/persistence/redis_connection_pool.h"
#include "v3/persistence/redis_event_store.h"
#include "v3/persistence/redis_leaderboard.h"

#include <chrono>
#include <string>
#include <thread>

using namespace v3::persistence;

// ── Redis availability gate (shared by all test suites) ──────────────────

static bool is_redis_running() {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    return client.reconnect();
}

static bool redis_available = is_redis_running();

// Unique key prefix per test run to avoid collisions.
static std::string unique_prefix(const std::string& label) {
    static int counter = 0;
    static const auto run_id = std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count());
    return "test:" + label + ":" + run_id + ":" + std::to_string(++counter);
}

// ── Helper: make a connected client for live tests ───────────────────────

static RedisClient make_connected_client() {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(1000);
    RedisClient client(cfg);
    EXPECT_TRUE(client.reconnect());
    return client;
}

// ========================================================================
// RedisClient tests
// ========================================================================

// ── Graceful-degradation (no Redis needed) ───────────────────────────────

TEST(RedisClientTest, ConnectFailsGracefully) {
    RedisClient::Config cfg;
    cfg.host = "127.0.0.1";
    cfg.port = 1;  // unreachable
    cfg.timeout = std::chrono::milliseconds(200);
    RedisClient client(cfg);
    EXPECT_FALSE(client.reconnect());
    EXPECT_FALSE(client.is_connected());
}

TEST(RedisClientTest, DisconnectedOperationsReturnEmpty) {
    RedisClient::Config cfg;
    cfg.host = "127.0.0.1";
    cfg.port = 1;
    cfg.timeout = std::chrono::milliseconds(200);
    RedisClient client(cfg);

    EXPECT_FALSE(client.is_connected());
    EXPECT_EQ(client.get("key"), std::nullopt);
    EXPECT_FALSE(client.set("key", "value"));
    EXPECT_FALSE(client.del("key"));
    EXPECT_FALSE(client.exists("key"));
    EXPECT_EQ(client.incr("key"), -1);
    EXPECT_FALSE(client.lpush("list", "item"));
    EXPECT_TRUE(client.lrange("list", 0, -1).empty());
    EXPECT_EQ(client.llen("list"), -1);
    EXPECT_FALSE(client.hset("h", "f", "v"));
    EXPECT_EQ(client.hget("h", "f"), std::nullopt);
    EXPECT_FALSE(client.zadd("z", 1.0, "m"));
    EXPECT_TRUE(client.zrange_with_scores("z", 0, -1).empty());
    EXPECT_TRUE(client.zrevrange_with_scores("z", 0, -1).empty());
    EXPECT_EQ(client.zrevrank("z", "m"), std::nullopt);
    EXPECT_EQ(client.zscore("z", "m"), std::nullopt);
    EXPECT_EQ(client.zcard("z"), -1);
}

// ── String operations ────────────────────────────────────────────────────

TEST(RedisClientTest, SetAndGet) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("str");

    EXPECT_TRUE(client.set(key, "hello"));
    auto val = client.get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "hello");

    client.del(key);
}

TEST(RedisClientTest, SetOverwritesExisting) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("over");

    EXPECT_TRUE(client.set(key, "first"));
    EXPECT_TRUE(client.set(key, "second"));
    auto val = client.get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "second");

    client.del(key);
}

TEST(RedisClientTest, GetNonExistent) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("nonex");

    EXPECT_EQ(client.get(key), std::nullopt);
}

TEST(RedisClientTest, DelRemovesKey) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("del");

    EXPECT_TRUE(client.set(key, "temp"));
    EXPECT_TRUE(client.exists(key));
    EXPECT_TRUE(client.del(key));
    EXPECT_FALSE(client.exists(key));
    EXPECT_EQ(client.get(key), std::nullopt);
}

TEST(RedisClientTest, DelNonExistentReturnsTrue) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("delne");

    // DEL of a non-existent key still returns OK (integer 0 -> bool true)
    EXPECT_TRUE(client.del(key));
}

TEST(RedisClientTest, ExistsReturnsTrue) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("ext");

    EXPECT_TRUE(client.set(key, "v"));
    EXPECT_TRUE(client.exists(key));
    client.del(key);
}

TEST(RedisClientTest, ExistsReturnsFalse) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("exf");

    EXPECT_FALSE(client.exists(key));
}

TEST(RedisClientTest, IncrIncrements) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("incr");

    client.del(key);
    EXPECT_EQ(client.incr(key), 1);
    EXPECT_EQ(client.incr(key), 2);
    EXPECT_EQ(client.incr(key), 3);
    client.del(key);
}

TEST(RedisClientTest, IncrNewKeyStartsAtOne) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("incrnew");

    client.del(key);
    EXPECT_EQ(client.incr(key), 1);
    client.del(key);
}

// ── List operations ──────────────────────────────────────────────────────

TEST(RedisClientTest, LPushAndLRange) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("list");

    client.del(key);
    EXPECT_TRUE(client.lpush(key, "c"));
    EXPECT_TRUE(client.lpush(key, "b"));
    EXPECT_TRUE(client.lpush(key, "a"));

    auto items = client.lrange(key, 0, -1);
    ASSERT_EQ(items.size(), 3U);
    EXPECT_EQ(items[0], "a");
    EXPECT_EQ(items[1], "b");
    EXPECT_EQ(items[2], "c");

    client.del(key);
}

TEST(RedisClientTest, LRangePartial) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("lpart");

    client.del(key);
    client.lpush(key, "z");
    client.lpush(key, "y");
    client.lpush(key, "x");

    auto items = client.lrange(key, 0, 1);
    ASSERT_EQ(items.size(), 2U);
    EXPECT_EQ(items[0], "x");
    EXPECT_EQ(items[1], "y");

    client.del(key);
}

TEST(RedisClientTest, LRangeEmptyReturnsEmpty) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("lempty");

    EXPECT_TRUE(client.lrange(key, 0, -1).empty());
}

TEST(RedisClientTest, LLenReturnsCount) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("llen");

    client.del(key);
    EXPECT_EQ(client.llen(key), 0);
    client.lpush(key, "a");
    client.lpush(key, "b");
    EXPECT_EQ(client.llen(key), 2);
    client.del(key);
}

// ── Hash operations ──────────────────────────────────────────────────────

TEST(RedisClientTest, HSetAndHGet) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("hash");

    client.del(key);
    EXPECT_TRUE(client.hset(key, "field1", "value1"));
    auto val = client.hget(key, "field1");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "value1");
    client.del(key);
}

TEST(RedisClientTest, HGetNonExistentField) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("hmiss");

    client.del(key);
    EXPECT_EQ(client.hget(key, "nope"), std::nullopt);
    client.del(key);
}

TEST(RedisClientTest, HSetMultipleFields) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("hmulti");

    client.del(key);
    EXPECT_TRUE(client.hset(key, "name", "alice"));
    EXPECT_TRUE(client.hset(key, "score", "100"));

    auto name = client.hget(key, "name");
    ASSERT_TRUE(name.has_value());
    EXPECT_EQ(*name, "alice");

    auto score = client.hget(key, "score");
    ASSERT_TRUE(score.has_value());
    EXPECT_EQ(*score, "100");

    client.del(key);
}

// ── Sorted set operations ────────────────────────────────────────────────

TEST(RedisClientTest, ZAddAndZRangeWithScores) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("zrange");

    client.del(key);
    EXPECT_TRUE(client.zadd(key, 10.0, "alice"));
    EXPECT_TRUE(client.zadd(key, 20.0, "bob"));
    EXPECT_TRUE(client.zadd(key, 30.0, "carol"));

    auto scores = client.zrange_with_scores(key, 0, -1);
    ASSERT_EQ(scores.size(), 3U);
    EXPECT_EQ(scores[0].first, "alice");
    EXPECT_DOUBLE_EQ(scores[0].second, 10.0);
    EXPECT_EQ(scores[1].first, "bob");
    EXPECT_DOUBLE_EQ(scores[1].second, 20.0);
    EXPECT_EQ(scores[2].first, "carol");
    EXPECT_DOUBLE_EQ(scores[2].second, 30.0);

    client.del(key);
}

TEST(RedisClientTest, ZRevRangeWithScores) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("zrev");

    client.del(key);
    client.zadd(key, 10.0, "alice");
    client.zadd(key, 30.0, "carol");
    client.zadd(key, 20.0, "bob");

    auto scores = client.zrevrange_with_scores(key, 0, -1);
    ASSERT_EQ(scores.size(), 3U);
    EXPECT_EQ(scores[0].first, "carol");
    EXPECT_DOUBLE_EQ(scores[0].second, 30.0);
    EXPECT_EQ(scores[1].first, "bob");
    EXPECT_DOUBLE_EQ(scores[1].second, 20.0);
    EXPECT_EQ(scores[2].first, "alice");
    EXPECT_DOUBLE_EQ(scores[2].second, 10.0);

    client.del(key);
}

TEST(RedisClientTest, ZRevRank) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("zrank");

    client.del(key);
    client.zadd(key, 10.0, "alice");
    client.zadd(key, 100.0, "bob");
    client.zadd(key, 50.0, "carol");

    // bob has highest score so revrank 0
    auto rank_bob = client.zrevrank(key, "bob");
    ASSERT_TRUE(rank_bob.has_value());
    EXPECT_EQ(*rank_bob, 0);

    auto rank_carol = client.zrevrank(key, "carol");
    ASSERT_TRUE(rank_carol.has_value());
    EXPECT_EQ(*rank_carol, 1);

    auto rank_alice = client.zrevrank(key, "alice");
    ASSERT_TRUE(rank_alice.has_value());
    EXPECT_EQ(*rank_alice, 2);

    // Non-existent member
    EXPECT_EQ(client.zrevrank(key, "nobody"), std::nullopt);

    client.del(key);
}

TEST(RedisClientTest, ZScore) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("zsc");

    client.del(key);
    client.zadd(key, 42.5, "player1");

    auto score = client.zscore(key, "player1");
    ASSERT_TRUE(score.has_value());
    EXPECT_DOUBLE_EQ(*score, 42.5);

    EXPECT_EQ(client.zscore(key, "nobody"), std::nullopt);

    client.del(key);
}

TEST(RedisClientTest, ZCard) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("zcard");

    client.del(key);
    EXPECT_EQ(client.zcard(key), 0);

    client.zadd(key, 1.0, "a");
    client.zadd(key, 2.0, "b");
    client.zadd(key, 3.0, "c");
    EXPECT_EQ(client.zcard(key), 3);

    client.del(key);
}

// ── Reconnect ────────────────────────────────────────────────────────────

TEST(RedisClientTest, ReconnectAfterConnected) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("recon");

    EXPECT_TRUE(client.is_connected());
    // Reconnect while already connected should work
    EXPECT_TRUE(client.reconnect());
    EXPECT_TRUE(client.is_connected());

    // Operations should work after reconnect
    EXPECT_TRUE(client.set(key, "after_reconnect"));
    auto val = client.get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "after_reconnect");

    client.del(key);
}

TEST(RedisClientTest, DoubleReconnect) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("dblrecon");

    EXPECT_TRUE(client.reconnect());
    EXPECT_TRUE(client.reconnect());
    EXPECT_TRUE(client.is_connected());

    EXPECT_TRUE(client.set(key, "double"));
    client.del(key);
}

// ── Move semantics ───────────────────────────────────────────────────────

TEST(RedisClientTest, MoveSemantics) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("move");

    EXPECT_TRUE(client.set(key, "val"));

    RedisClient moved(std::move(client));
    EXPECT_TRUE(moved.is_connected());
    auto val = moved.get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "val");
    moved.del(key);
}

TEST(RedisClientTest, MoveAssign) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto client = make_connected_client();
    auto key = unique_prefix("moveas");

    EXPECT_TRUE(client.set(key, "mv"));

    RedisClient other = make_connected_client();
    other = std::move(client);
    EXPECT_TRUE(other.is_connected());
    auto val = other.get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "mv");
    other.del(key);
}

// ========================================================================
// RedisConnectionPool tests
// ========================================================================

// ── Graceful degradation (no Redis needed) ───────────────────────────────

TEST(RedisConnectionPoolTest, AcquireWhenRedisDownReturnsEmpty) {
    RedisConnectionPool::Config cfg;
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    cfg.acquire_timeout = std::chrono::milliseconds(200);
    RedisConnectionPool pool(cfg);

    auto conn = pool.acquire();
    EXPECT_FALSE(conn);
    EXPECT_EQ(pool.size(), 0U);
    EXPECT_EQ(pool.idle_count(), 0U);
}

// ── Live redis needed ────────────────────────────────────────────────────

TEST(RedisConnectionPoolTest, AcquireReturnsValidConnection) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    auto conn = pool.acquire();
    ASSERT_TRUE(conn);
    EXPECT_TRUE(conn->is_connected());
    EXPECT_EQ(pool.size(), 1U);
    EXPECT_EQ(pool.idle_count(), 0U);
}

TEST(RedisConnectionPoolTest, ReleaseReturnsToPool) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    {
        auto conn = pool.acquire();
        ASSERT_TRUE(conn);
        EXPECT_EQ(pool.idle_count(), 0U);
    }
    EXPECT_EQ(pool.idle_count(), 1U);
}

TEST(RedisConnectionPoolTest, AcquireAfterReleaseReusesConnection) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    auto key = unique_prefix("pool_reuse");
    {
        auto c1 = pool.acquire();
        ASSERT_TRUE(c1);
        c1->set(key, "reused_value");
    }
    EXPECT_EQ(pool.size(), 1U);

    auto c2 = pool.acquire();
    ASSERT_TRUE(c2);
    EXPECT_EQ(pool.size(), 1U);  // reused, not created
    auto val = c2->get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "reused_value");
    c2->del(key);
}

TEST(RedisConnectionPoolTest, MaxSizeEnforced) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 1;
    cfg.acquire_timeout = std::chrono::milliseconds(100);
    RedisConnectionPool pool(cfg);

    auto c1 = pool.acquire();
    ASSERT_TRUE(c1);

    auto c2 = pool.acquire();
    EXPECT_FALSE(c2);  // timeout — pool exhausted
}

TEST(RedisConnectionPoolTest, MultipleConnections) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 3;
    RedisConnectionPool pool(cfg);

    auto c1 = pool.acquire();
    ASSERT_TRUE(c1);
    auto c2 = pool.acquire();
    ASSERT_TRUE(c2);
    EXPECT_EQ(pool.size(), 2U);
    EXPECT_EQ(pool.idle_count(), 0U);
}

TEST(RedisConnectionPoolTest, MoveSemantics) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    RedisConnectionPool pool(cfg);

    auto key = unique_prefix("pool_move");
    auto c1 = pool.acquire();
    ASSERT_TRUE(c1);
    c1->set(key, "moved_val");

    auto c2 = std::move(c1);
    EXPECT_FALSE(c1);  // moved-from is empty
    ASSERT_TRUE(c2);
    auto val = c2->get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "moved_val");
    c2->del(key);
}

TEST(RedisConnectionPoolTest, ReleaseViaMoveAssignment) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    auto c1 = pool.acquire();
    ASSERT_TRUE(c1);
    EXPECT_EQ(pool.size(), 1U);

    // Move-assign from a default (empty) PooledConnection — c1 releases
    c1 = PooledConnection{};
    EXPECT_FALSE(c1);
    EXPECT_EQ(pool.idle_count(), 1U);
}

TEST(RedisConnectionPoolTest, DeadConnectionRevivedOnAcquire) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    auto key = unique_prefix("pool_revive");
    {
        auto c = pool.acquire();
        ASSERT_TRUE(c);
        // Connection goes out of scope — returned to pool.
    }

    auto c2 = pool.acquire();
    ASSERT_TRUE(c2);
    EXPECT_TRUE(c2->is_connected());
    c2->set(key, "revived");
    auto val = c2->get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "revived");
    c2->del(key);
}

// ========================================================================
// RedisEventStore tests
// ========================================================================

// ── Graceful degradation (no Redis needed) ───────────────────────────────

TEST(RedisEventStoreTest, NoRedisAppendReturnsFalse) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_prefix("es_down");
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisEventStore store(cfg);

    EXPECT_FALSE(store.redis_available());

    EventRecord e;
    e.event_type = "test";
    e.aggregate_id = "agg";
    e.payload = "{}";
    EXPECT_FALSE(store.append(e));
}

TEST(RedisEventStoreTest, NoRedisReadReturnsEmpty) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_prefix("es_down2");
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisEventStore store(cfg);

    EXPECT_TRUE(store.read("anything").empty());
    EXPECT_EQ(store.latest_sequence("anything"), 0U);
    EXPECT_TRUE(store.read_by_type("anything").empty());
    EXPECT_EQ(store.total_events(), 0U);
}

// ── Live redis needed ────────────────────────────────────────────────────

TEST(RedisEventStoreTest, AppendAndRead) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EventRecord e1;
    e1.event_type = "battle_result";
    e1.aggregate_id = "battle_001";
    e1.payload = R"({"winner":"alice"})";
    EXPECT_TRUE(store.append(e1));

    EventRecord e2;
    e2.event_type = "battle_result";
    e2.aggregate_id = "battle_001";
    e2.payload = R"({"winner":"bob"})";
    EXPECT_TRUE(store.append(e2));

    auto events = store.read("battle_001");
    ASSERT_GE(events.size(), 2U);
    EXPECT_EQ(events[0].aggregate_id, "battle_001");
    EXPECT_EQ(events[1].aggregate_id, "battle_001");
}

TEST(RedisEventStoreTest, AppendMultipleAggregates) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_multi");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EventRecord e1;
    e1.event_type = "login";
    e1.aggregate_id = "user_a";
    e1.payload = "{}";
    EXPECT_TRUE(store.append(e1));

    EventRecord e2;
    e2.event_type = "login";
    e2.aggregate_id = "user_b";
    e2.payload = "{}";
    EXPECT_TRUE(store.append(e2));

    auto a_events = store.read("user_a");
    ASSERT_GE(a_events.size(), 1U);
    EXPECT_EQ(a_events[0].aggregate_id, "user_a");

    auto b_events = store.read("user_b");
    ASSERT_GE(b_events.size(), 1U);
    EXPECT_EQ(b_events[0].aggregate_id, "user_b");
}

TEST(RedisEventStoreTest, LatestSequence) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_seq");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EXPECT_EQ(store.latest_sequence("empty_agg"), 0U);

    EventRecord e;
    e.event_type = "login";
    e.aggregate_id = "user_seq";
    e.payload = "{}";
    EXPECT_TRUE(store.append(e));

    auto seq = store.latest_sequence("user_seq");
    EXPECT_GT(seq, 0U);
}

TEST(RedisEventStoreTest, ReadByType) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_type");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EventRecord e1;
    e1.event_type = "room_created";
    e1.aggregate_id = "room_a";
    e1.payload = "{}";
    store.append(e1);

    EventRecord e2;
    e2.event_type = "battle_result";
    e2.aggregate_id = "battle_x";
    e2.payload = "{}";
    store.append(e2);

    auto rooms = store.read_by_type("room_created", 10);
    ASSERT_GE(rooms.size(), 1U);
    EXPECT_EQ(rooms[0].event_type, "room_created");

    auto battles = store.read_by_type("battle_result", 10);
    ASSERT_GE(battles.size(), 1U);
    EXPECT_EQ(battles[0].event_type, "battle_result");
}

TEST(RedisEventStoreTest, TotalEvents) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_total");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EventRecord e;
    e.event_type = "test";
    e.aggregate_id = "agg";
    e.payload = "{}";

    auto before = store.total_events();
    store.append(e);
    auto after = store.total_events();
    EXPECT_GT(after, before);
}

TEST(RedisEventStoreTest, FromSequenceFilter) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_from");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EventRecord e1;
    e1.event_type = "t";
    e1.aggregate_id = "a1";
    e1.payload = "first";
    store.append(e1);

    EventRecord e2;
    e2.event_type = "t";
    e2.aggregate_id = "a1";
    e2.payload = "second";
    store.append(e2);

    auto all = store.read("a1");
    ASSERT_GE(all.size(), 2U);

    auto from_second = store.read("a1", all[1].sequence);
    ASSERT_GE(from_second.size(), 1U);
    EXPECT_GE(from_second[0].sequence, all[1].sequence);
}

TEST(RedisEventStoreTest, MaxCountLimit) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_max");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    for (int i = 0; i < 5; ++i) {
        EventRecord e;
        e.event_type = "bulk";
        e.aggregate_id = "bulk_agg";
        e.payload = std::to_string(i);
        store.append(e);
    }

    auto limited = store.read("bulk_agg", 0, 2);
    ASSERT_LE(limited.size(), 2U);
}

TEST(RedisEventStoreTest, ClientAccess) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_client");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EXPECT_TRUE(store.redis_available());
    auto& client = store.client();
    EXPECT_TRUE(client.is_connected());
}

TEST(RedisEventStoreTest, TimestampAutoFill) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto prefix = unique_prefix("es_ts");
    RedisEventStore::Config cfg;
    cfg.key_prefix = prefix;
    RedisEventStore store(cfg);

    EventRecord e;
    e.event_type = "auto_ts";
    e.aggregate_id = "ts_agg";
    e.payload = "{}";
    e.timestamp_ms = 0;  // should be auto-filled
    EXPECT_TRUE(store.append(e));

    auto events = store.read("ts_agg");
    ASSERT_GE(events.size(), 1U);
    EXPECT_GT(events[0].timestamp_ms, 0U);
}

// ========================================================================
// RedisLeaderboard tests
// ========================================================================

// ── Graceful degradation (no Redis needed) ───────────────────────────────

TEST(RedisLeaderboardTest, NoRedisSubmitReturnsNullopt) {
    RedisLeaderboard::Config cfg;
    cfg.key = unique_prefix("lb_down");
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisLeaderboard lb(cfg);

    EXPECT_FALSE(lb.available());
    EXPECT_EQ(lb.submit("u1", "User1", 100), std::nullopt);
}

TEST(RedisLeaderboardTest, NoRedisTopKReturnsEmpty) {
    RedisLeaderboard::Config cfg;
    cfg.key = unique_prefix("lb_down2");
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisLeaderboard lb(cfg);

    EXPECT_TRUE(lb.top_k(10).empty());
}

TEST(RedisLeaderboardTest, NoRedisRankOfReturnsNullopt) {
    RedisLeaderboard::Config cfg;
    cfg.key = unique_prefix("lb_down3");
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisLeaderboard lb(cfg);

    EXPECT_EQ(lb.rank_of("u1"), std::nullopt);
}

TEST(RedisLeaderboardTest, NoRedisSizeReturnsZero) {
    RedisLeaderboard::Config cfg;
    cfg.key = unique_prefix("lb_down4");
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisLeaderboard lb(cfg);

    EXPECT_EQ(lb.size(), 0U);
}

// ── Live redis needed ────────────────────────────────────────────────────

TEST(RedisLeaderboardTest, SubmitReturnsRank) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_submit");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    auto rank1 = lb.submit("player1", "Player One", 100);
    ASSERT_TRUE(rank1.has_value());
    EXPECT_EQ(*rank1, 1);  // first entry, rank 1

    auto rank2 = lb.submit("player2", "Player Two", 200);
    ASSERT_TRUE(rank2.has_value());
    EXPECT_EQ(*rank2, 1);  // higher score, rank 1

    // player1 is now rank 2
    auto rp = lb.rank_of("player1");
    ASSERT_TRUE(rp.has_value());
    EXPECT_EQ(rp->rank, 2);
}

TEST(RedisLeaderboardTest, TopK) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_topk");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    lb.submit("a", "Alice", 50);
    lb.submit("b", "Bob", 200);
    lb.submit("c", "Carol", 100);

    auto top = lb.top_k(2);
    ASSERT_EQ(top.size(), 2U);
    EXPECT_EQ(top[0].user_id, "b");     // Bob rank 1
    EXPECT_EQ(top[0].score, 200);
    EXPECT_EQ(top[0].rank, 1);
    EXPECT_EQ(top[1].user_id, "c");     // Carol rank 2
    EXPECT_EQ(top[1].score, 100);
    EXPECT_EQ(top[1].rank, 2);
}

TEST(RedisLeaderboardTest, TopKAll) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_topk_all");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    lb.submit("x", "X", 10);
    lb.submit("y", "Y", 20);
    lb.submit("z", "Z", 30);

    auto all = lb.top_k(10);
    ASSERT_EQ(all.size(), 3U);
    EXPECT_EQ(all[0].user_id, "z");
    EXPECT_EQ(all[1].user_id, "y");
    EXPECT_EQ(all[2].user_id, "x");
}

TEST(RedisLeaderboardTest, TopKWithEmptyLeaderboard) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_topk_empty");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    auto top = lb.top_k(10);
    EXPECT_TRUE(top.empty());
}

TEST(RedisLeaderboardTest, RankOf) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_rankof");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    lb.submit("p1", "Player1", 75);
    lb.submit("p2", "Player2", 150);

    auto r1 = lb.rank_of("p1");
    ASSERT_TRUE(r1.has_value());
    EXPECT_EQ(r1->user_id, "p1");
    EXPECT_EQ(r1->rank, 2);
    EXPECT_EQ(r1->score, 75);

    auto r2 = lb.rank_of("p2");
    ASSERT_TRUE(r2.has_value());
    EXPECT_EQ(r2->user_id, "p2");
    EXPECT_EQ(r2->rank, 1);
    EXPECT_EQ(r2->score, 150);
}

TEST(RedisLeaderboardTest, RankOfNonExistent) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_rankne");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    EXPECT_EQ(lb.rank_of("nobody"), std::nullopt);
}

TEST(RedisLeaderboardTest, ScoreUpdateReorders) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_update");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    lb.submit("player_a", "A", 100);
    lb.submit("player_b", "B", 200);

    // Update player_a to have a higher score than player_b
    auto new_rank = lb.submit("player_a", "A", 300);
    ASSERT_TRUE(new_rank.has_value());
    EXPECT_EQ(*new_rank, 1);  // now rank 1

    auto r = lb.rank_of("player_a");
    ASSERT_TRUE(r.has_value());
    EXPECT_EQ(r->rank, 1);
    EXPECT_EQ(r->score, 300);

    auto r2 = lb.rank_of("player_b");
    ASSERT_TRUE(r2.has_value());
    EXPECT_EQ(r2->rank, 2);
}

TEST(RedisLeaderboardTest, Size) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_size");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    EXPECT_EQ(lb.size(), 0U);

    lb.submit("a", "A", 10);
    lb.submit("b", "B", 20);
    EXPECT_EQ(lb.size(), 2U);

    lb.submit("c", "C", 30);
    EXPECT_EQ(lb.size(), 3U);
}

TEST(RedisLeaderboardTest, DisplayNameResolved) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_dname");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    lb.submit("uid42", "Sir Alice", 999);

    auto r = lb.rank_of("uid42");
    ASSERT_TRUE(r.has_value());
    EXPECT_EQ(r->display_name, "Sir Alice");
    EXPECT_EQ(r->score, 999);
    EXPECT_EQ(r->rank, 1);

    auto top = lb.top_k(10);
    ASSERT_GE(top.size(), 1U);
    EXPECT_EQ(top[0].display_name, "Sir Alice");
}

TEST(RedisLeaderboardTest, Available) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    auto key = unique_prefix("lb_avail");
    RedisLeaderboard::Config cfg;
    cfg.key = key;
    cfg.redis.timeout = std::chrono::milliseconds(1000);
    RedisLeaderboard lb(cfg);

    EXPECT_TRUE(lb.available());
}
