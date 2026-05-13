#pragma once
// v3.1.0: Redis-backed event store implementing IEventStore.
// Events are stored as JSON strings in Redis lists, keyed by aggregate_id.
// When Redis is unavailable, operations return empty/false gracefully.

#include "v3/persistence/event_store.h"
#include "v3/persistence/redis_client.h"

#include <memory>

namespace v3::persistence {

class RedisEventStore : public IEventStore {
public:
    struct Config {
        RedisClient::Config redis;
        std::string key_prefix = "es";  // Redis key namespace
    };

    explicit RedisEventStore(Config config);
    ~RedisEventStore() override;

    RedisEventStore(const RedisEventStore&) = delete;
    RedisEventStore& operator=(const RedisEventStore&) = delete;

    bool append(EventRecord event) override;
    std::vector<EventRecord> read(
        const std::string& aggregate_id,
        std::uint64_t from_sequence = 0,
        std::uint64_t max_count = 100) override;
    [[nodiscard]] std::uint64_t latest_sequence(
        const std::string& aggregate_id) const override;
    std::vector<EventRecord> read_by_type(
        const std::string& event_type,
        std::uint64_t max_count = 100) override;
    [[nodiscard]] std::uint64_t total_events() const override;

    /// Direct access to the underlying Redis client (for leaderboard etc).
    [[nodiscard]] RedisClient& client();
    [[nodiscard]] bool redis_available() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v3::persistence
