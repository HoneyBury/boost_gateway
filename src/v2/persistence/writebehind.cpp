#include "v2/persistence/writebehind.h"

#include <spdlog/spdlog.h>

#include <algorithm>
#include <utility>

namespace v2::persistence {

// -----------------------------------------------------------------------
// Construction / Destruction
// -----------------------------------------------------------------------

WriteBehindEngine::WriteBehindEngine(Options opts)
    : opts_(std::move(opts)) {
    worker_ = std::thread(&WriteBehindEngine::worker_loop, this);
}

WriteBehindEngine::WriteBehindEngine()
    : WriteBehindEngine(Options{}) {}

WriteBehindEngine::~WriteBehindEngine() {
    // Try to drain remaining work with a generous timeout.
    drain(std::chrono::seconds(5));
    running_ = false;
    cv_.notify_all();
    if (worker_.joinable()) {
        worker_.join();
    }
}

// -----------------------------------------------------------------------
// Push
// -----------------------------------------------------------------------

bool WriteBehindEngine::try_push(std::function<bool()> task) {
    if (!task) return false;

    std::lock_guard lock(mutex_);
    if (queue_.size() >= opts_.max_queue_size) {
        return false;  // backpressure
    }

    const auto now = std::chrono::steady_clock::now();
    queue_.push_back(TaskEntry{
        .task = std::move(task),
        .retries_left = opts_.max_retries,
        .ready_at = now,
        .enqueued_at = now,
    });
    cv_.notify_one();
    return true;
}

void WriteBehindEngine::push(std::function<bool()> task) {
    // Spinning try-push with a short yield — this keeps the API simple
    // without pulling in external synchronization primitives.
    while (!try_push(std::move(task))) {
        std::this_thread::yield();
    }
}

// -----------------------------------------------------------------------
// Lifecycle
// -----------------------------------------------------------------------

void WriteBehindEngine::flush() {
    std::unique_lock lock(mutex_);
    cv_.wait(lock, [this] {
        return queue_.empty() && worker_idle_;
    });
}

std::uint64_t WriteBehindEngine::drain(std::chrono::milliseconds timeout) {
    auto deadline = std::chrono::steady_clock::now() + timeout;
    std::unique_lock lock(mutex_);

    // Wait for the worker to finish whatever it is doing, up to the deadline.
    cv_.wait_until(lock, deadline, [this] {
        return queue_.empty() && worker_idle_;
    });

    // Discard whatever is left.
    const std::uint64_t dropped = queue_.size();
    if (dropped > 0) {
        dropped_count_ += dropped;
        queue_.clear();
        spdlog::warn(
            "[WriteBehindEngine] drain timeout -- discarded {} pending tasks",
            dropped);
    }
    return dropped;
}

// -----------------------------------------------------------------------
// Introspection
// -----------------------------------------------------------------------

WriteBehindMetrics WriteBehindEngine::get_metrics() const {
    std::lock_guard lock(mutex_);
    WriteBehindMetrics m;
    m.pending_count = queue_.size();
    m.dropped_count = dropped_count_;
    m.retry_count = retry_count_;
    m.success_count = success_count_;
    m.fail_count = fail_count_;
    m.degraded = degraded_;
    if (latency_samples_ > 0) {
        m.avg_latency_ms = std::chrono::duration<double, std::milli>(
                               total_latency_)
                               .count() /
                           static_cast<double>(latency_samples_);
    }
    return m;
}

// -----------------------------------------------------------------------
// Worker
// -----------------------------------------------------------------------

std::chrono::milliseconds WriteBehindEngine::retry_delay(int attempt) const {
    // Exponential: base * 2^attempt, capped at max_retry_delay.
    auto d = opts_.base_retry_delay;
    for (int i = 0; i < attempt; ++i) {
        d *= 2;
        if (d >= opts_.max_retry_delay) return opts_.max_retry_delay;
    }
    return d;
}

void WriteBehindEngine::worker_loop() {
    while (running_) {
        TaskEntry entry;
        {
            std::unique_lock lock(mutex_);

            // Wait until there is a task that is ready to process.
            cv_.wait(lock, [this] {
                if (!running_) return true;
                if (queue_.empty()) return false;
                return queue_.front().ready_at <= std::chrono::steady_clock::now();
            });

            if (!running_ && queue_.empty()) {
                return;
            }
            if (queue_.empty()) {
                continue;  // spurious wake
            }

            // Check if the front task is actually ready (it should be, but
            // clock rounding can cause edge cases).
            const auto now = std::chrono::steady_clock::now();
            if (queue_.front().ready_at > now) {
                // Sleep until the task is ready, then retry.
                cv_.wait_for(lock, queue_.front().ready_at - now);
                continue;
            }

            worker_idle_ = false;
            entry = std::move(queue_.front());
            queue_.pop_front();
        }

        // --- Execute (outside the lock) ------------------------------------
        const auto exec_start = std::chrono::steady_clock::now();
        const bool ok = entry.task ? entry.task() : false;
        const auto exec_elapsed = std::chrono::steady_clock::now() - exec_start;

        {
            std::lock_guard lock(mutex_);

            if (ok) {
                ++success_count_;
                total_latency_ += exec_elapsed;
                ++latency_samples_;
            } else if (entry.retries_left > 0) {
                // Schedule a retry with exponential backoff.
                const int attempt = opts_.max_retries - entry.retries_left;
                const auto delay = retry_delay(attempt);
                const auto ready_at = std::chrono::steady_clock::now() + delay;

                queue_.push_back(TaskEntry{
                    .task = std::move(entry.task),
                    .retries_left = entry.retries_left - 1,
                    .ready_at = ready_at,
                    .enqueued_at = entry.enqueued_at,
                });
                ++retry_count_;
                spdlog::warn(
                    "[WriteBehindEngine] task failed, retry {}/{} in {}ms",
                    attempt + 1, opts_.max_retries, delay.count());
            } else {
                // No retries left -- drop the task and enter degraded mode.
                ++dropped_count_;
                degraded_ = true;
                spdlog::error(
                    "[WriteBehindEngine] task dropped after {} retries -- "
                    "entering degraded mode",
                    opts_.max_retries);
            }

            worker_idle_ = true;

            // Exit degraded mode when the queue has drained enough.
            if (degraded_ && queue_.size() < opts_.degradation_threshold) {
                degraded_ = false;
                spdlog::info(
                    "[WriteBehindEngine] queue drained, exiting degraded mode");
            }
        }
        cv_.notify_all();
    }
}

}  // namespace v2::persistence
