// SDK v4.2.0 C3: Transport abstraction + ConnectionPool tests.
#include <gtest/gtest.h>
#include "boost_gateway/sdk/transport/transport.h"

#include <boost/asio.hpp>
#include <atomic>
#include <future>
#include <memory>
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

TEST(TransportTest, ConnectedTransportMayBeDestroyedWithoutExplicitDisconnect) {
    boost::asio::io_context io;
    boost::asio::ip::tcp::acceptor acceptor(
        io, {boost::asio::ip::tcp::v4(), 0});
    std::thread server([&] {
        boost::asio::ip::tcp::socket socket(io);
        acceptor.accept(socket);
        std::this_thread::sleep_for(100ms);
    });
    {
        auto transport = make_tcp_transport();
        ASSERT_TRUE(transport->connect("127.0.0.1", acceptor.local_endpoint().port(), 1s));
    }
    server.join();
}

TEST(TransportTest, AsyncReceiveCallbackMayDestroyTransport) {
    boost::asio::io_context io;
    boost::asio::ip::tcp::acceptor acceptor(
        io, {boost::asio::ip::tcp::v4(), 0});
    std::promise<void> send_data;
    auto send_future = send_data.get_future();
    std::thread server([&] {
        boost::asio::ip::tcp::socket socket(io);
        acceptor.accept(socket);
        send_future.wait();
        boost::asio::write(socket, boost::asio::buffer("hello", 5));
        std::this_thread::sleep_for(100ms);
    });

    auto transport = make_tcp_transport();
    std::promise<void> destroyed;
    auto destroyed_future = destroyed.get_future();
    transport->set_async_receive_callback([&](const std::string& data) {
        EXPECT_EQ(data, "hello");
        transport.reset();
        destroyed.set_value();
    });
    std::promise<bool> connected;
    auto connected_future = connected.get_future();
    transport->async_connect("127.0.0.1", acceptor.local_endpoint().port(),
                             [&](bool ok) { connected.set_value(ok); });
    ASSERT_TRUE(connected_future.get());
    send_data.set_value();
    EXPECT_EQ(destroyed_future.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(transport, nullptr);
    server.join();
}

TEST(TransportTest, DestructionCancelsInflightAsyncConnectWithoutCallback) {
    boost::asio::io_context io;
    boost::asio::ip::tcp::acceptor unused_port(io,
                                                {boost::asio::ip::tcp::v4(), 0});
    const auto port = unused_port.local_endpoint().port();
    unused_port.close();
    std::atomic<bool> callback_called{false};
    const auto started = std::chrono::steady_clock::now();
    {
        auto transport = make_tcp_transport();
        transport->async_connect("127.0.0.1", port,
                                 [&](bool) { callback_called = true; });
        std::this_thread::sleep_for(50ms);
    }
    EXPECT_FALSE(callback_called);
    EXPECT_LT(std::chrono::steady_clock::now() - started, 1s);
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
