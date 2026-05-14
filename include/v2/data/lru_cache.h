#pragma once

#include <cstddef>
#include <list>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <unordered_map>
#include <utility>
#include <vector>

namespace v2::data {

template <typename K, typename V>
class LruCache {
public:
    explicit LruCache(std::size_t max_size = 10000)
        : max_size_(max_size) {}

    [[nodiscard]] std::shared_ptr<const V> get(const K& key) const {
        std::shared_lock lock(mutex_);
        auto it = map_.find(key);
        if (it == map_.end()) {
            return nullptr;
        }
        // Move to front (most recently used)
        list_.splice(list_.begin(), list_, it->second);
        return std::make_shared<const V>(it->second->second);
    }

    /// Put a key-value pair. Returns the evicted entry if any.
    std::optional<std::pair<K, V>> put(const K& key, const V& value) {
        std::unique_lock lock(mutex_);
        std::optional<std::pair<K, V>> evicted;
        auto it = map_.find(key);
        if (it != map_.end()) {
            // Key exists: update value and move to front
            it->second->second = value;
            list_.splice(list_.begin(), list_, it->second);
            return evicted;
        }
        // Evict if at capacity
        if (map_.size() >= max_size_) {
            auto lru = list_.back();
            evicted = std::move(lru);
            map_.erase(evicted->first);
            list_.pop_back();
        }
        // Insert at front
        list_.emplace_front(key, value);
        map_[key] = list_.begin();
        return evicted;
    }

    void remove(const K& key) {
        std::unique_lock lock(mutex_);
        auto it = map_.find(key);
        if (it != map_.end()) {
            list_.erase(it->second);
            map_.erase(it);
        }
    }

    void clear() {
        std::unique_lock lock(mutex_);
        list_.clear();
        map_.clear();
    }

    [[nodiscard]] std::size_t size() const {
        std::shared_lock lock(mutex_);
        return map_.size();
    }

    [[nodiscard]] std::size_t max_size() const {
        std::shared_lock lock(mutex_);
        return max_size_;
    }

    [[nodiscard]] bool contains(const K& key) const {
        std::shared_lock lock(mutex_);
        auto it = map_.find(key);
        if (it == map_.end()) {
            return false;
        }
        // Move to front on access (LRU ordering)
        list_.splice(list_.begin(), list_, it->second);
        return true;
    }

    /// Iterate over all entries (read-only).
    template <typename F>
    void for_each(F&& func) const {
        std::shared_lock lock(mutex_);
        for (const auto& [key, value] : list_) {
            func(key, value);
        }
    }

    /// Drain all entries into a vector and clear the cache.
    [[nodiscard]] std::vector<std::pair<K, V>> drain() {
        std::unique_lock lock(mutex_);
        std::vector<std::pair<K, V>> result;
        result.reserve(list_.size());
        for (auto& [key, value] : list_) {
            result.emplace_back(std::move(key), std::move(value));
        }
        list_.clear();
        map_.clear();
        return result;
    }

private:
    using ListType = std::list<std::pair<K, V>>;
    using ListIterator = typename ListType::iterator;

    std::size_t max_size_;
    mutable std::shared_mutex mutex_;
    mutable ListType list_;
    std::unordered_map<K, ListIterator> map_;
};

}  // namespace v2::data
