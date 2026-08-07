#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <memory>
#include <optional>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

namespace v2::io {

// SPSC lock-free ring buffer.
// Single producer thread, single consumer thread — no mutex needed.
template <typename T>
class SpscQueue {
    static constexpr std::size_t kDefaultCapacity = 1024;

    static constexpr std::size_t next_power_of_two(std::size_t n) noexcept {
        if (n <= 1) return 1;
        --n;
        n |= n >> 1;
        n |= n >> 2;
        n |= n >> 4;
        n |= n >> 8;
        n |= n >> 16;
        n |= n >> 32;
        return n + 1;
    }

public:
    explicit SpscQueue(std::size_t capacity = kDefaultCapacity)
        : capacity_(next_power_of_two(capacity))
        , mask_(capacity_ - 1)
        , buffer_(capacity_) {}

    bool try_enqueue(T item) {
        const auto write = write_idx_.load(std::memory_order_relaxed);
        const auto read = read_idx_.load(std::memory_order_acquire);
        if (write - read >= capacity_) {
            return false;
        }
        buffer_[write & mask_] = std::move(item);
        write_idx_.store(write + 1, std::memory_order_release);
        return true;
    }

    std::optional<T> try_dequeue() {
        const auto read = read_idx_.load(std::memory_order_relaxed);
        const auto write = write_idx_.load(std::memory_order_acquire);
        if (read >= write) {
            return std::nullopt;
        }
        T item = std::move(buffer_[read & mask_]);
        read_idx_.store(read + 1, std::memory_order_release);
        return item;
    }

    std::vector<T> drain() {
        std::vector<T> items;
        while (auto item = try_dequeue()) {
            items.push_back(std::move(*item));
        }
        return items;
    }

    [[nodiscard]] bool empty() const noexcept {
        return read_idx_.load(std::memory_order_acquire) >=
               write_idx_.load(std::memory_order_acquire);
    }

    [[nodiscard]] std::size_t capacity() const noexcept { return capacity_; }
    [[nodiscard]] std::size_t size() const noexcept {
        const auto w = write_idx_.load(std::memory_order_acquire);
        const auto r = read_idx_.load(std::memory_order_acquire);
        return w - r;
    }

private:
    const std::size_t capacity_;
    const std::size_t mask_;
    std::vector<T> buffer_;

    // Separate cache lines to avoid false sharing between producer and consumer.
    alignas(64) std::atomic<std::size_t> write_idx_{0};
    alignas(64) std::atomic<std::size_t> read_idx_{0};
};

enum class EnqueueResult : std::uint8_t {
    kSuccess = 0,
    kFull,
    kClosed,
};

// Bounded lock-free multi-producer/single-consumer queue. Each producer
// reserves a distinct sequence slot before moving the payload, so a failed
// enqueue leaves the caller's item intact and per-producer FIFO is preserved.
template <typename T>
class MpscQueue {
    static_assert(
        std::is_nothrow_move_constructible_v<T>,
        "MpscQueue payloads must be nothrow move constructible");

    static constexpr std::size_t kDefaultCapacity = 1024;
    static constexpr std::size_t kClosedBit =
        std::size_t{1} << (std::numeric_limits<std::size_t>::digits - 1);
    static constexpr std::size_t kPositionMask = kClosedBit - 1;

    static constexpr std::size_t next_power_of_two(std::size_t n) noexcept {
        if (n <= 1) return 1;
        --n;
        n |= n >> 1;
        n |= n >> 2;
        n |= n >> 4;
        n |= n >> 8;
        n |= n >> 16;
        if constexpr (sizeof(std::size_t) > 4) {
            n |= n >> 32;
        }
        return n + 1;
    }

    struct alignas(64) Cell {
        std::atomic<std::size_t> sequence{0};
        std::optional<T> item;
    };

public:
    explicit MpscQueue(std::size_t capacity = kDefaultCapacity)
        : capacity_(next_power_of_two(capacity))
        , mask_(capacity_ - 1)
        , buffer_(std::make_unique<Cell[]>(capacity_)) {
        for (std::size_t i = 0; i < capacity_; ++i) {
            buffer_[i].sequence.store(i, std::memory_order_relaxed);
        }
    }

    MpscQueue(const MpscQueue&) = delete;
    MpscQueue& operator=(const MpscQueue&) = delete;

    EnqueueResult try_enqueue_result(T&& item) {
        auto state = enqueue_state_.load(std::memory_order_relaxed);
        for (;;) {
            if ((state & kClosedBit) != 0) {
                return EnqueueResult::kClosed;
            }
            const auto position = state & kPositionMask;
            auto& cell = buffer_[position & mask_];
            const auto sequence = cell.sequence.load(std::memory_order_acquire);
            const auto difference = static_cast<std::intptr_t>(sequence) -
                                    static_cast<std::intptr_t>(position);
            if (difference == 0) {
                if (enqueue_state_.compare_exchange_weak(
                        state, state + 1,
                        std::memory_order_relaxed,
                        std::memory_order_relaxed)) {
                    cell.item.emplace(std::move(item));
                    cell.sequence.store(position + 1, std::memory_order_release);
                    return EnqueueResult::kSuccess;
                }
            } else if (difference < 0) {
                return (enqueue_state_.load(std::memory_order_acquire) & kClosedBit) != 0
                    ? EnqueueResult::kClosed
                    : EnqueueResult::kFull;
            } else {
                state = enqueue_state_.load(std::memory_order_relaxed);
            }
        }
    }

    bool try_enqueue(T&& item) {
        return try_enqueue_result(std::move(item)) == EnqueueResult::kSuccess;
    }

    template <typename U = T>
    bool try_enqueue(const T& item)
        requires std::is_copy_constructible_v<U> {
        T copy(item);
        return try_enqueue(std::move(copy));
    }

    std::optional<T> try_dequeue() {
        const auto position = dequeue_pos_.load(std::memory_order_relaxed);
        auto& cell = buffer_[position & mask_];
        const auto sequence = cell.sequence.load(std::memory_order_acquire);
        const auto difference = static_cast<std::intptr_t>(sequence) -
                                static_cast<std::intptr_t>(position + 1);
        if (difference != 0) {
            return std::nullopt;
        }

        std::optional<T> item(std::move(cell.item));
        cell.item.reset();
        dequeue_pos_.store(position + 1, std::memory_order_release);
        cell.sequence.store(position + capacity_, std::memory_order_release);
        return item;
    }

    std::vector<T> drain() {
        std::vector<T> items;
        items.reserve(size());
        while (auto item = try_dequeue()) {
            items.push_back(std::move(*item));
        }
        return items;
    }

    void close() noexcept {
        const auto previous = enqueue_state_.fetch_or(kClosedBit, std::memory_order_acq_rel);
        const auto reserved_end = previous & kPositionMask;

        // Reservations linearized before the closed bit may still be moving
        // their payload. Wait until each such slot is published (or consumed)
        // so a drain immediately after close cannot miss an accepted item.
        auto position = dequeue_pos_.load(std::memory_order_acquire);
        while (position != reserved_end) {
            auto& cell = buffer_[position & mask_];
            if (cell.sequence.load(std::memory_order_acquire) == position) {
                std::this_thread::yield();
                continue;
            }
            ++position;
        }
    }

    [[nodiscard]] bool closed() const noexcept {
        return (enqueue_state_.load(std::memory_order_acquire) & kClosedBit) != 0;
    }

    [[nodiscard]] bool empty() const noexcept { return size() == 0; }
    [[nodiscard]] std::size_t capacity() const noexcept { return capacity_; }
    [[nodiscard]] std::size_t size() const noexcept {
        const auto write = enqueue_state_.load(std::memory_order_acquire) & kPositionMask;
        const auto read = dequeue_pos_.load(std::memory_order_acquire);
        return write - read;
    }

private:
    const std::size_t capacity_;
    const std::size_t mask_;
    std::unique_ptr<Cell[]> buffer_;

    alignas(64) std::atomic<std::size_t> enqueue_state_{0};
    alignas(64) std::atomic<std::size_t> dequeue_pos_{0};
};

}  // namespace v2::io
