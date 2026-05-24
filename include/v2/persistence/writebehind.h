#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>
#include <string>
#include <thread>

namespace v2::persistence {

/// Snapshot of write-behind engine health and throughput.
struct WriteBehindMetrics {
    std::uint64_t pending_count = 0;   ///< Tasks currently in the queue
    std::uint64_t dropped_count = 0;   ///< Tasks dropped due to retry exhaustion
    std::uint64_t retry_count = 0;     ///< Total retry attempts made
    std::uint64_t success_count = 0;   ///< Tasks completed successfully
    std::uint64_t fail_count = 0;      ///< Tasks that ultimately failed
    double avg_latency_ms = 0.0;       ///< Average execution latency per task
    bool degraded = false;             ///< Engine is in graceful-degradation mode
};

/// A general-purpose write-behind engine with exponential backoff retry,
/// backpressure signalling, graceful degradation, and a drain-with-timeout
/// shutdown path.
///
/// Thread-safe: all public methods may be called from any thread.
class WriteBehindEngine {
public:
    struct Options {
        /// Maximum queue depth before try_push() refuses new work.
        std::size_t max_queue_size = 10000;

        /// Queue depth that triggers graceful-degradation mode.
        std::size_t degradation_threshold = 5000;

        /// Initial retry delay (doubled after each failure).
        std::chrono::milliseconds base_retry_delay{100};

        /// Upper bound on retry delay.
        std::chrono::milliseconds max_retry_delay{5000};

        /// Maximum retry attempts per task before it is dropped.
        int max_retries = 3;
    };

    explicit WriteBehindEngine(Options opts);
    WriteBehindEngine();
    ~WriteBehindEngine();

    // -- Push ----------------------------------------------------------------

    /// Try to enqueue a task. Returns false when the queue is at capacity
    /// (backpressure); the caller should degrade its own behaviour.
    bool try_push(std::function<bool()> task);

    /// Enqueue a task, blocking if the queue is at capacity.
    void push(std::function<bool()> task);

    // -- Lifecycle -----------------------------------------------------------

    /// Block until all enqueued tasks have been processed (or have exhausted
    /// their retries and been dropped).
    void flush();

    /// Wait up to @p timeout for completion, then discard any remaining
    /// tasks. Returns the number of discarded tasks.
    std::uint64_t drain(std::chrono::milliseconds timeout);

    // -- Introspection -------------------------------------------------------

    /// Atomic snapshot of internal metrics.
    WriteBehindMetrics get_metrics() const;

private:
    struct TaskEntry {
        std::function<bool()> task;
        int retries_left;
        std::chrono::steady_clock::time_point ready_at;
        std::chrono::steady_clock::time_point enqueued_at;
    };

    void worker_loop();
    std::chrono::milliseconds retry_delay(int attempt) const;

    Options opts_;

    std::deque<TaskEntry> queue_;
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::thread worker_;
    std::atomic<bool> running_{true};

    // Metrics (accessed under mutex_)
    std::uint64_t dropped_count_ = 0;
    std::uint64_t retry_count_ = 0;
    std::uint64_t success_count_ = 0;
    std::uint64_t fail_count_ = 0;
    std::chrono::steady_clock::duration total_latency_{0};
    std::uint64_t latency_samples_ = 0;
    bool degraded_ = false;
    bool worker_idle_ = true;
};

}  // namespace v2::persistence
