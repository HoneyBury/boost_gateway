#include "v2/login/login_backend_service.h"

#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <thread>

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

}  // namespace

int main(int argc, char* argv[]) {
    std::uint16_t port = 9202;
    if (argc > 1) {
        port = static_cast<std::uint16_t>(std::stoi(argv[1]));
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    v2::login::LoginBackendService service(port);
    g_service = &service;

    service.start();
    std::cout << "v2_login_backend: listening on port " << service.local_port() << std::endl;
    std::cout << "v2_login_backend: running (Ctrl+C to stop)" << std::endl;

    while (g_running) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    service.stop();
    std::cout << "v2_login_backend: stopped" << std::endl;
    return 0;
}
