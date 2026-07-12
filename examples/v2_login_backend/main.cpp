#include "app/config.h"
#include "app/logging.h"
#include "v2/login/login_backend_service.h"
#include "v2/platform/highres_timer.h"

#include <atomic>
#include <chrono>
#include <csignal>
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

bool production_auth_required(const std::string& mode) {
    return mode == "external-jwt" || mode == "production" || mode == "prod" ||
           mode == "jwt";
}

}  // namespace

int main(int argc, char* argv[]) {
    const v2::platform::HighResTimer hi_res_timer;
    app::logging::init("v2_login_backend");

    const auto config_path = app::config::resolve_backend_config_path(
        "login", argc, argv, "config/environments/local/login.json");
    auto config = app::config::load_backend_service_config("login", config_path, 9202);
    if (argc > 1 && std::string(argv[1]) != "--config") {
        config.port = static_cast<std::uint16_t>(std::stoi(argv[1]));
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    try {
        v2::login::LoginBackendOptions options;
        options.port = config.port;
        options.production_auth_required = production_auth_required(config.jwt.mode);
        options.jwt_secret = config.jwt.secret;
        options.jwt_public_key_pem = config.jwt.public_key_pem;
        options.jwt_private_key_pem = config.jwt.private_key_pem;
        options.jwt_issuer = config.jwt.issuer.empty() ? "boost-gateway" : config.jwt.issuer;
        options.jwt_audience = config.jwt.audience;
        options.tls_config = config.tls_config;

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
