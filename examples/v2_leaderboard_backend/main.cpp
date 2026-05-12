// v2.3.0 G2: Leaderboard backend example — thin wrapper.
#include "v2/leaderboard/leaderboard_service.h"
#include <cstdlib>
#include <iostream>
int main() {
    std::uint16_t port = 9305;
    const char* env_port = std::getenv("LEADERBOARD_PORT");
    if (env_port) port = static_cast<std::uint16_t>(std::atoi(env_port));
    v2::leaderboard::LeaderboardService service(port);
    service.start();
    std::cout << "Leaderboard backend on port " << port << std::endl;
    std::cout << "Press Enter to stop..." << std::endl;
    std::cin.get();
    service.stop();
    return 0;
}
