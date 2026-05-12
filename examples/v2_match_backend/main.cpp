// v2.3.0 G1: Matchmaking backend example — thin wrapper.
#include "v2/match/matchmaking_service.h"

#include <cstdlib>
#include <iostream>
#include <thread>

int main() {
    std::uint16_t port = 9304;
    const char* env_port = std::getenv("MATCH_PORT");
    if (env_port) port = static_cast<std::uint16_t>(std::atoi(env_port));

    v2::match::MatchmakingService service(port);
    service.start();
    std::cout << "Matchmaking backend listening on port " << port << std::endl;

    // Run until interrupted
    std::cout << "Press Enter to stop..." << std::endl;
    std::cin.get();

    service.stop();
    return 0;
}
