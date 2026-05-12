// BoostGateway SDK: Echo client example.
// Connects to a gateway server, sends an echo request, prints the response.
//
// Usage: sdk_echo_client [host] [port]
//   Default: 127.0.0.1:9201

#include "boost_gateway/sdk/client.h"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <thread>

int main(int argc, char* argv[]) {
    std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    std::uint16_t port = argc > 2 ? static_cast<std::uint16_t>(std::atoi(argv[2])) : 9201;

    std::cout << "BoostGateway SDK Echo Client" << std::endl;
    std::cout << "Connecting to " << host << ":" << port << "..." << std::endl;

    boost_gateway::sdk::SdkClient client;

    // Register push callback
    client.on_push([](const boost_gateway::sdk::PushMessage& push) {
        std::cout << "[PUSH] msg_id=" << push.message_id
                  << " body=" << push.body << std::endl;
    });

    // Connect
    if (!client.connect(host, port, std::chrono::seconds(5))) {
        std::cerr << "Failed to connect to " << host << ":" << port << std::endl;
        return 1;
    }
    std::cout << "Connected!" << std::endl;

    // Login
    auto login = client.login("echo_user", "token:echo_user", std::chrono::seconds(5));
    if (!login.ok) {
        std::cerr << "Login failed: " << login.error_message << std::endl;
        return 1;
    }
    std::cout << "Logged in as: " << login.user_id << std::endl;

    // Echo
    auto echo = client.echo("Hello, Gateway!", std::chrono::seconds(5));
    if (echo.ok) {
        std::cout << "Echo response: " << echo.echo_body << std::endl;
    } else {
        std::cerr << "Echo failed" << std::endl;
    }

    client.disconnect();
    std::cout << "Done." << std::endl;
    return 0;
}
