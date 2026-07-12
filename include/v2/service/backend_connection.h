#pragma once

#include "v2/service/backend_envelope.h"
#include "v3/cluster/tls_config.h"

#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>

#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <optional>
#include <string>

namespace v2::service {

struct BackendConnectionRetryOptions {
    std::uint32_t max_retries = 3;
    std::chrono::milliseconds initial_backoff{100};
    std::chrono::milliseconds max_backoff{2000};
    double jitter_factor = 0.1;
};

struct BackendConnectionOptions {
    std::string host = "127.0.0.1";
    std::uint16_t port = 0;
    std::chrono::milliseconds timeout = std::chrono::milliseconds(5000);
    std::chrono::milliseconds connect_timeout = std::chrono::milliseconds(1000);

    // v3.3.2: Exponential backoff retry for transient failures.
    // When max_retries > 0, send_request will retry with exponential backoff
    // on write/read failures before giving up.
    BackendConnectionRetryOptions retry_options;

    // v3.3.2: Max concurrent in-flight requests (bulkhead isolation).
    // 0 = unlimited (default). When set, send_request waits on a semaphore.
    std::uint32_t max_concurrent_requests = 0;

    // Batch B: TLS default on. Set BOOST_DISABLE_TLS=1 to disable.
    bool tls_enabled = true;

    // Optional TLS config for encrypted inter-service communication.
    // When set, BackendConnection performs a TLS handshake after TCP connect.
    std::optional<v3::cluster::TlsSessionConfig> tls_config;
};

class BackendConnection {
public:
    enum class Availability {
        kConnected,
        kBusy,
        kDisconnected,
    };

    enum class FailureStage {
        kNone,
        kNotConnected,
        kWrite,
        kRead,
    };

    explicit BackendConnection(BackendConnectionOptions options);
    ~BackendConnection();

    BackendConnection(const BackendConnection&) = delete;
    BackendConnection& operator=(const BackendConnection&) = delete;
    BackendConnection(BackendConnection&&) = delete;
    BackendConnection& operator=(BackendConnection&&) = delete;

    bool connect();

    [[nodiscard]] std::optional<BackendEnvelope> send_request(
        BackendEnvelope request);

    /// Send a request with automatic retry on transient failures.
    /// Retries use exponential backoff with jitter.
    [[nodiscard]] std::optional<BackendEnvelope> send_request_with_retry(
        BackendEnvelope request);

    /// Try to acquire a bulkhead permit. Returns false if max_concurrent_requests
    /// is exceeded (non-blocking).
    [[nodiscard]] bool try_acquire_permit();

    /// Release a previously acquired bulkhead permit.
    void release_permit();

    void close();

    [[nodiscard]] bool is_connected() const;
    // Pool selection must not block behind an in-flight synchronous RPC.
    [[nodiscard]] Availability availability() const;
    [[nodiscard]] FailureStage last_failure_stage() const noexcept { return last_failure_stage_; }
    [[nodiscard]] std::uint32_t active_requests() const noexcept { return active_requests_; }

    /// Returns true if the connection is secured with TLS.
    [[nodiscard]] bool is_tls_enabled() const { return options_.tls_config.has_value(); }

private:
    bool tls_handshake();

    std::optional<BackendEnvelope> do_send(BackendEnvelope request);

    std::optional<BackendEnvelope> attempt_send_with_retry(
        BackendEnvelope request,
        std::uint32_t remaining_retries,
        std::chrono::milliseconds backoff);

    BackendConnectionOptions options_;
    boost::asio::io_context io_context_;
    std::unique_ptr<boost::asio::ip::tcp::socket> socket_;
    std::unique_ptr<boost::asio::ssl::context> ssl_context_;
    std::unique_ptr<boost::asio::ssl::stream<boost::asio::ip::tcp::socket&>> ssl_stream_;
    FailureStage last_failure_stage_ = FailureStage::kNone;
    mutable std::recursive_mutex mutex_;
    std::atomic<std::uint32_t> active_requests_{0};
};

}  // namespace v2::service
