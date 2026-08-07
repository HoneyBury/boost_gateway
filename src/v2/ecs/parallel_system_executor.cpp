#include "v2/ecs/parallel_system_executor.h"

#include <deque>
#include <iterator>
#include <stdexcept>
#include <utility>

#include <spdlog/spdlog.h>

namespace v2::ecs {

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
            // Multiple independent systems — launch in parallel.
            std::vector<std::future<void>> futures;
            futures.reserve(stage.size());

            for (const auto idx : stage) {
                futures.push_back(std::async(std::launch::async, [this, &world, &ctx, idx]() {
                    entries_[idx].system->run(world, ctx);
                }));
            }

            // Wait for all systems in this stage to complete.
            for (std::size_t i = 0; i < futures.size(); ++i) {
                try {
                    futures[i].get();
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
