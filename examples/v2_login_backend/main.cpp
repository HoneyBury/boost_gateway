#include "v2/login/login_backend_service.h"

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <utility>

namespace {

std::atomic<bool> g_running{true};
v2::login::LoginBackendService* g_service = nullptr;

void handle_signal(int) {
    g_running = false;
    if (g_service) {
        std::cout << "\nv2_login_backend: shutting down..." << std::endl;
        g_service->stop();
    }
}

std::string env_or_empty(const char* name) {
    const char* value = std::getenv(name);
    return value ? std::string(value) : std::string();
}

bool production_auth_required() {
    const auto mode = env_or_empty("V2_LOGIN_AUTH_MODE");
    return mode == "production" || mode == "prod" || mode == "jwt";
}

}  // namespace

int main(int argc, char* argv[]) {
    std::uint16_t port = 9202;
    if (argc > 1) {
        port = static_cast<std::uint16_t>(std::stoi(argv[1]));
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    try {
        v2::login::LoginBackendOptions options;
        options.port = port;
        options.production_auth_required = production_auth_required();
        options.jwt_secret = env_or_empty("V2_LOGIN_JWT_SECRET");
        options.jwt_public_key_pem = env_or_empty("V2_LOGIN_JWT_PUBLIC_KEY");
        options.jwt_private_key_pem = env_or_empty("V2_LOGIN_JWT_PRIVATE_KEY");
        const auto jwt_issuer = env_or_empty("V2_LOGIN_JWT_ISSUER");
        options.jwt_issuer = jwt_issuer.empty() ? "boost-gateway" : jwt_issuer;
        options.jwt_audience = env_or_empty("V2_LOGIN_JWT_AUDIENCE");

        v2::login::LoginBackendService service(std::move(options));
        g_service = &service;

        service.start();
        std::cout << "v2_login_backend: listening on port " << service.local_port() << std::endl;
        std::cout << "v2_login_backend: running (Ctrl+C to stop)" << std::endl;

        while (g_running) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        service.stop();
        std::cout << "v2_login_backend: stopped" << std::endl;
    } catch (const std::exception& ex) {
        std::cerr << "v2_login_backend: failed to start: " << ex.what() << std::endl;
        return 1;
    }
    return 0;
}
