#include "v2/service/backend_connection.h"

#include "v2/service/backend_frame_codec.h"

namespace v2::service {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

BackendConnection::BackendConnection(BackendConnectionOptions options)
    : options_(std::move(options)) {}

BackendConnection::~BackendConnection() { close(); }

bool BackendConnection::connect() {
    socket_ = std::make_unique<tcp::socket>(io_context_);

    tcp::resolver resolver(io_context_);
    boost::system::error_code ec;
    auto endpoints = resolver.resolve(options_.host,
                                      std::to_string(options_.port), ec);
    if (ec) return false;

    asio::connect(*socket_, endpoints, ec);
    if (ec) return false;

    // v3.0.0: Perform TLS handshake if TLS config is set.
    if (options_.tls_config.has_value()) {
        if (!tls_handshake()) {
            close();
            return false;
        }
    }

    return true;
}

bool BackendConnection::tls_handshake() {
    if (!options_.tls_config) return true;

    const auto& cfg = *options_.tls_config;
    try {
        auto method = boost::asio::ssl::context::tlsv12_client;
        if (cfg.min_version == v3::cluster::TlsSessionConfig::TlsVersion::k13) {
            method = boost::asio::ssl::context::tlsv13_client;
        }
        ssl_context_ = std::make_unique<boost::asio::ssl::context>(method);

        ssl_context_->set_options(
            boost::asio::ssl::context::default_workarounds |
            boost::asio::ssl::context::no_sslv2 |
            boost::asio::ssl::context::no_sslv3 |
            boost::asio::ssl::context::no_tlsv1 |
            boost::asio::ssl::context::no_tlsv1_1);

        if (!cfg.cipher_list.empty()) {
            SSL_CTX_set_cipher_list(ssl_context_->native_handle(),
                                    cfg.cipher_list.c_str());
        }

        // Load certificates
        if (!cfg.cert.cert_chain_path.empty()) {
            ssl_context_->use_certificate_chain_file(cfg.cert.cert_chain_path);
        }
        if (!cfg.cert.private_key_path.empty()) {
            ssl_context_->use_private_key_file(
                cfg.cert.private_key_path,
                boost::asio::ssl::context::pem);
        }

        // Set verification mode
        if (cfg.verify_mode == v3::cluster::TlsVerifyMode::kNone) {
            ssl_context_->set_verify_mode(boost::asio::ssl::verify_none);
        } else {
            ssl_context_->set_verify_mode(
                boost::asio::ssl::verify_peer |
                boost::asio::ssl::verify_fail_if_no_peer_cert);
            if (!cfg.cert.ca_cert_path.empty()) {
                ssl_context_->load_verify_file(cfg.cert.ca_cert_path);
            }
        }

        ssl_stream_ = std::make_unique<
            boost::asio::ssl::stream<tcp::socket&>>(*socket_, *ssl_context_);

        ssl_stream_->handshake(boost::asio::ssl::stream_base::client);
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

std::optional<BackendEnvelope> BackendConnection::send_request(
    BackendEnvelope request) {
    if (!socket_ || !socket_->is_open()) return std::nullopt;

    if (request.correlation_id == 0) {
        request.correlation_id = generate_correlation_id();
    }
    request.source_service = ServiceId::kGateway;

    if (ssl_stream_) {
        if (!write_frame(*ssl_stream_, request)) return std::nullopt;
        auto response = read_frame(*ssl_stream_, options_.timeout);
        if (!response) return std::nullopt;
        return response;
    }

    if (!write_frame(*socket_, request)) return std::nullopt;

    auto response = read_frame(*socket_, options_.timeout);
    if (!response) return std::nullopt;

    return response;
}

void BackendConnection::close() {
    if (ssl_stream_) {
        boost::system::error_code ec;
        ssl_stream_->shutdown(ec);
        ssl_stream_.reset();
    }
    if (socket_) {
        boost::system::error_code ec;
        socket_->close(ec);
    }
    ssl_context_.reset();
    io_context_.stop();
}

bool BackendConnection::is_connected() const {
    if (ssl_stream_) {
        // Check the underlying TCP socket since SSL stream doesn't
        // expose is_open() directly.
        return ssl_stream_->next_layer().is_open();
    }
    return socket_ && socket_->is_open();
}

}  // namespace v2::service
