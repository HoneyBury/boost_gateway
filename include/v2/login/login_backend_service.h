#pragma once

#include <cstdint>
#include <memory>
#include <optional>
#include <string>

#include "v3/cluster/tls_config.h"

namespace v2::login {

struct LoginBackendOptions {
    std::uint16_t port = 9202;
    // Production mode is an external-identity boundary: it accepts only RS256
    // JWTs verified with a configured public key and does not issue credentials.
    bool production_auth_required = false;
    // JWT config:
    // - development mode accepts an HMAC secret or RS256 public key
    // - production mode requires jwt_public_key_pem and rejects symmetric keys
    // - if neither is set, falls back to dev-mode "token:user_id" format
    //   only when production_auth_required is false
    std::string jwt_secret;
    std::string jwt_public_key_pem;
    std::string jwt_private_key_pem;
    std::string jwt_issuer = "boost-gateway";
    std::string jwt_audience;
    std::optional<v3::cluster::TlsSessionConfig> tls_config;
};

class LoginBackendService {
public:
    explicit LoginBackendService(std::uint16_t port);
    explicit LoginBackendService(LoginBackendOptions options);
    ~LoginBackendService();

    void start();
    void stop();
    [[nodiscard]] std::uint16_t local_port() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::login
