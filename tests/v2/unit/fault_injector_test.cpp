#include <gtest/gtest.h>

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <boost/asio.hpp>
#include <boost/system/error_code.hpp>

#include "v2/test/fault_injector.h"

using namespace std::chrono_literals;

using v2::test::LatencyInjector;
using v2::test::FailureInjector;
using v2::test::NetworkPartitionSimulator;

// ============================================================================
// Helpers
// ============================================================================

namespace {

using tcp = boost::asio::ip::tcp;

// ------------------------------------------------------------------
// EchoServer: a minimal single-threaded TCP echo server for tests.
// ------------------------------------------------------------------
class EchoServer {
public:
    explicit EchoServer(uint16_t port = 0)
        : acceptor_(io_, tcp::endpoint(tcp::v4(), port)) {
        acceptor_.set_option(tcp::acceptor::reuse_address(true));
        port_ = acceptor_.local_endpoint().port();
    }

    ~EchoServer() { stop(); }

    void start() {
        running_ = true;
        thread_ = std::thread([this]() { acceptor_loop(); });
    }

    void stop() {
        running_ = false;
        boost::system::error_code ec;
        close_active_client();
        if (acceptor_.is_open()) {
            tcp::socket wake_socket(io_);
            wake_socket.connect(
                tcp::endpoint(boost::asio::ip::address_v4::loopback(), port_),
                ec);
            wake_socket.close(ec);
        }
        if (thread_.joinable()) {
            thread_.join();
        }
        acceptor_.close(ec);
    }

    uint16_t port() const { return port_; }

private:
    void acceptor_loop() {
        while (running_) {
            boost::system::error_code ec;
            tcp::socket sock(io_);
            acceptor_.accept(sock, ec);
            if (ec || !running_) {
                break;
            }
            auto client = std::make_shared<tcp::socket>(std::move(sock));
            {
                std::lock_guard<std::mutex> lock(client_mutex_);
                active_client_ = client;
            }
            echo_client(client);
            {
                std::lock_guard<std::mutex> lock(client_mutex_);
                if (active_client_ == client) {
                    active_client_.reset();
                }
            }
        }
    }

    void echo_client(const std::shared_ptr<tcp::socket>& sock) {
        std::array<char, 4096> buf;
        boost::system::error_code ec;
        while (running_ && sock && sock->is_open()) {
            auto len = sock->read_some(boost::asio::buffer(buf), ec);
            if (ec) {
                break;
            }
            boost::asio::write(*sock, boost::asio::buffer(buf, len), ec);
            if (ec) {
                break;
            }
        }
    }

    void close_active_client() {
        std::shared_ptr<tcp::socket> client;
        {
            std::lock_guard<std::mutex> lock(client_mutex_);
            client = active_client_;
        }
        if (!client) {
            return;
        }

        boost::system::error_code ec;
        client->cancel(ec);
        client->shutdown(tcp::socket::shutdown_both, ec);
        client->close(ec);
    }

    boost::asio::io_context io_;
    tcp::acceptor acceptor_;
    uint16_t port_{0};
    std::atomic<bool> running_{false};
    std::mutex client_mutex_;
    std::shared_ptr<tcp::socket> active_client_;
    std::thread thread_;
};

// ------------------------------------------------------------------
// Connect to a TCP port, send data, and read back the response.
// Uses a read timeout so that tests do not hang indefinitely.
// ------------------------------------------------------------------
std::string tcp_send_recv(uint16_t port, const std::string& data,
                          std::chrono::milliseconds timeout = 3s) {
    boost::asio::io_context io;
    tcp::socket sock(io);
    boost::system::error_code ec;

    sock.connect(
        tcp::endpoint(boost::asio::ip::address_v4::loopback(), port), ec);
    if (ec) {
        return {};
    }

    boost::asio::write(sock, boost::asio::buffer(data), ec);
    if (ec) {
        return {};
    }

    // Async read with a deadline timer.
    std::array<char, 4096> buf;
    std::size_t len = 0;
    bool read_done = false;

    boost::asio::steady_timer timer(io, timeout);
    timer.async_wait([&](const boost::system::error_code& tec) {
        if (!tec) {
            sock.cancel(ec);
        }
    });

    sock.async_read_some(
        boost::asio::buffer(buf),
        [&](const boost::system::error_code& rec, std::size_t n) {
            ec = rec;
            len = n;
            read_done = true;
        });

    io.run();
    if (ec || len == 0) {
        return {};
    }
    return std::string(buf.data(), len);
}

}  // anonymous namespace

// ============================================================================
// LatencyInjector Tests
// ============================================================================

// ─── Fixed delay is within expected bounds ──────────────────────────

TEST(V2FaultInjectorTest, LatencyInjectorFixedDelay) {
    int call_count = 0;
    LatencyInjector injector([&]() { ++call_count; }, 50ms);

    auto start = std::chrono::steady_clock::now();
    injector();
    auto elapsed = std::chrono::steady_clock::now() - start;

    EXPECT_EQ(call_count, 1);
    // sleep_for should wait at least the requested duration; allow
    // generous bounds for scheduler noise on CI / Windows.
    EXPECT_GE(elapsed, 40ms) << "delay was too short";
    EXPECT_LE(elapsed, 500ms) << "delay was too long";
}

// ─── Range delay stays within configured min and max ────────────────

TEST(V2FaultInjectorTest, LatencyInjectorRangeDelay) {
    int call_count = 0;
    LatencyInjector injector([&]() { ++call_count; }, 10ms, 100ms);

    for (int i = 0; i < 20; ++i) {
        auto start = std::chrono::steady_clock::now();
        injector();
        auto elapsed = std::chrono::steady_clock::now() - start;
        // Each delay should be roughly within [10ms, 100ms]
        EXPECT_LE(elapsed, 500ms) << "iteration " << i;
    }

    EXPECT_EQ(call_count, 20);
}

// ─── Changing delay at runtime works ───────────────────────────────

TEST(V2FaultInjectorTest, LatencyInjectorSetDelay) {
    int call_count = 0;
    LatencyInjector injector([&]() { ++call_count; }, 200ms);

    // Change to a shorter fixed delay.
    injector.set_delay(10ms);

    auto start = std::chrono::steady_clock::now();
    injector();
    auto elapsed = std::chrono::steady_clock::now() - start;

    EXPECT_EQ(call_count, 1);
    EXPECT_LE(elapsed, 100ms) << "delay after set_delay was too long";
}

// ============================================================================
// FailureInjector Tests
// ============================================================================

// ─── Rate of 0.0 never fails ────────────────────────────────────────

TEST(V2FaultInjectorTest, FailureInjectorNeverFails) {
    FailureInjector injector(0.0);

    for (int i = 0; i < 1000; ++i) {
        EXPECT_FALSE(injector.should_fail()) << "iteration " << i;
    }
}

// ─── Rate of 1.0 always fails ───────────────────────────────────────

TEST(V2FaultInjectorTest, FailureInjectorAlwaysFails) {
    FailureInjector injector(1.0);

    for (int i = 0; i < 1000; ++i) {
        EXPECT_TRUE(injector.should_fail()) << "iteration " << i;
    }
}

// ─── Rate of 0.5 converges within 5 % over 10 000 iterations ───────

TEST(V2FaultInjectorTest, FailureInjectorRateConverges) {
    FailureInjector injector(0.5);
    constexpr int kIterations = 10000;
    constexpr double kTolerance = 0.05;

    int failures = 0;
    for (int i = 0; i < kIterations; ++i) {
        if (injector.should_fail()) {
            ++failures;
        }
    }

    double observed_rate = static_cast<double>(failures) / kIterations;
    EXPECT_NEAR(observed_rate, 0.5, kTolerance);
}

// ─── set_rate() takes effect immediately ────────────────────────────

TEST(V2FaultInjectorTest, FailureInjectorSetRate) {
    FailureInjector injector(0.0);
    EXPECT_DOUBLE_EQ(injector.rate(), 0.0);

    injector.set_rate(0.75);
    EXPECT_DOUBLE_EQ(injector.rate(), 0.75);

    int failures = 0;
    for (int i = 0; i < 10000; ++i) {
        if (injector.should_fail()) {
            ++failures;
        }
    }

    double observed_rate = static_cast<double>(failures) / 10000;
    EXPECT_NEAR(observed_rate, 0.75, 0.05);
}

// ============================================================================
// NetworkPartitionSimulator Tests
// ============================================================================

// ─── Basic relay: data sent reaches echo server and is returned ─────

TEST(V2FaultInjectorTest, NetworkPartitionSimulatorBasicRelay) {
    EchoServer echo;
    echo.start();

    NetworkPartitionSimulator relay(0, "127.0.0.1", echo.port());
    relay.start();
    ASSERT_TRUE(relay.is_running());

    std::string payload = "Hello, relay!";
    auto response = tcp_send_recv(relay.listen_port(), payload);
    EXPECT_EQ(response, payload);

    relay.stop();
    echo.stop();
}

// ─── Drop after byte threshold ──────────────────────────────────────

TEST(V2FaultInjectorTest, NetworkPartitionSimulatorDropAfterBytes) {
    EchoServer echo;
    echo.start();

    NetworkPartitionSimulator relay(0, "127.0.0.1", echo.port());
    relay.set_drop_after_bytes(12);  // Allow ~12 bytes then drop.
    relay.start();
    ASSERT_TRUE(relay.is_running());

    // Small payload under the threshold: should be relayed.
    auto r1 = tcp_send_recv(relay.listen_port(), "hi", 3s);
    EXPECT_EQ(r1, "hi");

    // Larger payload after the threshold: relay should drop it.
    auto r2 = tcp_send_recv(relay.listen_port(), std::string(200, 'X'), 3s);
    EXPECT_TRUE(r2.empty());

    relay.stop();
    echo.stop();
}

// ─── Stop / start cycle works ───────────────────────────────────────

TEST(V2FaultInjectorTest, NetworkPartitionSimulatorStopStart) {
    EchoServer echo;
    echo.start();

    NetworkPartitionSimulator relay(0, "127.0.0.1", echo.port());
    relay.start();
    ASSERT_TRUE(relay.is_running());
    relay.stop();
    EXPECT_FALSE(relay.is_running());

    // Can start again on the same instance (port 0 picks a new port).
    relay.start();
    ASSERT_TRUE(relay.is_running());

    std::string payload = "Second round";
    auto response = tcp_send_recv(relay.listen_port(), payload, 3s);
    EXPECT_EQ(response, payload);

    relay.stop();
    echo.stop();
}
