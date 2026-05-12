#pragma once

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <mutex>

namespace v2::benchmark {

inline constexpr std::array<double, 14> kLatencyBucketBoundariesMs = {
    1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0, 30000.0
};

inline constexpr std::size_t kLatencyBucketCount = kLatencyBucketBoundariesMs.size() + 1;

struct LatencyHistogramSnapshot {
    std::size_t total_count = 0;
    double min_ms = 0.0;
    double max_ms = 0.0;
    double p50_ms = 0.0;
    double p90_ms = 0.0;
    double p99_ms = 0.0;
    std::array<std::size_t, kLatencyBucketCount> bucket_counts{};
};

class LatencyHistogram {
public:
    LatencyHistogram() = default;

    void record_us(std::uint64_t latency_us) noexcept {
        record_ms(static_cast<double>(latency_us) / 1000.0);
    }

    void record_ms(double latency_ms) noexcept {
        if (latency_ms < 0.0) return;

        std::lock_guard<std::mutex> lock(mutex_);
        ++bucket_counts_[bucket_index(latency_ms)];
        const auto n = ++total_internal_;
        if (n == 1) {
            min_ms_ = latency_ms;
            max_ms_ = latency_ms;
        } else {
            min_ms_ = std::min(min_ms_, latency_ms);
            max_ms_ = std::max(max_ms_, latency_ms);
        }
        total_count_.store(n, std::memory_order_relaxed);
    }

    [[nodiscard]] LatencyHistogramSnapshot snapshot() const noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        return compute_snapshot();
    }

    [[nodiscard]] LatencyHistogramSnapshot drain() noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto snap = compute_snapshot();
        reset_locked();
        return snap;
    }

    void reset() noexcept {
        std::lock_guard<std::mutex> lock(mutex_);
        reset_locked();
    }

    [[nodiscard]] std::size_t total_count() const noexcept {
        return total_count_.load(std::memory_order_relaxed);
    }

private:
    void reset_locked() noexcept {
        total_count_.store(0, std::memory_order_relaxed);
        total_internal_ = 0;
        min_ms_ = 0.0;
        max_ms_ = 0.0;
        bucket_counts_.fill(0);
    }

    [[nodiscard]] LatencyHistogramSnapshot compute_snapshot() const noexcept {
        LatencyHistogramSnapshot snap;
        snap.total_count = total_internal_;
        snap.min_ms = total_internal_ > 0 ? min_ms_ : 0.0;
        snap.max_ms = total_internal_ > 0 ? max_ms_ : 0.0;
        snap.bucket_counts = bucket_counts_;

        if (total_internal_ == 0) return snap;

        const auto p50_idx = total_internal_ * 50 / 100;
        const auto p90_idx = total_internal_ * 90 / 100;
        const auto p99_idx = total_internal_ * 99 / 100;

        std::size_t cumulative = 0;
        std::size_t p50_idx_val = 0;
        std::size_t p90_idx_val = 0;
        std::size_t p99_idx_val = 0;
        bool p50_set = false;
        bool p90_set = false;

        for (std::size_t i = 0; i < kLatencyBucketCount; ++i) {
            cumulative += bucket_counts_[i];
            if (!p50_set && cumulative >= p50_idx) {
                p50_idx_val = i;
                p50_set = true;
            }
            if (!p90_set && cumulative > p90_idx) {
                p90_idx_val = i;
                p90_set = true;
            }
            if (cumulative > p99_idx) {
                p99_idx_val = i;
                break;
            }
        }

        snap.p50_ms = bucket_upper_bound(p50_idx_val);
        snap.p90_ms = bucket_upper_bound(p90_idx_val);
        snap.p99_ms = bucket_upper_bound(p99_idx_val);
        return snap;
    }

    static std::size_t bucket_index(double latency_ms) noexcept {
        for (std::size_t i = 0; i < kLatencyBucketBoundariesMs.size(); ++i) {
            if (latency_ms <= kLatencyBucketBoundariesMs[i]) {
                return i;
            }
        }
        return kLatencyBucketBoundariesMs.size();  // overflow bucket
    }

    static double bucket_upper_bound(std::size_t idx) noexcept {
        if (idx >= kLatencyBucketBoundariesMs.size()) return kLatencyBucketBoundariesMs.back() * 2.0;
        return kLatencyBucketBoundariesMs[idx];
    }

    mutable std::mutex mutex_;
    std::atomic<std::size_t> total_count_{0};
    std::size_t total_internal_ = 0;
    double min_ms_ = 0.0;
    double max_ms_ = 0.0;
    std::array<std::size_t, kLatencyBucketCount> bucket_counts_{};
};

}  // namespace v2::benchmark
