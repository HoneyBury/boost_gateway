#include "v2/aoi/aoi_grid.h"

#include <mutex>
#include <utility>

namespace v2::aoi {

// ── Hot path: broadcast ────────────────────────────────────────────────

BOOST_HOT_PATH
void AoiGrid::broadcast(const v2::actor::Message& message) const {
    auto list = std::atomic_load_explicit(&observers_, std::memory_order_acquire);
    for (const auto& ref : *list) {
        if (ref.is_valid()) {
            ref.tell(message);
        }
    }
}

// ── Writer path helpers ────────────────────────────────────────────────

namespace {

// Determine whether the given elapsed time and modify count exceed the
// coalescing thresholds.
inline bool should_enter_batch_mode(std::uint32_t elapsed_ms,
                                     std::uint32_t modify_count,
                                     std::uint32_t batch_window_ms,
                                     std::uint32_t batch_threshold) {
    return elapsed_ms < batch_window_ms && modify_count >= batch_threshold;
}

}  // anonymous namespace

// ── add_watch ──────────────────────────────────────────────────────────

void AoiGrid::add_watch(v2::actor::ActorRef observer) {
    std::unique_lock lock(write_mutex_);

    // Write coalescing logic.
    auto now = std::chrono::steady_clock::now();
    auto elapsed_ms = static_cast<std::uint32_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            now - last_modify_time_).count());

    if (elapsed_ms >= batch_window_ms_) {
        // Window expired — reset.
        batch_mode_ = false;
        modify_count_ = 0;
    }

    last_modify_time_ = now;
    ++modify_count_;

    if (!batch_mode_ &&
        should_enter_batch_mode(elapsed_ms, modify_count_,
                                 batch_window_ms_, batch_threshold_)) {
        batch_mode_ = true;
    }

    // RCU: copy current list, append, publish.
    auto old_list = std::atomic_load_explicit(&observers_, std::memory_order_relaxed);
    auto new_list = std::make_shared<std::vector<v2::actor::ActorRef>>(*old_list);
    new_list->push_back(std::move(observer));
    std::atomic_store_explicit(
        &observers_,
        std::static_pointer_cast<const std::vector<v2::actor::ActorRef>>(std::move(new_list)),
        std::memory_order_release);
}

// ── remove_watch ───────────────────────────────────────────────────────

void AoiGrid::remove_watch(const v2::actor::ActorRef& observer) {
    std::unique_lock lock(write_mutex_);

    auto old_list = std::atomic_load_explicit(&observers_, std::memory_order_relaxed);
    auto new_list = std::make_shared<std::vector<v2::actor::ActorRef>>();
    new_list->reserve(old_list->size());

    for (const auto& ref : *old_list) {
        if (ref.actor_id() != observer.actor_id()) {
            new_list->push_back(ref);
        }
    }

    std::atomic_store_explicit(
        &observers_,
        std::static_pointer_cast<const std::vector<v2::actor::ActorRef>>(std::move(new_list)),
        std::memory_order_release);
}

// ── Queries ────────────────────────────────────────────────────────────

ActorRefList AoiGrid::snapshot() const {
    return std::atomic_load_explicit(&observers_, std::memory_order_acquire);
}

std::size_t AoiGrid::size() const noexcept {
    auto list = std::atomic_load_explicit(&observers_, std::memory_order_acquire);
    return list->size();
}

}  // namespace v2::aoi
