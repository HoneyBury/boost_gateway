#pragma once
// v3.1.0: Redis client wrapper around hiredis.
// RAII connection management with PIMPL for ABI stability.

#include <chrono>
#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace v3::persistence {

class RedisClient {
public:
    struct Config {
        std::string host = "127.0.0.1";
        std::uint16_t port = 6379;
        std::chrono::milliseconds timeout{1000};
        std::string password;
    };

    explicit RedisClient(Config config);
    ~RedisClient();

    RedisClient(const RedisClient&) = delete;
    RedisClient& operator=(const RedisClient&) = delete;
    RedisClient(RedisClient&&) noexcept;
    RedisClient& operator=(RedisClient&&) noexcept;

    bool is_connected() const;
    bool reconnect();

    // String operations
    std::optional<std::string> get(const std::string& key);
    bool set(const std::string& key, const std::string& value);
    bool del(const std::string& key);
    bool exists(const std::string& key);
    std::int64_t incr(const std::string& key);

    // List operations
    bool lpush(const std::string& key, const std::string& value);
    std::vector<std::string> lrange(const std::string& key,
                                     std::int64_t start, std::int64_t stop);
    std::int64_t llen(const std::string& key);

    // Sorted set operations
    bool zadd(const std::string& key, double score, const std::string& member);
    std::vector<std::pair<std::string, double>>
        zrange_with_scores(const std::string& key,
                           std::int64_t start, std::int64_t stop);
    std::int64_t zcard(const std::string& key);

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v3::persistence
