#pragma once

#include "v2/actor/actor_ref.h"
#include "v2/actor/message.h"
#include "v2/perf/hot_path.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <shared_mutex>
#include <vector>

namespace v2::aoi {

// ============================================================================
// RCU-protected actor-reference list for lock-free AOI broadcast.
//
// The hot path (broadcast) takes an atomic snapshot of the observer list and
// iterates it without holding any lock. Writer threads (add / remove) use a
// shared_mutex to serialize, copy the current list, mutate the copy, then
// atomically publish the new list via the C++20 shared_ptr atomic free functions.
//
// Write coalescing (back-off):
//   If more than 10 modifications happen within a 100 ms window the grid enters
//   "batch mode" — subsequent modifications still build fresh snapshots, but the
//   caller is expected to batch updates.  Batch mode resets when 100 ms elapse
//   with fewer than 10 modifications.
// ============================================================================

using ActorRefList = std::shared_ptr<const std::vector<v2::actor::ActorRef>>;

class AoiGrid {
public:
    AoiGrid() = default;

    // Not copyable / movable.
    AoiGrid(const AoiGrid&) = delete;
    AoiGrid& operator=(const AoiGrid&) = delete;

    // ── Hot path ─────────────────────────────────────────────────────────

    // Broadcast a message to every observer in the current snapshot.
    // Does NOT block writers (add_watch / remove_watch).
    BOOST_HOT_PATH void broadcast(const v2::actor::Message& message) const;

    // ── Writer path ──────────────────────────────────────────────────────

    // Add an observer to the watch list.  Publishes a new RCU snapshot.
    void add_watch(v2::actor::ActorRef observer);

    // Remove an observer from the watch list.  Publishes a new RCU snapshot.
    void remove_watch(const v2::actor::ActorRef& observer);

    // ── Queries ──────────────────────────────────────────────────────────

    // Obtain a copy of the current snapshot for manual iteration.
    [[nodiscard]] ActorRefList snapshot() const;

    // Current number of observers.
    [[nodiscard]] std::size_t size() const noexcept;

    // ── Configuration ────────────────────────────────────────────────────

    void set_batch_window_ms(std::uint32_t ms) noexcept { batch_window_ms_ = ms; }
    void set_batch_threshold(std::uint32_t count) noexcept { batch_threshold_ = count; }

private:
    // Protects the write path (shared_mutex so there is no contention with
    // readers — readers never acquire this lock).
    mutable std::shared_mutex write_mutex_;

    // The RCU-protected observer list.
    ActorRefList observers_{
        std::make_shared<const std::vector<v2::actor::ActorRef>>()};

    // ── Write coalescing state (protected by write_mutex_) ───────────────

    std::chrono::steady_clock::time_point last_modify_time_;
    std::uint32_t modify_count_ = 0;
    bool batch_mode_ = false;
    std::uint32_t batch_window_ms_ = 100;
    std::uint32_t batch_threshold_ = 10;
};

}  // namespace v2::aoi
