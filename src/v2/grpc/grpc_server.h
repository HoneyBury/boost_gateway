#pragma once

// grpc_server.h — Generic gRPC server base class with lifecycle management,
// health check endpoint, and error code translation.
//
// Conditionally compiled only when BOOST_BUILD_GRPC is defined.

#include <atomic>
#include <chrono>
#include <exception>
#include <memory>
#include <string>
#include <thread>

#include <grpcpp/grpcpp.h>
#include <grpcpp/health_check_service_interface.h>
#include <grpcpp/ext/proto_server_reflection_plugin.h>

#include <spdlog/spdlog.h>

#include "net/protocol.h"
#include "v2/service/error_codes.h"

namespace v2::grpc {

struct GrpcTlsServerOptions {
  std::string certificate_chain_pem;
  std::string private_key_pem;
  std::string root_ca_pem;
  bool require_client_certificate = false;

  [[nodiscard]] bool enabled() const noexcept {
    return !certificate_chain_pem.empty() || !private_key_pem.empty();
  }

  [[nodiscard]] bool valid() const noexcept {
    if (!enabled()) return true;
    return !certificate_chain_pem.empty() && !private_key_pem.empty() &&
           (!require_client_certificate || !root_ca_pem.empty());
  }
};

struct BackendFailure {
  v2::service::ServiceErrorCode code = v2::service::ServiceErrorCode::kRejected;
  std::string message;
};

inline std::string encode_backend_failure(v2::service::ServiceErrorCode code,
                                          std::string message) {
  return "v2-service-error:" + std::to_string(static_cast<std::int32_t>(code)) +
         ":" + std::move(message);
}

inline BackendFailure decode_backend_failure(
    const std::string& value,
    v2::service::ServiceErrorCode fallback = v2::service::ServiceErrorCode::kRejected) {
  constexpr const char* kPrefix = "v2-service-error:";
  if (!value.starts_with(kPrefix)) {
    return {.code = fallback, .message = value};
  }
  const auto code_start = std::char_traits<char>::length(kPrefix);
  const auto separator = value.find(':', code_start);
  if (separator == std::string::npos) {
    return {.code = fallback, .message = value};
  }
  try {
    const auto code = std::stoi(value.substr(code_start, separator - code_start));
    return {.code = static_cast<v2::service::ServiceErrorCode>(code),
            .message = value.substr(separator + 1)};
  } catch (const std::exception&) {
    return {.code = fallback, .message = value};
  }
}

// -------------------------------------------------------------------
// ErrorCodeMapper: maps net::protocol::ErrorCode to gRPC StatusCode.
// -------------------------------------------------------------------
struct ErrorCodeMapper {
  /// Convert a project-internal error code to a gRPC status code.
  /// kOk (0) -> OK, positive values -> specific codes, negative -> Unknown.
  static ::grpc::StatusCode to_grpc_status(std::int32_t error_code) noexcept {
    using net::protocol::ErrorCode;

    switch (static_cast<ErrorCode>(error_code)) {
      case ErrorCode::kOk:
        return ::grpc::StatusCode::OK;
      case ErrorCode::kAuthRequired:
      case ErrorCode::kInvalidToken:
      case ErrorCode::kTokenExpired:
        return ::grpc::StatusCode::UNAUTHENTICATED;
      case ErrorCode::kInvalidUserId:
      case ErrorCode::kInvalidRoomId:
      case ErrorCode::kNotInRoom:
      case ErrorCode::kNotRoomOwner:
        return ::grpc::StatusCode::INVALID_ARGUMENT;
      case ErrorCode::kDuplicateLogin:
        return ::grpc::StatusCode::ALREADY_EXISTS;
      case ErrorCode::kRoomNotFound:
        return ::grpc::StatusCode::NOT_FOUND;
      case ErrorCode::kRoomAlreadyExists:
        return ::grpc::StatusCode::ALREADY_EXISTS;
      case ErrorCode::kRateLimited:
        return ::grpc::StatusCode::RESOURCE_EXHAUSTED;
      case ErrorCode::kSessionNotFound:
        return ::grpc::StatusCode::NOT_FOUND;
      default:
        return ::grpc::StatusCode::UNKNOWN;
    }
  }

  static ::grpc::StatusCode to_grpc_status(
      v2::service::ServiceErrorCode error_code) noexcept {
    using v2::service::ServiceErrorCode;
    switch (error_code) {
      case ServiceErrorCode::kOk:
        return ::grpc::StatusCode::OK;
      case ServiceErrorCode::kTimeout:
        return ::grpc::StatusCode::DEADLINE_EXCEEDED;
      case ServiceErrorCode::kUnavailable:
      case ServiceErrorCode::kStorageUnavailable:
        return ::grpc::StatusCode::UNAVAILABLE;
      case ServiceErrorCode::kInvalidRequest:
      case ServiceErrorCode::kIllegalUsername:
      case ServiceErrorCode::kWeakCredential:
        return ::grpc::StatusCode::INVALID_ARGUMENT;
      case ServiceErrorCode::kUserAlreadyExists:
        return ::grpc::StatusCode::ALREADY_EXISTS;
      case ServiceErrorCode::kRoomNotFound:
        return ::grpc::StatusCode::NOT_FOUND;
      case ServiceErrorCode::kAccountDisabled:
      case ServiceErrorCode::kRejected:
        return ::grpc::StatusCode::PERMISSION_DENIED;
      case ServiceErrorCode::kCircuitOpen:
        return ::grpc::StatusCode::UNAVAILABLE;
      default:
        return ::grpc::StatusCode::UNKNOWN;
    }
  }

  /// Build a ::grpc::Status from a project error code and optional message.
  static ::grpc::Status from_error_code(std::int32_t error_code,
                                      std::string message = {}) {
    if (error_code == 0) {
      return ::grpc::Status::OK;
    }
    if (message.empty()) {
      message = net::protocol::to_string(static_cast<net::protocol::ErrorCode>(error_code));
    }
    return ::grpc::Status(to_grpc_status(error_code), std::move(message));
  }

  static ::grpc::Status from_service_error(v2::service::ServiceErrorCode error_code,
                                         std::string message = {}) {
    if (error_code == v2::service::ServiceErrorCode::kOk) {
      return ::grpc::Status::OK;
    }
    if (message.empty()) {
      message = v2::service::to_string(error_code);
    }
    return ::grpc::Status(to_grpc_status(error_code), std::move(message));
  }
};

// -------------------------------------------------------------------
// GrpcServer base class
// -------------------------------------------------------------------
class GrpcServer {
 public:
  explicit GrpcServer(std::string server_name,
                      std::uint16_t port,
                      GrpcTlsServerOptions tls_options = {})
      : server_name_(std::move(server_name)),
        port_(port),
        tls_options_(std::move(tls_options)) {}

  virtual ~GrpcServer() = default;

  // Non-copyable, non-movable.
  GrpcServer(const GrpcServer&) = delete;
  GrpcServer& operator=(const GrpcServer&) = delete;

  /// Start the gRPC server on the configured port.
  /// Returns true if the server started successfully.
  bool start() {
    if (server_) {
      SPDLOG_WARN("{}: gRPC server already running", server_name_);
      return false;
    }

    start_time_ = std::chrono::steady_clock::now();

    if (!tls_options_.valid()) {
      SPDLOG_ERROR("{}: invalid TLS configuration", server_name_);
      return false;
    }

    // Build server address string
    const std::string address = fmt::format("0.0.0.0:{}", port_);
    SPDLOG_INFO("{}: starting gRPC server on {}", server_name_, address);

    ::grpc::EnableDefaultHealthCheckService(true);
    ::grpc::reflection::InitProtoReflectionServerBuilderPlugin();

    // Let subclasses register services before building
    register_services(builder_);

    std::shared_ptr<::grpc::ServerCredentials> credentials;
    if (tls_options_.enabled()) {
      ::grpc::SslServerCredentialsOptions ssl_options;
      ssl_options.pem_root_certs = tls_options_.root_ca_pem;
      ssl_options.pem_key_cert_pairs.push_back({
          tls_options_.private_key_pem, tls_options_.certificate_chain_pem});
      if (tls_options_.require_client_certificate) {
        ssl_options.client_certificate_request =
            GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY;
      }
      credentials = ::grpc::SslServerCredentials(ssl_options);
    } else {
      credentials = ::grpc::InsecureServerCredentials();
    }

    int selected_port = 0;
    builder_.AddListeningPort(address, credentials, &selected_port);

    // Optional: set server-level resource limits
    builder_.SetMaxReceiveMessageSize(4 * 1024 * 1024);   // 4 MB
    builder_.SetMaxSendMessageSize(4 * 1024 * 1024);      // 4 MB

    server_ = builder_.BuildAndStart();
    if (!server_ || selected_port <= 0 || selected_port > 65535) {
      SPDLOG_ERROR("{}: failed to start gRPC server on {}", server_name_, address);
      server_.reset();
      return false;
    }

    port_ = static_cast<std::uint16_t>(selected_port);

    SPDLOG_INFO("{}: gRPC server listening on {}", server_name_, address);
    return true;
  }

  /// Gracefully shut down the server.
  void shutdown() {
    if (server_) {
      SPDLOG_INFO("{}: shutting down gRPC server", server_name_);
      server_->Shutdown(std::chrono::system_clock::now() + std::chrono::seconds(1));
      server_.reset();
    }
  }

  /// Block until the server is shut down (for main-loop integration).
  void wait() {
    if (server_) {
      server_->Wait();
    }
  }

  /// Get local port.
  std::uint16_t port() const noexcept { return port_; }

  /// Get server name.
  const std::string& server_name() const noexcept { return server_name_; }

  /// Whether the underlying gRPC listener is still available.
  bool running() const noexcept { return static_cast<bool>(server_); }

  /// Uptime in seconds since start() was called.
  std::uint64_t uptime_seconds() const {
    const auto now = std::chrono::steady_clock::now();
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::seconds>(now - start_time_).count());
  }

 protected:
  /// Subclasses override this to register gRPC services on the builder.
  virtual void register_services(::grpc::ServerBuilder& builder) = 0;

  ::grpc::ServerBuilder builder_;
  std::unique_ptr<::grpc::Server> server_;
  std::chrono::steady_clock::time_point start_time_;

 private:
  std::string server_name_;
  std::uint16_t port_;
  GrpcTlsServerOptions tls_options_;
};

}  // namespace v2::grpc
