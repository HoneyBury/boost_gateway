// v3.2.0: Redis connection pool implementation.

#include "v3/persistence/redis_connection_pool.h"

#include <utility>

namespace v3::persistence {

// ── PooledConnection ──────────────────────────────────────────────────

PooledConnection::PooledConnection(RedisClient* client, RedisConnectionPool* pool)
    : client_(client), pool_(pool) {}

PooledConnection::~PooledConnection() { release(); }

PooledConnection::PooledConnection(PooledConnection&& other) noexcept
    : client_(std::exchange(other.client_, nullptr))
    , pool_(std::exchange(other.pool_, nullptr)) {}

PooledConnection& PooledConnection::operator=(PooledConnection&& other) noexcept {
    if (this != &other) {
        release();
        client_ = std::exchange(other.client_, nullptr);
        pool_ = std::exchange(other.pool_, nullptr);
    }
    return *this;
}

void PooledConnection::release() {
    if (client_ && pool_) {
        pool_->release(client_);
        client_ = nullptr;
        pool_ = nullptr;
    }
}

// ── RedisConnectionPool ───────────────────────────────────────────────

RedisConnectionPool::RedisConnectionPool(Config config)
    : config_(std::move(config)) {}

RedisConnectionPool::~RedisConnectionPool() {
    // PooledConnections must be destroyed before the pool.
}

PooledConnection RedisConnectionPool::acquire() {
    std::unique_lock lock(mutex_);
    auto deadline = std::chrono::steady_clock::now() + config_.acquire_timeout;

    while (true) {
        // 1. Try an existing idle connection.
        for (auto& slot : slots_) {
            if (slot.in_use) continue;
            if (!slot.client->is_connected()) {
                // Dead connection — try to revive.
                if (!slot.client->reconnect()) continue;
            }
            slot.in_use = true;
            return {slot.client.get(), this};
        }

        // 2. Create a new connection if under max.
        if (slots_.size() < config_.max_size) {
            auto client = std::make_unique<RedisClient>(config_.redis);
            if (client->reconnect()) {
                auto* ptr = client.get();
                slots_.push_back({std::move(client), true});
                return {ptr, this};
            }
        }

        // 3. Wait for a connection to be returned.
        if (cv_.wait_until(lock, deadline) == std::cv_status::timeout) {
            return {};
        }
    }
}

void RedisConnectionPool::release(RedisClient* client) {
    std::lock_guard lock(mutex_);
    for (auto& slot : slots_) {
        if (slot.client.get() == client) {
            slot.in_use = false;
            break;
        }
    }
    cv_.notify_one();
}

std::size_t RedisConnectionPool::size() const {
    std::lock_guard lock(mutex_);
    return slots_.size();
}

std::size_t RedisConnectionPool::idle_count() const {
    std::lock_guard lock(mutex_);
    std::size_t n = 0;
    for (const auto& s : slots_) {
        if (!s.in_use) ++n;
    }
    return n;
}

}  // namespace v3::persistence
