#pragma once

#include "v2/ecs/frame_context.h"
#include "v2/ecs/system.h"
#include "v2/perf/hot_path.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace v2::ecs {

class World;

// ============================================================================
// SystemExecutor (base)
//
// Provides the common interface.  The base implementation runs all systems
// sequentially (same as SimpleWorld::tick).  ParallelSystemExecutor
// overrides to run independent systems concurrently.
// ============================================================================

class SystemExecutor {
public:
    virtual ~SystemExecutor() = default;

    // Execute all registered systems for the given world and frame context.
    // Returns the number of systems that were executed.
    virtual std::size_t execute_all(World& world, const FrameContext& ctx) = 0;

    // Add a system with its metadata (dependencies).
    virtual void add_system(std::unique_ptr<System> system,
                            SystemMetadata metadata = {}) = 0;

    // Remove all systems.
    virtual void clear() noexcept = 0;

    // Number of registered systems.
    [[nodiscard]] virtual std::size_t size() const noexcept = 0;
};

// ============================================================================
// SequentialSystemExecutor
//
// Default implementation: runs all systems in registration order on the
// calling thread.  Equivalent to SimpleWorld::tick().
// ============================================================================

class SequentialSystemExecutor final : public SystemExecutor {
public:
    BOOST_HOT_PATH
    std::size_t execute_all(World& world, const FrameContext& ctx) override;

    void add_system(std::unique_ptr<System> system,
                    SystemMetadata metadata = {}) override;

    void clear() noexcept override;
    [[nodiscard]] std::size_t size() const noexcept override;

private:
    struct SystemEntry {
        std::unique_ptr<System> system;
        SystemMetadata metadata;
    };

    std::vector<SystemEntry> entries_;
};

// ============================================================================
// ParallelSystemExecutor
//
// Executes independent systems on a process-wide bounded worker pool.
//
// Algorithm:
//   1. Topological sort the system list by their declared dependencies.
//   2. Group into "stages" — within a stage, every system has no dependency
//      on another system in the same stage (they can run concurrently).
//   3. Execute each stage sequentially, running all systems inside a stage
//      on persistent workers while the caller executes one system inline.
//   4. Systems with no metadata entry are assumed to depend on nothing and
//      are placed in the first stage.
// ============================================================================

class ParallelSystemExecutor final : public SystemExecutor {
public:
    BOOST_HOT_PATH
    std::size_t execute_all(World& world, const FrameContext& ctx) override;

    void add_system(std::unique_ptr<System> system,
                    SystemMetadata metadata = {}) override;

    void clear() noexcept override;
    [[nodiscard]] std::size_t size() const noexcept override;

    // Rebuild the topological stage ordering.  Called automatically on
    // execute_all() if systems have changed, but can be called manually
    // after bulk registration to amortise the cost.
    void rebuild_stages();

private:
    struct SystemEntry {
        std::unique_ptr<System> system;
        SystemMetadata metadata;
    };

    std::vector<SystemEntry> entries_;
    bool dirty_ = true;

    // Stage list: each stage is a vector of indices into entries_.
    std::vector<std::vector<std::size_t>> stages_;

    // Build topological stages from entries_.
    void build_stages();
};

}  // namespace v2::ecs
