#pragma once

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <limits>
#include <memory>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

namespace v2::test {

// ============================================================================
// LatencyInjector
//
// Wraps a callback and injects a configurable delay before each invocation.
// Supports a fixed delay or a uniform random delay in [min, max].
// Fully thread-safe.
// ============================================================================

class LatencyInjector {
public:
    using Callback = std::function<void()>;

    /// Construct with a fixed delay.
    LatencyInjector(Callback callback, std::chrono::milliseconds delay)
        : callback_(std::move(callback))
        , use_fixed_(true)
        , fixed_delay_(delay)
        , min_delay_(delay)
        , max_delay_(delay)
        , rng_(std::random_device{}())
        , delay_dist_(0, 0) {}

    /// Construct with a uniform random delay in [min_delay, max_delay].
    LatencyInjector(Callback callback,
                    std::chrono::milliseconds min_delay,
                    std::chrono::milliseconds max_delay)
        : callback_(std::move(callback))
        , use_fixed_(false)
        , fixed_delay_(min_delay)
        , min_delay_(min_delay)
        , max_delay_(max_delay)
        , rng_(std::random_device{}())
        , delay_dist_(static_cast<int>(min_delay.count()),
                       static_cast<int>(max_delay.count())) {}

    /// Invoke the callback after applying the configured delay.
    void operator()() {
        auto delay = compute_delay();
        std::this_thread::sleep_for(delay);
        callback_();
    }

    /// Configure a fixed delay.
    void set_delay(std::chrono::milliseconds delay) {
        std::lock_guard<std::mutex> lock(mutex_);
        use_fixed_ = true;
        fixed_delay_ = delay;
        min_delay_ = delay;
        max_delay_ = delay;
        delay_dist_ = std::uniform_int_distribution<int>(0, 0);
    }

    /// Configure a uniform random delay range.
    void set_delay_range(std::chrono::milliseconds min_delay,
                         std::chrono::milliseconds max_delay) {
        std::lock_guard<std::mutex> lock(mutex_);
        use_fixed_ = false;
        fixed_delay_ = min_delay;
        min_delay_ = min_delay;
        max_delay_ = max_delay;
        delay_dist_ = std::uniform_int_distribution<int>(
            static_cast<int>(min_delay.count()),
            static_cast<int>(max_delay.count()));
    }

private:
    std::chrono::milliseconds compute_delay() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (use_fixed_) {
            return fixed_delay_;
        }
        return std::chrono::milliseconds(delay_dist_(rng_));
    }

    Callback callback_;
    bool use_fixed_;
    std::chrono::milliseconds fixed_delay_;
    std::chrono::milliseconds min_delay_;
    std::chrono::milliseconds max_delay_;
    std::mt19937 rng_;
    std::uniform_int_distribution<int> delay_dist_;
    mutable std::mutex mutex_;
};

// ============================================================================
// FailureInjector
//
// Determines whether an operation should fail based on a configurable
// probability rate (0.0 = never, 1.0 = always). Thread-safe. Uses
// std::mt19937 for random number generation.
// ============================================================================

class FailureInjector {
public:
    /// Construct with an initial failure rate.
    explicit FailureInjector(double rate = 0.0)
        : rate_(rate)
        , rng_(std::random_device{}())
        , dist_(0.0, 1.0) {}

    /// Return true if the operation should fail based on the configured rate.
    bool should_fail() {
        std::lock_guard<std::mutex> lock(mutex_);
        return dist_(rng_) < rate_;
    }

    /// Set the failure rate (0.0 to 1.0).
    void set_rate(double rate) {
        std::lock_guard<std::mutex> lock(mutex_);
        rate_ = rate;
    }

    /// Get the current failure rate.
    [[nodiscard]] double rate() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return rate_;
    }

private:
    double rate_;
    std::mt19937 rng_;
    std::uniform_real_distribution<double> dist_;
    mutable std::mutex mutex_;
};

// ============================================================================
// NetworkPartitionSimulator
//
// A simple TCP data relay that can simulate network partitions by dropping
// data after a configurable byte threshold, or by randomly injecting drops
// at a configurable rate.
//
// Implementation is via the pimpl idiom to hide Boost.Asio types from
// consumers of this header.
// ============================================================================

class NetworkPartitionSimulator {
public:
    /// Construct the simulator with relay parameters.
    /// @param listen_port  Local port to listen on (0 = OS-assigned).
    /// @param target_host  Upstream host to relay to.
    /// @param target_port  Upstream port to relay to.
    NetworkPartitionSimulator(uint16_t listen_port,
                              const std::string& target_host,
                              uint16_t target_port);

    ~NetworkPartitionSimulator();

    /// Start the relay. Spawns a background acceptor thread.
    void start();

    /// Stop the relay, close all connections, and join background threads.
    void stop();

    /// Drop all data after N total bytes have been relayed across all
    /// connections. Set to std::numeric_limits<size_t>::max() to disable.
    void set_drop_after_bytes(size_t bytes);

    /// Randomly drop data at the given rate (0.0 to 1.0).
    void set_drop_rate(double rate);

    /// Return the actual listening port (useful when port 0 was requested).
    [[nodiscard]] uint16_t listen_port() const;

    /// Return true if the relay is currently running.
    [[nodiscard]] bool is_running() const;

    NetworkPartitionSimulator(const NetworkPartitionSimulator&) = delete;
    NetworkPartitionSimulator& operator=(const NetworkPartitionSimulator&) = delete;
    NetworkPartitionSimulator(NetworkPartitionSimulator&&) = delete;
    NetworkPartitionSimulator& operator=(NetworkPartitionSimulator&&) = delete;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::test
