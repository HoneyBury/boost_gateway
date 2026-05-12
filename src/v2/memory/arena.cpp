#include "v2/memory/arena.h"

#include <cstdint>

namespace v2::memory {

BumpArena::BumpArena(std::size_t capacity_bytes)
    : base_(new unsigned char[capacity_bytes])
    , current_(base_)
    , end_(base_ + capacity_bytes)
    , owns_buffer_(true) {
}

BumpArena::~BumpArena() {
    if (owns_buffer_) {
        delete[] base_;
    }
}

void* BumpArena::alloc(std::size_t n) noexcept {
    constexpr auto alignment = alignof(std::max_align_t);

    // Align the bump pointer up to the alignment boundary
    auto const raw = reinterpret_cast<std::uintptr_t>(current_);
    auto const aligned = (raw + alignment - 1) & ~(alignment - 1);

    // Round allocation size up to alignment
    auto const aligned_n = (n + alignment - 1) & ~(alignment - 1);

    auto const next = reinterpret_cast<unsigned char*>(aligned + aligned_n);
    if (next > end_) {
        ++exhausted_count_;
        return nullptr;
    }

    current_ = next;
    return reinterpret_cast<void*>(aligned);
}

void BumpArena::reset() noexcept {
    current_ = base_;
}

std::size_t BumpArena::remaining() const noexcept {
    return static_cast<std::size_t>(end_ - current_);
}

std::size_t BumpArena::allocated() const noexcept {
    return static_cast<std::size_t>(current_ - base_);
}

std::size_t BumpArena::capacity() const noexcept {
    return static_cast<std::size_t>(end_ - base_);
}

}  // namespace v2::memory
