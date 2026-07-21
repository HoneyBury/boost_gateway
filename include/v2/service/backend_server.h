#pragma once

#include "v2/service/backend_envelope.h"
#include "v3/cluster/tls_config.h"

#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace v2::service {

using BackendHandler = std::function<BackendEnvelope(const BackendEnvelope& request)>;

struct BackendServerOptions {
    std::uint16_t port = 0;
    bool tls_enabled = true;   // Batch B: TLS default on, set BOOST_DISABLE_TLS=1 to disable
    std::optional<v3::cluster::TlsSessionConfig> tls_config;
    // BackendConnection instances are intentionally long lived. Keep their
    // server-side idle window longer than a normal capacity measurement.
    std::chrono::milliseconds session_idle_timeout = std::chrono::minutes(2);
};

class BackendServer {
public:
    using HandlerMap = std::unordered_map<std::string, BackendHandler>;

    BackendServer(std::uint16_t port, HandlerMap handlers);
    BackendServer(BackendServerOptions options, HandlerMap handlers);
    ~BackendServer();

    BackendServer(const BackendServer&) = delete;
    BackendServer& operator=(const BackendServer&) = delete;
    BackendServer(BackendServer&&) = delete;
    BackendServer& operator=(BackendServer&&) = delete;

    void start();
    void stop();

    [[nodiscard]] std::uint16_t local_port() const;

private:
    void do_accept();
    void handle_session(std::shared_ptr<boost::asio::ip::tcp::socket> socket);
    bool setup_tls_context();
    void handle_plain_session(std::shared_ptr<boost::asio::ip::tcp::socket> socket);
    void handle_tls_session(std::shared_ptr<boost::asio::ip::tcp::socket> socket);
    BackendEnvelope handle_request(const BackendEnvelope& request);

    BackendServerOptions options_;
    HandlerMap handlers_;
    boost::asio::io_context io_context_;
    std::unique_ptr<boost::asio::ip::tcp::acceptor> acceptor_;
    std::unique_ptr<boost::asio::ssl::context> ssl_context_;
    std::mutex session_mutex_;
    std::mutex stop_mutex_;
    std::vector<std::shared_ptr<boost::asio::ip::tcp::socket>> session_sockets_;
    std::thread thread_;
    std::vector<std::thread> session_threads_;
    std::atomic<bool> running_{false};
};

}  // namespace v2::service
