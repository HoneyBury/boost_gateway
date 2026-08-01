#pragma once

#include "v2/service/service_id.h"

#include <array>
#include <cmath>
#include <cstdint>
#include <limits>
#include <mutex>
#include <unordered_map>

namespace v2::gateway {

inline constexpr std::array<std::uint64_t, 15> kBackendLatencyBucketUpperBoundsUs{
    1'000,
    2'000,
    5'000,
    10'000,
    20'000,
    50'000,
    100'000,
    200'000,
    500'000,
    1'000'000,
    2'000'000,
    5'000'000,
    10'000'000,
    30'000'000,
    std::numeric_limits<std::uint64_t>::max(),
};

struct BackendMetricsSnapshot {
    std::uint64_t total_requests = 0;
    std::uint64_t total_successes = 0;
    std::uint64_t total_timeouts = 0;
    std::uint64_t total_unavailable = 0;
    std::uint64_t total_errors = 0;
    std::uint64_t total_degraded = 0;
    std::uint64_t transport_not_connected = 0;
    std::uint64_t transport_write_failures = 0;
    std::uint64_t transport_read_failures = 0;
    std::uint64_t transport_retry_recovered = 0;
    std::uint64_t transport_retry_exhausted = 0;
    std::uint64_t total_latency_us = 0;
    std::uint64_t latency_sample_count = 0;
    std::array<std::uint64_t, kBackendLatencyBucketUpperBoundsUs.size()> latency_bucket_counts{};
};

inline std::uint64_t backend_latency_percentile_us(
    const BackendMetricsSnapshot& snapshot,
    double percentile) {
    if (snapshot.latency_sample_count == 0) {
        return 0;
    }
    if (percentile <= 0.0) {
        percentile = 0.0;
    } else if (percentile > 1.0) {
        percentile = 1.0;
    }

    const auto target = static_cast<std::uint64_t>(
        std::ceil(static_cast<double>(snapshot.latency_sample_count) * percentile));
    const auto rank = target == 0 ? std::uint64_t{1} : target;

    std::uint64_t cumulative = 0;
    for (std::size_t i = 0; i < snapshot.latency_bucket_counts.size(); ++i) {
        cumulative += snapshot.latency_bucket_counts[i];
        if (cumulative >= rank) {
            return kBackendLatencyBucketUpperBoundsUs[i];
        }
    }
    return kBackendLatencyBucketUpperBoundsUs.back();
}

class BackendMetrics {
public:
    void record_request(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].total_requests++;
    }

    void record_success(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].total_successes++;
    }

    void record_timeout(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].total_timeouts++;
    }

    void record_unavailable(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].total_unavailable++;
    }

    void record_error(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].total_errors++;
    }

    void record_degraded(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].total_degraded++;
    }

    void record_transport_not_connected(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].transport_not_connected++;
    }

    void record_transport_write_failure(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].transport_write_failures++;
    }

    void record_transport_read_failure(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].transport_read_failures++;
    }

    void record_transport_retry_recovered(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].transport_retry_recovered++;
    }

    void record_transport_retry_exhausted(v2::service::ServiceId service) {
        std::scoped_lock lock(mutex_);
        counters_[service].transport_retry_exhausted++;
    }

    void record_latency(v2::service::ServiceId service, std::uint64_t latency_us) {
        std::scoped_lock lock(mutex_);
        auto& c = counters_[service];
        c.total_latency_us += latency_us;
        ++c.latency_sample_count;
        for (std::size_t i = 0; i < kBackendLatencyBucketUpperBoundsUs.size(); ++i) {
            if (latency_us <= kBackendLatencyBucketUpperBoundsUs[i]) {
                ++c.latency_bucket_counts[i];
                break;
            }
        }
    }

    [[nodiscard]] BackendMetricsSnapshot snapshot(
        v2::service::ServiceId service) const {
        std::scoped_lock lock(mutex_);
        auto it = counters_.find(service);
        if (it != counters_.end()) return it->second;
        return {};
    }

    [[nodiscard]] std::unordered_map<v2::service::ServiceId, BackendMetricsSnapshot>
    all_snapshots() const {
        std::scoped_lock lock(mutex_);
        return counters_;
    }

private:
    mutable std::mutex mutex_;
    std::unordered_map<v2::service::ServiceId, BackendMetricsSnapshot> counters_;
};

inline const char* service_id_to_key(v2::service::ServiceId id) {
    switch (id) {
        case v2::service::ServiceId::kLogin:       return "login";
        case v2::service::ServiceId::kRoom:        return "room";
        case v2::service::ServiceId::kBattle:      return "battle";
        case v2::service::ServiceId::kMatchmaking: return "matchmaking";
        case v2::service::ServiceId::kLeaderboard: return "leaderboard";
        default: return "unknown";
    }
}

}  // namespace v2::gateway
