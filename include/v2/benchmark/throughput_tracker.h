#pragma once

#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <mutex>

namespace v2::benchmark {

struct ThroughputSnapshot {
    double rate_per_second = 0.0;
    std::uint64_t total_count = 0;
    std::uint64_t window_seconds = 0;
};

class ThroughputTracker {
public:
    // window_seconds: sliding window duration
    // buckets: number of sub-buckets within the window (controls granularity)
    explicit ThroughputTracker(std::uint64_t window_seconds = 5,
                               std::size_t buckets = 10)
        : window_seconds_(window_seconds)
        , bucket_count_(buckets > 0 ? buckets : 1)
        , interval_per_bucket_(std::chrono::milliseconds(window_seconds * 1000) / bucket_count_)
    {
        counts_.fill(0);
        timestamps_.fill(SteadyClock::now());
    }

    void record(std::uint64_t n = 1) noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        advance();
        counts_[head_] += n;
        total_count_ += n;
    }

    [[nodiscard]] ThroughputSnapshot snapshot() const noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        return compute_snapshot();
    }

    [[nodiscard]] std::uint64_t total_count() const noexcept {
        return total_count_.load(std::memory_order_relaxed);
    }

    void reset() noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        counts_.fill(0);
        timestamps_.fill(SteadyClock::now());
        head_ = 0;
        total_count_.store(0, std::memory_order_relaxed);
    }

private:
    using SteadyClock = std::chrono::steady_clock;
    using TimePoint = SteadyClock::time_point;

    void advance() const noexcept {
        const auto now = SteadyClock::now();
        const auto elapsed = now - timestamps_[head_];
        if (elapsed < interval_per_bucket_) return;

        const auto buckets_to_advance =
            static_cast<std::size_t>(elapsed / interval_per_bucket_);
        const auto advance_count = std::min(buckets_to_advance, bucket_count_);

        for (std::size_t i = 0; i < advance_count; ++i) {
            head_ = (head_ + 1) % bucket_count_;
            counts_[head_] = 0;
            timestamps_[head_] = now;
        }
    }

    [[nodiscard]] ThroughputSnapshot compute_snapshot() const noexcept {
        advance();
        std::uint64_t window_total = 0;
        for (std::size_t i = 0; i < bucket_count_; ++i) {
            window_total += counts_[i];
        }

        ThroughputSnapshot snap;
        snap.total_count = total_count_.load(std::memory_order_relaxed);
        snap.window_seconds = window_seconds_;
        snap.rate_per_second = static_cast<double>(window_total) /
                               static_cast<double>(window_seconds_);
        return snap;
    }

    std::uint64_t window_seconds_;
    std::size_t bucket_count_;
    std::chrono::milliseconds interval_per_bucket_;

    mutable std::mutex mutex_;
    mutable std::size_t head_ = 0;
    mutable std::array<std::uint64_t, 20> counts_{};
    mutable std::array<TimePoint, 20> timestamps_{};
    std::atomic<std::uint64_t> total_count_{0};
};

}  // namespace v2::benchmark
