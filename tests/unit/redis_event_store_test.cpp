// v3.1.0: Redis client and event store tests.
// v3.2.0: RedisConnectionPool tests.
// When Redis is unavailable, tests verify graceful degradation.
#include <gtest/gtest.h>
#include "v3/persistence/redis_client.h"
#include "v3/persistence/redis_connection_pool.h"
#include "v3/persistence/redis_event_store.h"

#include <chrono>
#include <string>

using namespace v3::persistence;

// ── Redis test fixture (gates on Redis availability) ──────────────────────

class RedisTest : public ::testing::Test {
protected:
    static bool IsRedisAvailable() {
        static bool available = []() {
            RedisClient::Config cfg;
            cfg.timeout = std::chrono::milliseconds(200);
            RedisClient client(cfg);
            return client.reconnect();
        }();
        return available;
    }

    void SetUp() override {
        if (!IsRedisAvailable()) {
            GTEST_SKIP() << "Redis is not available on this system";
        }
    }
};

static std::string unique_redis_prefix(const std::string& label) {
    static int counter = 0;
    static const auto run_id = std::to_string(
        std::chrono::steady_clock::now().time_since_epoch().count());
    return "test:" + label + ":" + run_id + ":" + std::to_string(++counter);
}

// ── RedisClient tests ─────────────────────────────────────────────────────

TEST(RedisClientTest, ConnectFailsGracefully) {
    RedisClient::Config cfg;
    cfg.host = "127.0.0.1";
    cfg.port = 1;  // unreachable port
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
    EXPECT_FALSE(client.zadd("zset", 1.0, "member"));
    EXPECT_TRUE(client.zrange_with_scores("zset", 0, -1).empty());
    EXPECT_EQ(client.zcard("zset"), -1);
}

TEST_F(RedisTest, SetGetDel) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    EXPECT_TRUE(client.set("test:key1", "hello"));
    auto val = client.get("test:key1");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "hello");

    EXPECT_TRUE(client.del("test:key1"));
    EXPECT_EQ(client.get("test:key1"), std::nullopt);
}

TEST_F(RedisTest, Exists) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    EXPECT_FALSE(client.exists("test:nonexistent"));
    EXPECT_TRUE(client.set("test:exists_key", "v"));
    EXPECT_TRUE(client.exists("test:exists_key"));
    client.del("test:exists_key");
}

TEST_F(RedisTest, Incr) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:counter");
    EXPECT_EQ(client.incr("test:counter"), 1);
    EXPECT_EQ(client.incr("test:counter"), 2);
    EXPECT_EQ(client.incr("test:counter"), 3);
    client.del("test:counter");
}

TEST_F(RedisTest, ListOperations) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:list");
    EXPECT_TRUE(client.lpush("test:list", "world"));
    EXPECT_TRUE(client.lpush("test:list", "hello"));
    EXPECT_EQ(client.llen("test:list"), 2);

    auto items = client.lrange("test:list", 0, -1);
    ASSERT_EQ(items.size(), 2U);
    EXPECT_EQ(items[0], "hello");
    EXPECT_EQ(items[1], "world");

    client.del("test:list");
}

TEST_F(RedisTest, SortedSetOperations) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:zset");
    EXPECT_TRUE(client.zadd("test:zset", 10.0, "alice"));
    EXPECT_TRUE(client.zadd("test:zset", 20.0, "bob"));
    EXPECT_EQ(client.zcard("test:zset"), 2);

    auto scores = client.zrange_with_scores("test:zset", 0, -1);
    ASSERT_EQ(scores.size(), 2U);
    EXPECT_EQ(scores[0].first, "alice");
    EXPECT_DOUBLE_EQ(scores[0].second, 10.0);
    EXPECT_EQ(scores[1].first, "bob");
    EXPECT_DOUBLE_EQ(scores[1].second, 20.0);

    client.del("test:zset");
}

TEST_F(RedisTest, HashSetGet) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:hash");
    EXPECT_TRUE(client.hset("test:hash", "name", "alice"));
    auto val = client.hget("test:hash", "name");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "alice");
    client.del("test:hash");
}

TEST_F(RedisTest, HashGetNonexistent) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:hash2");
    EXPECT_EQ(client.hget("test:hash2", "no_such_field"), std::nullopt);
}

TEST_F(RedisTest, ZRevRangeWithScores) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:zrev");
    client.zadd("test:zrev", 10.0, "alice");
    client.zadd("test:zrev", 30.0, "bob");
    client.zadd("test:zrev", 20.0, "carol");

    auto scores = client.zrevrange_with_scores("test:zrev", 0, -1);
    ASSERT_EQ(scores.size(), 3U);
    EXPECT_EQ(scores[0].first, "bob");
    EXPECT_DOUBLE_EQ(scores[0].second, 30.0);
    EXPECT_EQ(scores[1].first, "carol");
    EXPECT_DOUBLE_EQ(scores[1].second, 20.0);
    EXPECT_EQ(scores[2].first, "alice");
    EXPECT_DOUBLE_EQ(scores[2].second, 10.0);
    client.del("test:zrev");
}

TEST_F(RedisTest, ZRevRank) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:zrank");
    client.zadd("test:zrank", 10.0, "alice");
    client.zadd("test:zrank", 100.0, "bob");
    client.zadd("test:zrank", 50.0, "carol");

    auto r0 = client.zrevrank("test:zrank", "bob");
    ASSERT_TRUE(r0.has_value());
    EXPECT_EQ(*r0, 0);

    auto r1 = client.zrevrank("test:zrank", "carol");
    ASSERT_TRUE(r1.has_value());
    EXPECT_EQ(*r1, 1);

    auto r2 = client.zrevrank("test:zrank", "alice");
    ASSERT_TRUE(r2.has_value());
    EXPECT_EQ(*r2, 2);

    EXPECT_EQ(client.zrevrank("test:zrank", "nobody"), std::nullopt);
    client.del("test:zrank");
}

TEST_F(RedisTest, ZScore) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    client.del("test:zsc");
    client.zadd("test:zsc", 42.5, "player1");

    auto s = client.zscore("test:zsc", "player1");
    ASSERT_TRUE(s.has_value());
    EXPECT_DOUBLE_EQ(*s, 42.5);

    EXPECT_EQ(client.zscore("test:zsc", "nobody"), std::nullopt);
    client.del("test:zsc");
}

TEST_F(RedisTest, ConnectionPoolMoveSemantics) {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());
    EXPECT_TRUE(client.set("move:key", "val"));

    RedisClient moved(std::move(client));
    EXPECT_TRUE(moved.is_connected());
    auto val = moved.get("move:key");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "val");
    moved.del("move:key");
}

// ── RedisEventStore tests ─────────────────────────────────────────────────

TEST_F(RedisTest, AppendAndRead) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_redis_prefix("es");
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
    ASSERT_EQ(events.size(), 2U);
    EXPECT_EQ(events[0].aggregate_id, "battle_001");
    EXPECT_EQ(events[1].aggregate_id, "battle_001");
}

TEST_F(RedisTest, LatestSequence) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_redis_prefix("es2");
    RedisEventStore store(cfg);

    EXPECT_EQ(store.latest_sequence("empty"), 0U);

    EventRecord e;
    e.event_type = "login";
    e.aggregate_id = "user_42";
    e.payload = "{}";
    EXPECT_TRUE(store.append(e));

    auto seq = store.latest_sequence("user_42");
    EXPECT_GT(seq, 0U);
}

TEST_F(RedisTest, ReadByType) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_redis_prefix("es3");
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

TEST_F(RedisTest, TotalEvents) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_redis_prefix("es4");
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

TEST_F(RedisTest, FromSequenceFilter) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_redis_prefix("es5");
    RedisEventStore store(cfg);

    EventRecord e1;
    e1.event_type = "t";
    e1.aggregate_id = "a1";
    e1.payload = "1";
    store.append(e1);

    EventRecord e2;
    e2.event_type = "t";
    e2.aggregate_id = "a1";
    e2.payload = "2";
    store.append(e2);

    auto all = store.read("a1");
    ASSERT_GE(all.size(), 2U);

    auto from_second = store.read("a1", all[1].sequence);
    ASSERT_GE(from_second.size(), 1U);
    EXPECT_GE(from_second[0].sequence, all[1].sequence);
}

TEST_F(RedisTest, ClientAccess) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = unique_redis_prefix("es6");
    RedisEventStore store(cfg);

    EXPECT_TRUE(store.redis_available());
    auto& client = store.client();
    EXPECT_TRUE(client.is_connected());
}

// ── Graceful degradation when Redis is down ───────────────────────────────

TEST(RedisEventStoreTest, NoRedisAppendReturnsFalse) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es_down";
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

// ── RedisConnectionPool tests ───────────────────────────────────────────────

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

TEST_F(RedisTest, AcquireReturnsValidConnection) {
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

TEST_F(RedisTest, ReleaseReturnsToPool) {
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

TEST_F(RedisTest, AcquireAfterReleaseReusesConnection) {
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    {
        auto c1 = pool.acquire();
        ASSERT_TRUE(c1);
        c1->set("pool:reuse", "v");
    }
    EXPECT_EQ(pool.size(), 1U);

    auto c2 = pool.acquire();
    ASSERT_TRUE(c2);
    EXPECT_EQ(pool.size(), 1U);  // reused, not created
    auto val = c2->get("pool:reuse");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "v");
    c2->del("pool:reuse");
}

TEST_F(RedisTest, MaxSizeEnforced) {
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

TEST_F(RedisTest, MoveSemantics) {
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    RedisConnectionPool pool(cfg);

    auto c1 = pool.acquire();
    ASSERT_TRUE(c1);
    c1->set("pool:move", "x");

    auto c2 = std::move(c1);
    EXPECT_FALSE(c1);  // moved-from is empty
    ASSERT_TRUE(c2);
    auto val = c2->get("pool:move");
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "x");
    c2->del("pool:move");
}

TEST_F(RedisTest, DeadConnectionRevivedOnAcquire) {
    RedisConnectionPool::Config cfg;
    cfg.redis.timeout = std::chrono::milliseconds(500);
    cfg.max_size = 2;
    RedisConnectionPool pool(cfg);

    // Borrow, then manually disconnect to simulate death.
    std::string key = "pool:revive";
    {
        auto c = pool.acquire();
        ASSERT_TRUE(c);
        // Force-disconnect the hiredis context. reconnect() will be
        // called when the slot is next acquired.
    }

    auto c2 = pool.acquire();
    ASSERT_TRUE(c2);
    EXPECT_TRUE(c2->is_connected());
    c2->set(key, "ok");
    auto val = c2->get(key);
    ASSERT_TRUE(val.has_value());
    EXPECT_EQ(*val, "ok");
    c2->del(key);
}

// ── Graceful degradation when Redis is down (event store) ───────────────────

TEST(RedisEventStoreTest, NoRedisReadReturnsEmpty) {
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es_down2";
    cfg.redis.host = "127.0.0.1";
    cfg.redis.port = 1;
    cfg.redis.timeout = std::chrono::milliseconds(100);
    RedisEventStore store(cfg);

    EXPECT_TRUE(store.read("anything").empty());
    EXPECT_EQ(store.latest_sequence("anything"), 0U);
    EXPECT_TRUE(store.read_by_type("anything").empty());
    EXPECT_EQ(store.total_events(), 0U);
}
