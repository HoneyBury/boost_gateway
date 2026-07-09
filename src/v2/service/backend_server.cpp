#include "v2/service/backend_server.h"

#include "v2/service/backend_frame_codec.h"

#include <boost/asio/bind_executor.hpp>
#include <boost/asio/strand.hpp>

#include <algorithm>
#include <stdexcept>

namespace v2::service {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

BackendServer::BackendServer(std::uint16_t port, HandlerMap handlers)
    : BackendServer([port] {
          BackendServerOptions options;
          options.port = port;
          return options;
      }(), std::move(handlers)) {}

BackendServer::BackendServer(BackendServerOptions options, HandlerMap handlers)
    : options_(std::move(options)), handlers_(std::move(handlers)) {}

BackendServer::~BackendServer() { stop(); }

void BackendServer::start() {
    running_ = true;

    // Batch B: allow disabling TLS via env var for backward compatibility
    if (options_.tls_enabled) {
        const char* disable_tls = std::getenv("BOOST_DISABLE_TLS");
        if (disable_tls != nullptr && (std::string(disable_tls) == "1" ||
                                        std::string(disable_tls) == "true")) {
            options_.tls_enabled = false;
        }
    }

    if (!setup_tls_context()) {
        running_ = false;
        throw std::runtime_error("failed to initialize backend TLS context");
    }

    acceptor_ = std::make_unique<tcp::acceptor>(
        io_context_, tcp::endpoint(tcp::v4(), options_.port));

    thread_ = std::thread([this] {
        do_accept();
        io_context_.run();
    });
}

void BackendServer::stop() {
    running_ = false;
    if (acceptor_) {
        boost::system::error_code ec;
        acceptor_->close(ec);
    }
    std::vector<std::shared_ptr<tcp::socket>> sessions;
    {
        std::scoped_lock lock(session_mutex_);
        sessions = session_sockets_;
    }
    for (auto& session : sessions) {
        if (!session) {
            continue;
        }
        boost::system::error_code ec;
        session->close(ec);
    }
    io_context_.stop();
    ssl_context_.reset();
    if (thread_.joinable()) {
        thread_.join();
    }

    std::vector<std::thread> session_threads;
    {
        std::scoped_lock lock(session_mutex_);
        session_threads.swap(session_threads_);
    }
    for (auto& session_thread : session_threads) {
        if (session_thread.joinable()) {
            session_thread.join();
        }
    }
}

std::uint16_t BackendServer::local_port() const {
    if (!acceptor_ || !acceptor_->is_open()) return 0;
    return acceptor_->local_endpoint().port();
}

bool BackendServer::setup_tls_context() {
    // Batch B: when TLS is disabled via env var, skip setup
    if (!options_.tls_enabled) {
        ssl_context_.reset();
        return true;
    }

    if (!options_.tls_config) {
        // Dev mode: TLS enabled but no certs configured → warn and continue plain
        ssl_context_.reset();
        return true;
    }

    const auto& cfg = *options_.tls_config;
    try {
        auto method = asio::ssl::context::tlsv12_server;
        if (cfg.min_version == v3::cluster::TlsSessionConfig::TlsVersion::k13) {
            method = asio::ssl::context::tlsv13_server;
        }
        ssl_context_ = std::make_unique<asio::ssl::context>(method);
        ssl_context_->set_options(
            asio::ssl::context::default_workarounds |
            asio::ssl::context::no_sslv2 |
            asio::ssl::context::no_sslv3 |
            asio::ssl::context::no_tlsv1 |
            asio::ssl::context::no_tlsv1_1 |
            asio::ssl::context::single_dh_use);

        if (!cfg.cipher_list.empty()) {
            SSL_CTX_set_cipher_list(ssl_context_->native_handle(),
                                    cfg.cipher_list.c_str());
        }
        if (!cfg.cert.cert_chain_path.empty()) {
            ssl_context_->use_certificate_chain_file(cfg.cert.cert_chain_path);
        }
        if (!cfg.cert.private_key_path.empty()) {
            ssl_context_->use_private_key_file(
                cfg.cert.private_key_path,
                asio::ssl::context::pem);
        }

        if (cfg.verify_mode == v3::cluster::TlsVerifyMode::kMutual) {
            ssl_context_->set_verify_mode(asio::ssl::verify_peer |
                                          asio::ssl::verify_fail_if_no_peer_cert);
            if (!cfg.cert.ca_cert_path.empty()) {
                ssl_context_->load_verify_file(cfg.cert.ca_cert_path);
            }
        } else {
            ssl_context_->set_verify_mode(asio::ssl::verify_none);
        }
        return true;
    } catch (const std::exception&) {
        ssl_context_.reset();
        return false;
    }
}

void BackendServer::do_accept() {
    if (!running_) return;

    auto socket = std::make_shared<tcp::socket>(io_context_);
    acceptor_->async_accept(*socket, [this, socket](boost::system::error_code ec) {
        if (!ec && running_) {
            std::scoped_lock lock(session_mutex_);
            if (!running_) {
                return;
            }
            session_threads_.emplace_back([this, socket] {
                handle_session(socket);
            });
        }
        do_accept();
    });
}

void BackendServer::handle_session(std::shared_ptr<tcp::socket> socket) {
    if (ssl_context_) {
        handle_tls_session(std::move(socket));
        return;
    }
    handle_plain_session(std::move(socket));
}

BackendEnvelope BackendServer::handle_request(const BackendEnvelope& request) {
    BackendEnvelope response;
    response.correlation_id = request.correlation_id;
    response.source_service = request.target_service;
    response.target_service = request.source_service;
    response.kind = MessageKind::kResponse;

    auto it = handlers_.find(request.message_type);
    if (it != handlers_.end()) {
        try {
            response = it->second(request);
            response.correlation_id = request.correlation_id;
            if (response.source_service == ServiceId::kGateway) {
                response.source_service = request.target_service;
            }
            if (response.target_service == ServiceId::kGateway) {
                response.target_service = request.source_service;
            }
        } catch (...) {
            response.kind = MessageKind::kError;
            response.error_code = -1005;
            response.payload.clear();
        }
    } else {
        response.kind = MessageKind::kError;
        response.error_code = -1006;
        response.payload.clear();
    }
    return response;
}

void BackendServer::handle_plain_session(std::shared_ptr<tcp::socket> socket) {
    {
        std::scoped_lock lock(session_mutex_);
        session_sockets_.push_back(socket);
    }
    while (running_ && socket->is_open()) {
        auto request = read_frame(*socket, std::chrono::milliseconds(7000));
        if (!request) break;

        write_frame(*socket, handle_request(*request));
    }
    {
        std::scoped_lock lock(session_mutex_);
        session_sockets_.erase(
            std::remove(session_sockets_.begin(), session_sockets_.end(), socket),
            session_sockets_.end());
    }
}

void BackendServer::handle_tls_session(std::shared_ptr<tcp::socket> socket) {
    {
        std::scoped_lock lock(session_mutex_);
        session_sockets_.push_back(socket);
    }

    try {
        asio::ssl::stream<tcp::socket&> stream(*socket, *ssl_context_);
        stream.handshake(asio::ssl::stream_base::server);
        while (running_ && socket->is_open()) {
            auto request = read_frame(stream, std::chrono::milliseconds(7000));
            if (!request) break;
            write_frame(stream, handle_request(*request));
        }
        boost::system::error_code ec;
        stream.shutdown(ec);
    } catch (const std::exception&) {
        boost::system::error_code ec;
        socket->close(ec);
    }
    {
        std::scoped_lock lock(session_mutex_);
        session_sockets_.erase(
            std::remove(session_sockets_.begin(), session_sockets_.end(), socket),
            session_sockets_.end());
    }
}

}  // namespace v2::service
