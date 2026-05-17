#pragma once

#include <cstdint>
#include <memory>
#include <string>

namespace v2::login {

struct LoginBackendOptions {
    std::uint16_t port = 9202;
    // Production mode rejects the dev "token:user_id" fallback unless a JWT
    // verifier is configured.
    bool production_auth_required = false;
    // JWT config:
    // - if jwt_secret is set, HS256 validation is enabled
    // - if jwt_public_key_pem is set, RS256 validation is enabled
    // - if neither is set, falls back to dev-mode "token:user_id" format
    //   only when production_auth_required is false
    std::string jwt_secret;
    std::string jwt_public_key_pem;
    std::string jwt_private_key_pem;
    std::string jwt_issuer = "boost-gateway";
    std::string jwt_audience;
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
