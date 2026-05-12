#pragma once

#include <cstddef>

namespace v2::memory {

class BumpArena {
public:
    explicit BumpArena(std::size_t capacity_bytes = 64 * 1024 * 1024);
    ~BumpArena();

    BumpArena(const BumpArena&) = delete;
    BumpArena& operator=(const BumpArena&) = delete;
    BumpArena(BumpArena&&) = delete;
    BumpArena& operator=(BumpArena&&) = delete;

    [[nodiscard]] void* alloc(std::size_t n) noexcept;
    void reset() noexcept;

    [[nodiscard]] std::size_t remaining() const noexcept;
    [[nodiscard]] std::size_t allocated() const noexcept;
    [[nodiscard]] std::size_t capacity() const noexcept;
    [[nodiscard]] std::size_t exhausted_count() const noexcept { return exhausted_count_; }

private:
    unsigned char* base_ = nullptr;
    unsigned char* current_ = nullptr;
    unsigned char* end_ = nullptr;
    bool owns_buffer_ = true;
    std::size_t exhausted_count_ = 0;
};

}  // namespace v2::memory
