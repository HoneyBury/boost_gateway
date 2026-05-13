// SDK v4.0.0 C3: Transport abstraction + ConnectionPool tests.
#include <gtest/gtest.h>
#include "boost_gateway/sdk/transport/transport.h"

#include <thread>

using namespace boost_gateway::sdk::transport;
using namespace std::chrono_literals;

// ── TcpTransport tests ─────────────────────────────────────────────────

TEST(TransportTest, ConnectFailsGracefully) {
    auto t = make_tcp_transport();
    EXPECT_FALSE(t->is_connected());
    EXPECT_FALSE(t->connect("127.0.0.1", 1, 100ms));
    EXPECT_FALSE(t->is_connected());
    t->disconnect();  // safe to disconnect unconnected
}

TEST(TransportTest, SendFailsWhenDisconnected) {
    auto t = make_tcp_transport();
    std::vector<char> data{'h', 'e', 'l', 'l', 'o'};
    EXPECT_FALSE(t->send(data));
}

TEST(TransportTest, ReceiveEmptyWhenDisconnected) {
    auto t = make_tcp_transport();
    auto result = t->receive(50ms);
    EXPECT_TRUE(result.empty());
}

// ── ConnectionPool tests ───────────────────────────────────────────────

TEST(ConnectionPoolTest, InitialState) {
    PoolConfig cfg{.max_connections = 3};
    ConnectionPool pool(cfg, make_tcp_transport);
    EXPECT_EQ(pool.available(), 0U);
    EXPECT_EQ(pool.total(), 0U);
}

TEST(ConnectionPoolTest, ConnectAllPreconnects) {
    PoolConfig cfg{
        .max_connections = 2,
        .connect_timeout = 100ms,
    };
    ConnectionPool pool(cfg, make_tcp_transport);

    // connect_all to an unreachable port should fail gracefully
    bool ok = pool.connect_all("127.0.0.1", 1);
    EXPECT_FALSE(ok);
    EXPECT_EQ(pool.total(), 0U);

    pool.disconnect_all();
}

TEST(ConnectionPoolTest, AcquireCreatesTransport) {
    PoolConfig cfg{
        .max_connections = 2,
        .connect_timeout = 100ms,
    };
    ConnectionPool pool(cfg, make_tcp_transport);

    // Acquire should create a transport even without connect_all
    // (it just won't be connected to anything yet).
    auto* t = pool.acquire();
    EXPECT_NE(t, nullptr);
    EXPECT_EQ(pool.available(), 0U);
    EXPECT_EQ(pool.total(), 1U);
    EXPECT_FALSE(t->is_connected());

    pool.release(t);
    EXPECT_EQ(pool.available(), 1U);
    EXPECT_EQ(pool.total(), 1U);

    pool.disconnect_all();
    EXPECT_EQ(pool.total(), 0U);
}

TEST(ConnectionPoolTest, RespectsMaxConnections) {
    PoolConfig cfg{
        .max_connections = 2,
        .connect_timeout = 100ms,
    };
    ConnectionPool pool(cfg, make_tcp_transport);

    auto* a = pool.acquire();
    auto* b = pool.acquire();
    EXPECT_NE(a, nullptr);
    EXPECT_NE(b, nullptr);
    EXPECT_NE(a, b);
    EXPECT_EQ(pool.total(), 2U);

    // Third acquire should block since max is 2 and none released.
    // Use a separate thread to release one after a short delay.
    std::thread releaser([&]() {
        std::this_thread::sleep_for(50ms);
        pool.release(a);
    });

    auto* c = pool.acquire();
    EXPECT_NE(c, nullptr);
    EXPECT_EQ(c, a);  // should get the released transport back

    releaser.join();
    pool.release(c);
    pool.release(b);
    pool.disconnect_all();
}

TEST(ConnectionPoolTest, AvailableCount) {
    PoolConfig cfg{
        .max_connections = 3,
        .connect_timeout = 100ms,
    };
    ConnectionPool pool(cfg, make_tcp_transport);

    auto* a = pool.acquire();
    auto* b = pool.acquire();
    EXPECT_EQ(pool.available(), 0U);

    pool.release(a);
    EXPECT_EQ(pool.available(), 1U);

    pool.release(b);
    EXPECT_EQ(pool.available(), 2U);

    // Acquiring again should consume from idle
    auto* c = pool.acquire();
    EXPECT_EQ(pool.available(), 1U);

    pool.release(c);
    pool.disconnect_all();
}
