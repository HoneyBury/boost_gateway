#pragma once

#include "v2/memory/cache_line.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <numeric>
#include <vector>

// ============================================================================
// Hot / Cold path annotations
//
// BOOST_HOT_PATH  — annotate a function on the hot execution path.
//                    GCC/Clang: __attribute__((hot)).
//
// BOOST_COLD_PATH — annotate a rarely-executed path (error handling,
//                    configuration, teardown).
//                    GCC/Clang: __attribute__((cold)).
// ============================================================================

#if defined(__GNUC__) || defined(__clang__)
#define BOOST_HOT_PATH  __attribute__((hot))
#define BOOST_COLD_PATH __attribute__((cold))
#else
#define BOOST_HOT_PATH
#define BOOST_COLD_PATH
#endif

namespace v2::perf {

// ============================================================================
// PerfCounter — thread-local microsecond-resolution performance counter.
//
// Usage:
//   static thread_local PerfCounter counter("aoi_broadcast");
//   auto start = counter.record();
//   // ... hot code ...
//   auto elapsed_us = counter.latency_us(start);
//   counter.sample(elapsed_us);
//   auto snap = counter.snapshot();  // {count, min, max, avg, p50, p99}
// ============================================================================

struct PerfSnapshot {
    std::uint64_t count = 0;
    double min_us = 0.0;
    double max_us = 0.0;
    double avg_us = 0.0;
    double p50_us = 0.0;
    double p99_us = 0.0;
};

class PerfCounter {
public:
    explicit PerfCounter(const char* name) noexcept;

    PerfCounter(const PerfCounter&) = delete;
    PerfCounter& operator=(const PerfCounter&) = delete;

    // Record a timestamp (wall clock).
    // Returns an opaque timestamp that can be passed to latency_us().
    [[nodiscard]] std::uint64_t record() noexcept;

    // Compute microseconds elapsed since `start` (from record()).
    [[nodiscard]] double latency_us(std::uint64_t start) const noexcept;

    // Record a single sample value (microseconds).
    void sample(double elapsed_us) noexcept;

    // Return a statistical snapshot of all samples so far.
    [[nodiscard]] PerfSnapshot snapshot() const noexcept;

    // Reset all accumulated samples.
    void reset() noexcept;

    // Accessors.
    [[nodiscard]] const char* name() const noexcept { return name_; }

private:
    static constexpr std::size_t kMaxSamples = 4096;

    const char* name_;

    // TLS storage — placed in a cache-line-aligned struct to avoid false sharing.
    struct alignas(v2::memory::kCacheLineSize) CounterStorage {
        std::vector<double> samples;
        std::uint64_t sample_count = 0;
        double running_min = std::numeric_limits<double>::max();
        double running_max = std::numeric_limits<double>::min();
        double running_sum = 0.0;
    };

    CounterStorage storage_;
};

// ============================================================================
// Global registry of PerfCounters for periodic reporting.
// ============================================================================

void register_perf_counter(PerfCounter* counter) noexcept;
void unregister_perf_counter(PerfCounter* counter) noexcept;

// Dump all registered counter snapshots to spdlog once.
// Returns the number of counters dumped.
std::size_t dump_all_counters() noexcept;

// Periodically call this from a timer (every 60s recommended).
void periodic_perf_report() noexcept;

}  // namespace v2::perf
