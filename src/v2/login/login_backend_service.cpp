#include "v2/login/login_backend_service.h"
#include "v2/auth/jwt_validator.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"

#include <nlohmann/json.hpp>
#include "app/audit_log.h"

#include <stdexcept>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

namespace {

// Per-backend login state (would be a database in production)
struct BackendPlayerState {
    std::unordered_map<std::string, std::string> active_sessions_;  // user_id -> session_id
    std::unordered_map<std::string, std::string> user_tokens_;      // user_id -> token

    std::mutex mutex_;
};

v2::service::BackendEnvelope make_error_response(int error_code,
                                                  const std::string& reason) {
    v2::service::BackendEnvelope response;
    response.kind = v2::service::MessageKind::kError;
    response.error_code = error_code;
    nlohmann::json body{{"status", "error"}, {"reason", reason}};
    response.payload = body.dump();
    return response;
}

v2::service::BackendEnvelope make_ok_response(const std::string& user_id,
                                               const std::string& display_name,
                                               bool is_duplicate = false,
                                               const std::string& role = "player") {
    v2::service::BackendEnvelope response;
    response.kind = v2::service::MessageKind::kResponse;
    nlohmann::json body{
        {"status", "ok"},
        {"user_id", user_id},
        {"display_name", display_name},
        {"is_duplicate", is_duplicate},
        {"role", role},
    };
    response.payload = body.dump();
    return response;
}

}  // namespace

namespace v2::login {

class LoginBackendService::Impl {
public:
    explicit Impl(std::uint16_t port) : port_(port) {}
    explicit Impl(LoginBackendOptions options)
        : port_(options.port),
          production_auth_required_(options.production_auth_required) {
        if (!options.jwt_secret.empty() || !options.jwt_public_key_pem.empty()) {
            jwt_validator_.emplace(v2::auth::JwtValidator::Config{
                .secret = options.jwt_secret,
                .public_key_pem = options.jwt_public_key_pem,
                .private_key_pem = options.jwt_private_key_pem,
                .issuer = options.jwt_issuer,
                .audience = options.jwt_audience,
            });
        }
        if (production_auth_required_ && !jwt_validator_.has_value()) {
            throw std::invalid_argument(
                "production auth requires V2_LOGIN_JWT_SECRET or V2_LOGIN_JWT_PUBLIC_KEY");
        }
    }

    void start() {
        v2::service::BackendServer::HandlerMap handlers;
        handlers["login_request"] = [this](const auto& req) { return handle_login_request(req); };
        handlers["token_validate"] = [this](const auto& req) { return handle_token_validate(req); };
        handlers["session_bind"] = [this](const auto& req) { return handle_session_bind(req); };
        handlers["session_close"] = [this](const auto& req) { return handle_session_close(req); };

        server_ = std::make_unique<v2::service::BackendServer>(port_, std::move(handlers));
        server_->start();
    }

    void stop() {
        if (server_) {
            server_->stop();
        }
    }

    std::uint16_t local_port() const {
        return server_ ? server_->local_port() : port_;
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    BackendPlayerState state_;
    std::optional<v2::auth::JwtValidator> jwt_validator_;
    bool production_auth_required_ = false;

    v2::service::BackendEnvelope handle_login_request(
        const v2::service::BackendEnvelope& request) {
        auto decoded = v2::service::decode_handler_payload(request);
        if (!decoded.has_value()) {
            return make_error_response(-1004, "empty_payload");
        }

        const auto& doc = decoded->payload;
        if (!doc.is_object() || !doc.contains("user_id") || !doc.contains("token")) {
            return make_error_response(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();
        std::string token = doc["token"].get<std::string>();
        std::string display_name = doc.value("display_name", user_id);

        if (user_id.empty()) {
            return make_error_response(-1004, "empty_user_id");
        }

        // Token validation: JWT if configured, else dev-mode
        if (token.empty()) {
            AUDIT_LOG("login_failure", "user_id=" + user_id + " reason=empty_token");
            return make_error_response(-1004, "empty_token");
        }

        std::string token_role = "player";

        if (jwt_validator_.has_value()) {
            auto result = jwt_validator_->validate(token);
            if (result.valid) {
                if (result.payload.sub != user_id) {
                    AUDIT_LOG("login_failure", "user_id=" + user_id + " reason=jwt_subject_mismatch");
                    return make_error_response(-1003, "token_subject_mismatch");
                }
                token_role = result.payload.role;
            } else {
                AUDIT_LOG("login_failure", "user_id=" + user_id + " reason=" + result.error);
                return make_error_response(-1003, result.error);
            }
        } else if (!production_auth_required_) {
            // Dev mode: "token:user_id" format — any non-empty token is accepted
            auto colon_pos = token.find(':');
            std::string token_user_id = (colon_pos != std::string::npos)
                ? token.substr(colon_pos + 1) : token;
            if (token_user_id.empty()) {
                AUDIT_LOG("login_failure", "user_id=" + user_id + " reason=invalid_token_format");
                return make_error_response(-1004, "invalid_token_format");
            }
        } else {
            AUDIT_LOG("login_failure", "user_id=" + user_id + " reason=jwt_required");
            return make_error_response(-1003, "jwt_required");
        }

        // Accept the token
        std::lock_guard<std::mutex> lock(state_.mutex_);

        bool is_duplicate = false;
        auto it = state_.active_sessions_.find(user_id);
        if (it != state_.active_sessions_.end()) {
            is_duplicate = true;
        }

        // Record the active session and token
        state_.active_sessions_[user_id] = user_id;
        state_.user_tokens_[user_id] = token;

        AUDIT_LOG("login_success", "user_id=" + user_id + " session_id=N/A");
        auto response = make_ok_response(user_id, display_name, is_duplicate, token_role);
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(response),
            v3::proto::EnvelopeMessageKind::kLoginResponse);
    }

    v2::service::BackendEnvelope handle_token_validate(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("token")) {
            return make_error_response(-1004, "invalid_json");
        }

        std::string token = doc["token"].get<std::string>();
        bool valid = !token.empty();
        if (jwt_validator_.has_value() && valid) {
            valid = jwt_validator_->validate(token).valid;
        } else if (production_auth_required_) {
            valid = false;
        }

        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kResponse;
        nlohmann::json body{{"valid", valid}};
        response.payload = body.dump();
        return response;
    }

    v2::service::BackendEnvelope handle_session_bind(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id")) {
            return make_error_response(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(state_.mutex_);
        state_.active_sessions_[user_id] = user_id;

        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kResponse;
        response.payload = R"({"status":"ok","action":"session_bound"})";
        return response;
    }

    v2::service::BackendEnvelope handle_session_close(
        const v2::service::BackendEnvelope& request) {
        auto doc = nlohmann::json::parse(request.payload, nullptr, false);
        if (doc.is_discarded() || !doc.contains("user_id")) {
            return make_error_response(-1004, "invalid_json");
        }

        std::string user_id = doc["user_id"].get<std::string>();

        std::lock_guard<std::mutex> lock(state_.mutex_);
        state_.active_sessions_.erase(user_id);

        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kResponse;
        response.payload = R"({"status":"ok","action":"session_closed"})";
        return response;
    }
};

LoginBackendService::LoginBackendService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}

LoginBackendService::LoginBackendService(LoginBackendOptions options)
    : impl_(std::make_unique<Impl>(std::move(options))) {}

LoginBackendService::~LoginBackendService() = default;

void LoginBackendService::start() { impl_->start(); }
void LoginBackendService::stop() { impl_->stop(); }
std::uint16_t LoginBackendService::local_port() const { return impl_->local_port(); }

}  // namespace v2::login
