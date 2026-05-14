// v2.3.0 G2: Leaderboard backend example — thin wrapper.
// v3.3.0 P0b: Optional Redis-backed persistence via REDIS_HOST env var.
#include "v2/leaderboard/leaderboard_service.h"
#include "v3/persistence/redis_client.h"
#include "v3/persistence/redis_leaderboard.h"
#include <cstdlib>
#include <iostream>
#include <memory>

int main() {
    std::uint16_t port = 9305;
    const char* env_port = std::getenv("LEADERBOARD_PORT");
    if (env_port) port = static_cast<std::uint16_t>(std::atoi(env_port));

    v2::leaderboard::LeaderboardService service(port);

    // v3.3.0 P0b: Optional Redis leaderboard backend
    const char* redis_host = std::getenv("REDIS_HOST");
    if (redis_host && redis_host[0] != '\0') {
        v3::persistence::RedisClient::Config redis_config;
        redis_config.host = redis_host;
        const char* redis_port = std::getenv("REDIS_PORT");
        if (redis_port) redis_config.port =
            static_cast<std::uint16_t>(std::atoi(redis_port));
        const char* redis_password = std::getenv("REDIS_PASSWORD");
        if (redis_password) redis_config.password = redis_password;

        v3::persistence::RedisLeaderboard::Config lb_config;
        lb_config.redis = std::move(redis_config);
        lb_config.key = "lb:global";

        auto display_port = lb_config.redis.port;
        auto redis_lb = std::make_shared<v3::persistence::RedisLeaderboard>(
            std::move(lb_config));
        if (redis_lb->available()) {
            service.set_redis_leaderboard(std::move(redis_lb));
            std::cout << "Redis leaderboard enabled ("
                      << redis_host << ":" << display_port << ")"
                      << std::endl;
        } else {
            std::cerr << "Redis unavailable at " << redis_host
                      << ", falling back to in-memory" << std::endl;
        }
    } else {
        std::cout << "Redis not configured, using in-memory leaderboard"
                  << std::endl;
    }

    service.start();
    std::cout << "Leaderboard backend on port " << port << std::endl;
    std::cout << "Press Enter to stop..." << std::endl;
    std::cin.get();
    service.stop();
    return 0;
}
