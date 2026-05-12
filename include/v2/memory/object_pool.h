#pragma once

#include <cstddef>
#include <mutex>
#include <new>
#include <vector>

namespace v2::memory {

template <typename T, std::size_t BlockSize = 256>
class ObjectPool {
public:
    explicit ObjectPool(std::size_t max_blocks = 0) : max_blocks_(max_blocks) {}
    ~ObjectPool();

    ObjectPool(const ObjectPool&) = delete;
    ObjectPool& operator=(const ObjectPool&) = delete;
    ObjectPool(ObjectPool&&) = delete;
    ObjectPool& operator=(ObjectPool&&) = delete;

    [[nodiscard]] T* acquire();

    void release(T* ptr) noexcept;

    void prefill(std::size_t n);

    [[nodiscard]] std::size_t available() const noexcept;

    [[nodiscard]] std::size_t total_allocated() const noexcept;

    [[nodiscard]] std::size_t exhausted_count() const noexcept { return exhausted_count_; }

    [[nodiscard]] std::size_t max_blocks() const noexcept { return max_blocks_; }

private:
    struct Node {
        Node* next;
    };

    static_assert(sizeof(T) >= sizeof(Node),
                  "ObjectPool requires T to be at least pointer-sized");

    void allocate_block();

    Node* free_list_ = nullptr;
    std::size_t available_ = 0;
    std::size_t total_allocated_ = 0;
    std::size_t max_blocks_ = 0;
    std::size_t exhausted_count_ = 0;
    std::vector<T*> blocks_;
    mutable std::mutex mutex_;
};

// -----------------------------------------------------------------------
// Implementation
// -----------------------------------------------------------------------

template <typename T, std::size_t BlockSize>
ObjectPool<T, BlockSize>::~ObjectPool() {
    for (auto* block : blocks_) {
        ::operator delete(block);
    }
}

template <typename T, std::size_t BlockSize>
T* ObjectPool<T, BlockSize>::acquire() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!free_list_) {
        if (max_blocks_ > 0 && blocks_.size() >= max_blocks_) {
            ++exhausted_count_;
            return nullptr;
        }
        allocate_block();
    }
    auto* node = free_list_;
    free_list_ = node->next;
    --available_;
    T* ptr = reinterpret_cast<T*>(node);
    return ::new (ptr) T();
}

template <typename T, std::size_t BlockSize>
void ObjectPool<T, BlockSize>::release(T* ptr) noexcept {
    if (!ptr) {
        return;
    }
    ptr->~T();
    std::lock_guard<std::mutex> lock(mutex_);
    auto* node = reinterpret_cast<Node*>(ptr);
    node->next = free_list_;
    free_list_ = node;
    ++available_;
}

template <typename T, std::size_t BlockSize>
void ObjectPool<T, BlockSize>::prefill(std::size_t n) {
    std::lock_guard<std::mutex> lock(mutex_);
    while (available_ < n) {
        allocate_block();
    }
}

template <typename T, std::size_t BlockSize>
std::size_t ObjectPool<T, BlockSize>::available() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return available_;
}

template <typename T, std::size_t BlockSize>
std::size_t ObjectPool<T, BlockSize>::total_allocated() const noexcept {
    std::lock_guard<std::mutex> lock(mutex_);
    return total_allocated_;
}

template <typename T, std::size_t BlockSize>
void ObjectPool<T, BlockSize>::allocate_block() {
    // Caller must hold mutex_.
    auto* block = static_cast<T*>(::operator new(sizeof(T) * BlockSize));
    blocks_.push_back(block);
    for (std::size_t i = 0; i < BlockSize; ++i) {
        auto* obj = &block[i];
        auto* node = reinterpret_cast<Node*>(obj);
        node->next = free_list_;
        free_list_ = node;
    }
    available_ += BlockSize;
    total_allocated_ += BlockSize;
}

}  // namespace v2::memory
