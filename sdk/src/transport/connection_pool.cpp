// SDK v4.0.0: Connection pool implementation.
#include "boost_gateway/sdk/transport/transport.h"

#include <algorithm>
#include <condition_variable>
#include <mutex>
#include <vector>

namespace boost_gateway {
namespace sdk {
namespace transport {

class ConnectionPool::Impl {
public:
    Impl(PoolConfig config, TransportFactory factory)
        : config_(std::move(config)), factory_(std::move(factory)) {}

    ITransport* acquire() {
        std::unique_lock lock(mutex_);

        // Wait until an idle transport is available or we can create a new one.
        while (idle_.empty() && (in_use_.size() + idle_.size()) >= config_.max_connections) {
            cv_.wait(lock);
        }

        // Prefer an idle transport.
        if (!idle_.empty()) {
            auto* t = idle_.back();
            idle_.pop_back();
            in_use_.push_back(t);
            return t;
        }

        // Create a new transport if under the max.
        auto transport = factory_();
        if (!transport) return nullptr;

        // If host/port were set via connect_all, connect the new transport.
        if (!host_.empty() && port_ > 0) {
            if (!transport->connect(host_, port_, config_.connect_timeout)) {
                return nullptr;
            }
        }

        auto* raw = transport.get();
        in_use_.push_back(raw);
        owned_.push_back(std::move(transport));
        return raw;
    }

    void release(ITransport* transport) {
        std::lock_guard lock(mutex_);
        auto it = std::find(in_use_.begin(), in_use_.end(), transport);
        if (it == in_use_.end()) return;
        in_use_.erase(it);
        idle_.push_back(transport);
        cv_.notify_one();
    }

    bool connect_all(const std::string& host, std::uint16_t port) {
        host_ = host;
        port_ = port;

        std::lock_guard lock(mutex_);
        for (std::size_t i = owned_.size(); i < config_.max_connections; ++i) {
            auto transport = factory_();
            if (!transport) return false;
            if (!transport->connect(host, port, config_.connect_timeout)) {
                return false;
            }
            idle_.push_back(transport.get());
            owned_.push_back(std::move(transport));
        }
        cv_.notify_all();
        return true;
    }

    void disconnect_all() {
        std::lock_guard lock(mutex_);
        for (auto* t : in_use_) {
            t->disconnect();
        }
        for (auto* t : idle_) {
            t->disconnect();
        }
        in_use_.clear();
        idle_.clear();
        owned_.clear();
        host_.clear();
        port_ = 0;
    }

    std::size_t available() const {
        std::lock_guard lock(mutex_);
        return idle_.size();
    }

    std::size_t total() const {
        std::lock_guard lock(mutex_);
        return owned_.size();
    }

private:
    PoolConfig config_;
    TransportFactory factory_;
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::vector<ITransport*> idle_;
    std::vector<ITransport*> in_use_;
    std::vector<std::unique_ptr<ITransport>> owned_;
    std::string host_;
    std::uint16_t port_ = 0;
};

ConnectionPool::ConnectionPool(PoolConfig config, TransportFactory factory)
    : impl_(std::make_unique<Impl>(std::move(config), std::move(factory))) {}

ConnectionPool::~ConnectionPool() = default;

ITransport* ConnectionPool::acquire() { return impl_->acquire(); }
void ConnectionPool::release(ITransport* transport) { impl_->release(transport); }
bool ConnectionPool::connect_all(const std::string& host, std::uint16_t port) {
    return impl_->connect_all(host, port);
}
void ConnectionPool::disconnect_all() { impl_->disconnect_all(); }
std::size_t ConnectionPool::available() const { return impl_->available(); }
std::size_t ConnectionPool::total() const { return impl_->total(); }

}  // namespace transport
}  // namespace sdk
}  // namespace boost_gateway
