#pragma once
// SDK v4.2.0: Transport abstraction layer.
// Allows pluggable transports (TCP, WebSocket, mock for testing).

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace boost_gateway {
namespace sdk {
namespace transport {

using ReceiveCallback = std::function<void(const std::vector<char>& data)>;

class ITransport {
public:
    virtual ~ITransport() = default;

    virtual bool connect(const std::string& host, std::uint16_t port,
                         std::chrono::milliseconds timeout) = 0;
    virtual void disconnect() = 0;
    virtual bool is_connected() const = 0;
    virtual bool send(const std::vector<char>& data) = 0;

    /// Blocking receive with timeout. Returns empty vector on timeout.
    virtual std::vector<char> receive(std::chrono::milliseconds timeout) = 0;

    /// Async receive callback (called from transport thread).
    virtual void set_receive_callback(ReceiveCallback cb) = 0;

    // ── Async operations ──────────────────────────────────────────────────

    /// Async connect with callback
    virtual void async_connect(const std::string& host, std::uint16_t port,
                               std::function<void(bool)> callback) = 0;

    /// Async send with callback (bytes_sent or error)
    virtual void async_send(const std::string& data,
                            std::function<void(bool)> callback) = 0;

    /// Set async receive callback (called when data arrives)
    virtual void set_async_receive_callback(
        std::function<void(const std::string&)> callback) = 0;
};

// ── Connection pool ──────────────────────────────────────────────────

struct PoolConfig {
    std::size_t max_connections = 4;
    std::chrono::milliseconds connect_timeout{5000};
    std::chrono::milliseconds health_check_interval{10000};
    std::uint32_t max_retries = 3;
};

class ConnectionPool {
public:
    using TransportFactory = std::function<std::unique_ptr<ITransport>()>;

    explicit ConnectionPool(PoolConfig config, TransportFactory factory);
    ~ConnectionPool();

    ConnectionPool(const ConnectionPool&) = delete;
    ConnectionPool& operator=(const ConnectionPool&) = delete;

    /// Get a connected transport (may block until one is available).
    ITransport* acquire();

    /// Release a transport back to the pool.
    void release(ITransport* transport);

    /// Connect all pooled transports to host:port.
    bool connect_all(const std::string& host, std::uint16_t port);

    /// Disconnect all.
    void disconnect_all();

    [[nodiscard]] std::size_t available() const;
    [[nodiscard]] std::size_t total() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

// ── Factory functions ─────────────────────────────────────────────────

/// Create a TCP transport backed by Boost.Asio.
std::unique_ptr<ITransport> make_tcp_transport();

}  // namespace transport
}  // namespace sdk
}  // namespace boost_gateway
