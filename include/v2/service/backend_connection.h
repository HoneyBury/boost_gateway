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

struct BackendConnectionOptions {
    std::string host = "127.0.0.1";
    std::uint16_t port = 0;
    std::chrono::milliseconds timeout = std::chrono::milliseconds(5000);
    std::chrono::milliseconds connect_timeout = std::chrono::milliseconds(1000);

    // v3.0.0: Optional TLS config for encrypted inter-service communication.
    // When set, BackendConnection performs a TLS handshake after TCP connect.
    std::optional<v3::cluster::TlsSessionConfig> tls_config;
};

class BackendConnection {
public:
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

    void close();

    [[nodiscard]] bool is_connected() const;
    [[nodiscard]] FailureStage last_failure_stage() const noexcept { return last_failure_stage_; }

    /// Returns true if the connection is secured with TLS.
    [[nodiscard]] bool is_tls_enabled() const { return options_.tls_config.has_value(); }

private:
    bool tls_handshake();

    BackendConnectionOptions options_;
    boost::asio::io_context io_context_;
    std::unique_ptr<boost::asio::ip::tcp::socket> socket_;
    std::unique_ptr<boost::asio::ssl::context> ssl_context_;
    std::unique_ptr<boost::asio::ssl::stream<boost::asio::ip::tcp::socket&>> ssl_stream_;
    FailureStage last_failure_stage_ = FailureStage::kNone;
    mutable std::recursive_mutex mutex_;
};

}  // namespace v2::service
