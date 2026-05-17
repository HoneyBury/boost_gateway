#pragma once

#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <gtest/gtest.h>

// ─── Forward declarations ──────────────────────────────────────────
// v2_test namespace to avoid leaking aliases to includers
namespace v2_test {

// ─── ProcessGuard ──────────────────────────────────────────────────
// RAII wrapper around a child OS process (boost::process under the hood).
//
// Spawns a binary on construction, sends a graceful shutdown signal on
// destruction, waits up to 5 s, then force-kills.
class ProcessGuard {
public:
    ProcessGuard() = default;

    /// Spawn @p binary with @p args.  Throws nothing — errors are captured
    /// in `startup_error()` so callers can inspect without exceptions.
    ProcessGuard(const std::string& binary, const std::vector<std::string>& args);

    ~ProcessGuard() noexcept;

    ProcessGuard(const ProcessGuard&) = delete;
    ProcessGuard& operator=(const ProcessGuard&) = delete;
    ProcessGuard(ProcessGuard&& other) noexcept;
    ProcessGuard& operator=(ProcessGuard&& other) noexcept;

    /// Graceful-then-forced termination.  Idempotent.
    void terminate();

    [[nodiscard]] bool is_running() const;
    [[nodiscard]] std::optional<int> exit_code() const;

    [[nodiscard]] bool started() const { return started_; }
    [[nodiscard]] const std::string& startup_error() const { return startup_error_; }

private:
    // Forward-declared; the full type (bp::child) is only needed in the .cpp.
    struct ChildHandle;
    std::unique_ptr<ChildHandle> child_;
    bool started_ = false;
    std::string startup_error_;
};

// ─── ServiceProcess ────────────────────────────────────────────────
struct ServiceProcess {
    std::string service_id;
    std::uint16_t port = 0;
    ProcessGuard process;
};

// ─── TestClient ────────────────────────────────────────────────────
// Simple TCP client that speaks the project's length-prefixed packet
// protocol.  Mirrors the pattern from backend_routing_test.cpp /
// demo_server_smoke_test.cpp.
class TestClient {
public:
    TestClient();
    ~TestClient();

    TestClient(const TestClient&) = delete;
    TestClient& operator=(const TestClient&) = delete;
    TestClient(TestClient&&) = delete;
    TestClient& operator=(TestClient&&) = delete;

    void connect(std::uint16_t port);
    void connect(const std::string& host, std::uint16_t port);
    void close();

    void send(std::uint16_t message_id, std::uint32_t request_id,
              const std::string& body);

    /// Blocking read (no timeout).
    [[nodiscard]] net::packet::DecodedPacket read();

    /// Blocking read with millisecond timeout.
    /// Throws std::runtime_error on timeout.
    [[nodiscard]] net::packet::DecodedPacket read(std::chrono::milliseconds timeout);

    /// Send + blocking read.
    [[nodiscard]] net::packet::DecodedPacket
    exchange(std::uint16_t message_id, std::uint32_t request_id,
             const std::string& body);

    /// Send + blocking read with timeout.
    [[nodiscard]] net::packet::DecodedPacket
    exchange(std::uint16_t message_id, std::uint32_t request_id,
             const std::string& body, std::chrono::milliseconds timeout);

    /// Loop reading until a packet with @p message_id arrives (no timeout).
    [[nodiscard]] net::packet::DecodedPacket
    expect_message(std::uint16_t message_id);

    /// Loop reading until a packet with @p message_id arrives,
    /// with an overall timeout.
    [[nodiscard]] net::packet::DecodedPacket
    expect_message(std::uint16_t message_id, std::chrono::milliseconds timeout);

private:
    boost::asio::io_context io_context_;
    boost::asio::ip::tcp::socket socket_;
};

// ─── MultiProcessFixture ───────────────────────────────────────────
// Google Test fixture that manages all four v2 services as real OS
// processes.
//
// Usage (in TEST_F):
//   TEST_F(MultiProcessFixture, MyTest) {
//       SKIP_IF_V2_RUNTIME_UNAVAILABLE(*this);
//       auto client = make_client();
//       // ... test logic ...
//   }
class MultiProcessFixture : public ::testing::Test {
public:
    static constexpr std::chrono::milliseconds kServiceStartTimeout{15'000};
    static constexpr std::chrono::milliseconds kServicePollInterval{100};

protected:
    void SetUp() override;
    void TearDown() override;

    /// Launch all four services (login → room → battle → gateway).
    /// Returns false on any failure; stores a human-readable error in
    /// `startup_error()`.
    [[nodiscard]] bool start_all();

    /// Stop all services (reverse order).
    void stop_all();

    /// Stop a single service by name ("gateway", "login", "room",
    /// "battle").  Idempotent — no-op if not running.
    void stop_service(const std::string& service_id);

    /// (Re)start a single service by name.
    [[nodiscard]] bool start_service(const std::string& service_id);

    /// Create a TestClient already connected to the gateway port.
    std::unique_ptr<TestClient> make_client();

    /// Human-readable startup error, set by start_all / start_service.
    [[nodiscard]] const std::string& startup_error() const { return startup_error_; }

    /// Wait until TCP connect succeeds on @p port, or @p timeout elapses.
    static bool wait_for_port(std::uint16_t port,
                              std::chrono::milliseconds timeout = kServiceStartTimeout);

    // Binary path helpers — resolved from CMake compile definitions.
    static std::string gateway_binary();
    static std::string login_binary();
    static std::string room_binary();
    static std::string battle_binary();
    static std::string leaderboard_binary();
    static std::uint16_t reserve_free_port();

    ServiceProcess* find_service(const std::string& service_id);
    const ServiceProcess* find_service(const std::string& service_id) const;

    std::vector<ServiceProcess> services_;
    std::string startup_error_;
    bool all_started_ = false;
    std::uint16_t gateway_port_ = 0;
    std::uint16_t login_port_ = 0;
    std::uint16_t room_port_ = 0;
    std::uint16_t battle_port_ = 0;
    std::uint16_t leaderboard_port_ = 0;
};

}  // namespace v2_test

// ─── SKIP macro ────────────────────────────────────────────────────
// Drop this at the top of every TEST_F(MultiProcessFixture, ...) that
// requires real OS processes.
#define SKIP_IF_V2_RUNTIME_UNAVAILABLE(fixture)                               \
    do {                                                                       \
        if (!(fixture).start_all()) {                                          \
            GTEST_SKIP() << "v2 runtime unavailable: " << (fixture).startup_error(); \
        }                                                                      \
    } while (false)
