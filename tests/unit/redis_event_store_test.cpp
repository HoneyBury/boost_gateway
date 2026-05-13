// v3.1.0: Redis client and event store tests.
// When Redis is unavailable, tests verify graceful degradation.
#include <gtest/gtest.h>
#include "v3/persistence/redis_client.h"
#include "v3/persistence/redis_event_store.h"

using namespace v3::persistence;

// ── Redis availability check (shared by all tests) ────────────────────────

static bool is_redis_running() {
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(200);
    RedisClient client(cfg);
    return client.reconnect();
}

static bool redis_available = is_redis_running();

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

TEST(RedisClientTest, SetGetDel) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
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

TEST(RedisClientTest, Exists) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisClient::Config cfg;
    cfg.timeout = std::chrono::milliseconds(500);
    RedisClient client(cfg);
    ASSERT_TRUE(client.reconnect());

    EXPECT_FALSE(client.exists("test:nonexistent"));
    EXPECT_TRUE(client.set("test:exists_key", "v"));
    EXPECT_TRUE(client.exists("test:exists_key"));
    client.del("test:exists_key");
}

TEST(RedisClientTest, Incr) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
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

TEST(RedisClientTest, ListOperations) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
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

TEST(RedisClientTest, SortedSetOperations) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
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

TEST(RedisClientTest, MoveSemantics) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
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

TEST(RedisEventStoreTest, AppendAndRead) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es";
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

TEST(RedisEventStoreTest, LatestSequence) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es2";
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

TEST(RedisEventStoreTest, ReadByType) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es3";
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
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es4";
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
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es5";
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

TEST(RedisEventStoreTest, ClientAccess) {
    if (!redis_available) GTEST_SKIP() << "Redis not running";
    RedisEventStore::Config cfg;
    cfg.key_prefix = "test:es6";
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
