#pragma once

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>

namespace v2::gateway_pressure {

struct LoadEvidenceSnapshot {
    std::size_t target_clients = 0;
    std::size_t started_clients = 0;
    std::size_t tcp_connected_clients = 0;
    std::size_t authenticated_clients = 0;
    std::size_t active_clients = 0;
    std::size_t peak_active_clients = 0;
    std::size_t cancelled_clients = 0;
    std::size_t cancelled_before_connect = 0;
    bool ramp_completed = false;
    bool measurement_started = false;
    double ramp_up_seconds = 0.0;
    double steady_state_elapsed_seconds = 0.0;
};

class LoadEvidence {
public:
    using Clock = std::chrono::steady_clock;

    explicit LoadEvidence(std::size_t target_clients, Clock::time_point run_started = Clock::now())
        : target_clients_(target_clients), run_started_ns_(to_ns(run_started)) {}

    void on_started() noexcept {
        started_clients_.fetch_add(1, std::memory_order_relaxed);
    }

    void on_tcp_connected() noexcept {
        tcp_connected_clients_.fetch_add(1, std::memory_order_relaxed);
    }

    bool on_authenticated(Clock::time_point now = Clock::now()) noexcept {
        const auto active = active_clients_.fetch_add(1, std::memory_order_relaxed) + 1;
        update_peak(active);
        const auto authenticated = authenticated_clients_.fetch_add(1, std::memory_order_relaxed) + 1;
        if (authenticated != target_clients_) {
            return false;
        }

        const auto now_ns = to_ns(now);
        measurement_started_ns_.store(now_ns, std::memory_order_relaxed);
        bool expected = false;
        return measurement_started_.compare_exchange_strong(
            expected, true, std::memory_order_release, std::memory_order_relaxed);
    }

    void on_terminal(bool was_authenticated, bool cancelled, bool before_connect) noexcept {
        if (was_authenticated) {
            active_clients_.fetch_sub(1, std::memory_order_relaxed);
        }
        if (cancelled) {
            cancelled_clients_.fetch_add(1, std::memory_order_relaxed);
            if (before_connect) {
                cancelled_before_connect_.fetch_add(1, std::memory_order_relaxed);
            }
        }
    }

    void finish_measurement(Clock::time_point now = Clock::now()) noexcept {
        if (!measurement_started()) {
            return;
        }
        auto expected = std::int64_t{0};
        (void)measurement_finished_ns_.compare_exchange_strong(
            expected, to_ns(now), std::memory_order_relaxed);
    }

    [[nodiscard]] bool measurement_started() const noexcept {
        return measurement_started_.load(std::memory_order_acquire);
    }

    [[nodiscard]] LoadEvidenceSnapshot snapshot(Clock::time_point now = Clock::now()) const noexcept {
        const auto started_ns = measurement_started_ns_.load(std::memory_order_relaxed);
        auto finished_ns = measurement_finished_ns_.load(std::memory_order_relaxed);
        if (finished_ns == 0 && started_ns != 0) {
            finished_ns = to_ns(now);
        }
        return {
            target_clients_,
            started_clients_.load(std::memory_order_relaxed),
            tcp_connected_clients_.load(std::memory_order_relaxed),
            authenticated_clients_.load(std::memory_order_relaxed),
            active_clients_.load(std::memory_order_relaxed),
            peak_active_clients_.load(std::memory_order_relaxed),
            cancelled_clients_.load(std::memory_order_relaxed),
            cancelled_before_connect_.load(std::memory_order_relaxed),
            measurement_started(),
            measurement_started(),
            started_ns == 0 ? 0.0 : seconds(started_ns - run_started_ns_),
            started_ns == 0 ? 0.0 : seconds(finished_ns - started_ns),
        };
    }

private:
    static std::int64_t to_ns(Clock::time_point value) noexcept {
        return std::chrono::duration_cast<std::chrono::nanoseconds>(value.time_since_epoch()).count();
    }

    static double seconds(std::int64_t nanoseconds) noexcept {
        return static_cast<double>(nanoseconds) / 1'000'000'000.0;
    }

    void update_peak(std::size_t active) noexcept {
        auto peak = peak_active_clients_.load(std::memory_order_relaxed);
        while (active > peak && !peak_active_clients_.compare_exchange_weak(
                                  peak, active, std::memory_order_relaxed)) {
        }
    }

    const std::size_t target_clients_;
    const std::int64_t run_started_ns_;
    std::atomic<std::size_t> started_clients_{0};
    std::atomic<std::size_t> tcp_connected_clients_{0};
    std::atomic<std::size_t> authenticated_clients_{0};
    std::atomic<std::size_t> active_clients_{0};
    std::atomic<std::size_t> peak_active_clients_{0};
    std::atomic<std::size_t> cancelled_clients_{0};
    std::atomic<std::size_t> cancelled_before_connect_{0};
    std::atomic<std::int64_t> measurement_started_ns_{0};
    std::atomic<std::int64_t> measurement_finished_ns_{0};
    std::atomic<bool> measurement_started_{false};
};

}  // namespace v2::gateway_pressure
