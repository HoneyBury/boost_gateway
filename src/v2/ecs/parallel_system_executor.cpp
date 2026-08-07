#include "v2/ecs/parallel_system_executor.h"

#include <atomic>
#include <condition_variable>
#include <deque>
#include <exception>
#include <functional>
#include <iterator>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <utility>

#include <spdlog/spdlog.h>

namespace v2::ecs {

namespace {

thread_local bool g_ecs_worker_thread = false;

class SharedWorkerPool final {
public:
    SharedWorkerPool() {
        const auto hardware_threads = std::max(1U, std::thread::hardware_concurrency());
        const auto worker_count = std::min(4U, std::max(1U, hardware_threads - 1U));
        workers_.reserve(worker_count);
        for (std::uint32_t i = 0; i < worker_count; ++i) {
            workers_.emplace_back([this]() { worker_loop(); });
        }
    }

    ~SharedWorkerPool() {
        {
            std::lock_guard lock(mutex_);
            stopping_ = true;
        }
        cv_.notify_all();
        for (auto& worker : workers_) {
            if (worker.joinable()) {
                worker.join();
            }
        }
    }

    void submit(std::function<void()> task) {
        {
            std::lock_guard lock(mutex_);
            tasks_.push_back(std::move(task));
        }
        cv_.notify_one();
    }

    [[nodiscard]] bool is_worker_thread() const noexcept {
        return g_ecs_worker_thread;
    }

private:
    void worker_loop() {
        g_ecs_worker_thread = true;
        for (;;) {
            std::function<void()> task;
            {
                std::unique_lock lock(mutex_);
                cv_.wait(lock, [this]() { return stopping_ || !tasks_.empty(); });
                if (stopping_ && tasks_.empty()) {
                    return;
                }
                task = std::move(tasks_.front());
                tasks_.pop_front();
            }
            task();
        }
    }

    std::mutex mutex_;
    std::condition_variable cv_;
    std::deque<std::function<void()>> tasks_;
    std::vector<std::thread> workers_;
    bool stopping_ = false;
};

struct StageCompletion {
    explicit StageCompletion(std::size_t task_count)
        : remaining(task_count), errors(task_count + 1) {}

    std::atomic<std::size_t> remaining;
    std::vector<std::exception_ptr> errors;
    std::mutex mutex;
    std::condition_variable cv;
};

SharedWorkerPool& shared_worker_pool() {
    static SharedWorkerPool pool;
    return pool;
}

}  // namespace

// ============================================================================
// SequentialSystemExecutor
// ============================================================================

BOOST_HOT_PATH
std::size_t SequentialSystemExecutor::execute_all(World& world,
                                                   const FrameContext& ctx) {
    for (auto& entry : entries_) {
        entry.system->run(world, ctx);
    }
    return entries_.size();
}

void SequentialSystemExecutor::add_system(std::unique_ptr<System> system,
                                           SystemMetadata metadata) {
    if (!system) return;
    entries_.push_back(SystemEntry{
        .system = std::move(system),
        .metadata = std::move(metadata),
    });
}

void SequentialSystemExecutor::clear() noexcept {
    entries_.clear();
}

std::size_t SequentialSystemExecutor::size() const noexcept {
    return entries_.size();
}

// ============================================================================
// ParallelSystemExecutor
// ============================================================================

void ParallelSystemExecutor::add_system(std::unique_ptr<System> system,
                                         SystemMetadata metadata) {
    if (!system) return;
    entries_.push_back(SystemEntry{
        .system = std::move(system),
        .metadata = std::move(metadata),
    });
    dirty_ = true;
}

void ParallelSystemExecutor::clear() noexcept {
    entries_.clear();
    stages_.clear();
    dirty_ = true;
}

std::size_t ParallelSystemExecutor::size() const noexcept {
    return entries_.size();
}

void ParallelSystemExecutor::rebuild_stages() {
    if (!dirty_) return;
    build_stages();
    dirty_ = false;
}

BOOST_HOT_PATH
std::size_t ParallelSystemExecutor::execute_all(World& world,
                                                  const FrameContext& ctx) {
    if (dirty_) {
        build_stages();
        dirty_ = false;
    }

    if (stages_.empty()) {
        return 0;
    }

    std::size_t executed = 0;

    for (const auto& stage : stages_) {
        if (stage.size() == 1) {
            // Single system — run inline, no overhead.
            const auto idx = stage.front();
            entries_[idx].system->run(world, ctx);
            ++executed;
        } else {
            auto& pool = shared_worker_pool();
            if (pool.is_worker_thread()) {
                for (const auto idx : stage) {
                    try {
                        entries_[idx].system->run(world, ctx);
                    } catch (const std::exception& e) {
                        SPDLOG_ERROR("[ParallelSystemExecutor] System '{}' threw: {}",
                                     entries_[idx].metadata.name,
                                     e.what());
                    } catch (...) {
                        SPDLOG_ERROR(
                            "[ParallelSystemExecutor] System '{}' threw a non-standard exception",
                            entries_[idx].metadata.name);
                    }
                }
                executed += stage.size();
                continue;
            }

            auto completion = std::make_shared<StageCompletion>(stage.size() - 1);
            for (std::size_t i = 1; i < stage.size(); ++i) {
                const auto idx = stage[i];
                pool.submit([this, &world, &ctx, completion, idx, i]() {
                    try {
                        entries_[idx].system->run(world, ctx);
                    } catch (...) {
                        completion->errors[i] = std::current_exception();
                    }
                    bool completed = false;
                    {
                        std::lock_guard lock(completion->mutex);
                        completed = completion->remaining.fetch_sub(
                            1, std::memory_order_release) == 1;
                    }
                    if (completed) {
                        completion->cv.notify_one();
                    }
                });
            }

            try {
                entries_[stage.front()].system->run(world, ctx);
            } catch (...) {
                completion->errors[0] = std::current_exception();
            }
            {
                std::unique_lock lock(completion->mutex);
                completion->cv.wait(lock, [&completion]() {
                    return completion->remaining.load(std::memory_order_acquire) == 0;
                });
            }

            for (std::size_t i = 0; i < completion->errors.size(); ++i) {
                if (!completion->errors[i]) {
                    continue;
                }
                try {
                    std::rethrow_exception(completion->errors[i]);
                } catch (const std::exception& e) {
                    SPDLOG_ERROR("[ParallelSystemExecutor] System '{}' threw: {}",
                                 entries_[stage[i]].metadata.name,
                                 e.what());
                } catch (...) {
                    SPDLOG_ERROR("[ParallelSystemExecutor] System '{}' threw a non-standard exception",
                                 entries_[stage[i]].metadata.name);
                }
            }
            executed += stage.size();
        }
    }

    return executed;
}

// ── Topological sort ───────────────────────────────────────────────────
//
// Kahn's algorithm: compute in-degree for each node, repeatedly emit nodes
// with in-degree zero, removing their outgoing edges.  Each pass of the
// outer loop yields one "stage" (a set of nodes with no dependencies on
// each other).

void ParallelSystemExecutor::build_stages() {
    stages_.clear();

    const auto n = entries_.size();
    if (n == 0) return;

    // Map system_id -> index.
    std::unordered_map<std::string, std::size_t> id_to_idx;
    id_to_idx.reserve(n);
    for (std::size_t i = 0; i < n; ++i) {
        const auto& id = entries_[i].metadata.name;
        if (!id.empty()) {
            id_to_idx[id] = i;
        }
    }

    // Build adjacency list (dependency -> dependent) and in-degree count.
    std::vector<std::vector<std::size_t>> dependents(n);
    std::vector<std::size_t> in_degree(n, 0);

    for (std::size_t i = 0; i < n; ++i) {
        for (const auto& dep : entries_[i].metadata.dependencies) {
            auto it = id_to_idx.find(dep);
            if (it != id_to_idx.end()) {
                dependents[it->second].push_back(i);
                ++in_degree[i];
            }
            // Unknown dependency IDs are silently ignored.
        }
    }

    // Kahn's algorithm.
    std::deque<std::size_t> ready;
    for (std::size_t i = 0; i < n; ++i) {
        if (in_degree[i] == 0) {
            ready.push_back(i);
        }
    }

    std::size_t visited = 0;

    while (!ready.empty()) {
        std::vector<std::size_t> stage;
        stage.reserve(ready.size());

        // Drain all currently-ready nodes into a stage.
        std::deque<std::size_t> next_ready;
        while (!ready.empty()) {
            auto idx = ready.front();
            ready.pop_front();
            stage.push_back(idx);
            ++visited;

            for (auto dependent : dependents[idx]) {
                if (--in_degree[dependent] == 0) {
                    next_ready.push_back(dependent);
                }
            }
        }

        stages_.push_back(std::move(stage));
        ready = std::move(next_ready);
    }

    if (visited != n) {
        // Cycle detected — fall back to sequential execution for the
        // remaining (unvisited) systems to avoid deadlock.
        SPDLOG_WARN(
            "[ParallelSystemExecutor] Detected cycle in system dependencies "
            "({} of {} systems visited).  Falling back to sequential for "
            "remaining systems.",
            visited, n);

        std::vector<std::size_t> fallback_stage;
        for (std::size_t i = 0; i < n; ++i) {
            if (in_degree[i] > 0) {
                fallback_stage.push_back(i);
            }
        }
        if (!fallback_stage.empty()) {
            stages_.push_back(std::move(fallback_stage));
        }
    }
}

}  // namespace v2::ecs
