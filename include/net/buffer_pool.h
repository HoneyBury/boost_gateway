#pragma once

#include <array>
#include <cstddef>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace net {

template <typename T, std::size_t PoolSize = 64>
class ObjectPool {
public:
    ObjectPool() {
        for (auto& slot : pool_) {
            slot = std::make_unique<T>();
        }
    }

    [[nodiscard]] std::unique_ptr<T> acquire() {
        std::lock_guard lock(mutex_);
        for (auto& slot : pool_) {
            if (slot) {
                auto obj = std::move(slot);
                slot = std::make_unique<T>();
                return obj;
            }
        }
        return std::make_unique<T>();
    }

    void release(std::unique_ptr<T> obj) {
        if (!obj) return;
        std::lock_guard lock(mutex_);
        for (auto& slot : pool_) {
            if (!slot) {
                slot = std::move(obj);
                return;
            }
        }
    }

private:
    std::array<std::unique_ptr<T>, PoolSize> pool_;
    std::mutex mutex_;
};

class BufferPool {
public:
    [[nodiscard]] std::string acquire_string() {
        std::lock_guard lock(mutex_);
        if (!string_pool_.empty()) {
            auto s = std::move(string_pool_.back());
            string_pool_.pop_back();
            s.clear();
            return s;
        }
        return {};
    }

    void release_string(std::string s) {
        std::lock_guard lock(mutex_);
        if (string_pool_.size() < kMaxStrings) {
            s.clear();
            string_pool_.push_back(std::move(s));
        }
    }

    [[nodiscard]] std::vector<char> acquire_vector(std::size_t capacity_hint = 0) {
        std::lock_guard lock(mutex_);
        if (!vector_pool_.empty()) {
            auto v = std::move(vector_pool_.back());
            vector_pool_.pop_back();
            v.clear();
            if (capacity_hint > 0) {
                v.reserve(capacity_hint);
            }
            return v;
        }
        std::vector<char> v;
        if (capacity_hint > 0) {
            v.reserve(capacity_hint);
        }
        return v;
    }

    void release_vector(std::vector<char> v) {
        std::lock_guard lock(mutex_);
        if (vector_pool_.size() < kMaxVectors) {
            v.clear();
            vector_pool_.push_back(std::move(v));
        }
    }

    static BufferPool& instance() {
        static BufferPool pool;
        return pool;
    }

private:
    static constexpr std::size_t kMaxStrings = 128;
    static constexpr std::size_t kMaxVectors = 64;
    std::vector<std::string> string_pool_;
    std::vector<std::vector<char>> vector_pool_;
    std::mutex mutex_;
};

}  // namespace net
