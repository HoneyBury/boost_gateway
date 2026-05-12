#pragma once
// v3.0.0 D3: Consistent Hash Ring for sharded data distribution.
// Used to distribute rooms and battles across backend nodes.
// Virtual nodes ensure even distribution and minimal migration on scale.

#include <algorithm>
#include <cstdint>
#include <functional>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace v3::cluster {

// ── FNV-1a hash (fast, good distribution) ──────────────────────────────

inline std::uint32_t fnv1a_hash(const std::string& key) {
    constexpr std::uint32_t kFnvPrime = 16777619u;
    constexpr std::uint32_t kFnvOffset = 2166136261u;
    std::uint32_t hash = kFnvOffset;
    for (char c : key) {
        hash ^= static_cast<std::uint32_t>(static_cast<unsigned char>(c));
        hash *= kFnvPrime;
    }
    return hash;
}

// ── Consistent Hash Ring ───────────────────────────────────────────────

class ConsistentHashRing {
public:
    struct Config {
        std::uint32_t virtual_nodes = 150;  // virtual nodes per physical node
    };

    explicit ConsistentHashRing(Config config = {}) : config_(config) {}

    /// Add a physical node to the ring.
    void add_node(const std::string& node_name) {
        std::lock_guard lock(mutex_);
        for (std::uint32_t i = 0; i < config_.virtual_nodes; ++i) {
            auto vnode_key = node_name + "#" + std::to_string(i);
            auto hash = fnv1a_hash(vnode_key);
            ring_[hash] = node_name;
        }
        physical_nodes_.push_back(node_name);
    }

    /// Remove a physical node from the ring.
    void remove_node(const std::string& node_name) {
        std::lock_guard lock(mutex_);
        for (std::uint32_t i = 0; i < config_.virtual_nodes; ++i) {
            auto vnode_key = node_name + "#" + std::to_string(i);
            auto hash = fnv1a_hash(vnode_key);
            ring_.erase(hash);
        }
        physical_nodes_.erase(
            std::remove(physical_nodes_.begin(), physical_nodes_.end(), node_name),
            physical_nodes_.end());
    }

    /// Look up which node owns a key.
    [[nodiscard]] std::string lookup(const std::string& key) const {
        std::lock_guard lock(mutex_);
        if (ring_.empty()) return {};

        auto hash = fnv1a_hash(key);
        auto it = ring_.lower_bound(hash);
        if (it == ring_.end()) {
            return ring_.begin()->second;  // wrap around
        }
        return it->second;
    }

    /// Get N successor nodes for a key (for replication).
    [[nodiscard]] std::vector<std::string> lookup_n(const std::string& key,
                                                      std::size_t n) const {
        std::lock_guard lock(mutex_);
        std::vector<std::string> result;
        if (ring_.empty() || n == 0) return result;

        auto hash = fnv1a_hash(key);
        auto it = ring_.lower_bound(hash);

        std::vector<std::string> seen;
        for (std::size_t i = 0; i < ring_.size() && result.size() < n; ++i) {
            if (it == ring_.end()) it = ring_.begin();
            const auto& node = it->second;
            if (std::find(seen.begin(), seen.end(), node) == seen.end()) {
                result.push_back(node);
                seen.push_back(node);
            }
            ++it;
        }
        return result;
    }

    /// Get all physical nodes.
    [[nodiscard]] std::vector<std::string> nodes() const {
        std::lock_guard lock(mutex_);
        return physical_nodes_;
    }

    /// Number of virtual nodes in the ring.
    [[nodiscard]] std::size_t size() const {
        std::lock_guard lock(mutex_);
        return ring_.size();
    }

    /// Estimated fraction of keys that would be remapped if a node is removed.
    /// With 150 virtual nodes, this should be approximately 1/N.
    [[nodiscard]] double remap_fraction() const {
        std::lock_guard lock(mutex_);
        auto n = physical_nodes_.size();
        if (n <= 1) return 1.0;
        return 1.0 / static_cast<double>(n);
    }

private:
    Config config_;
    mutable std::mutex mutex_;
    std::map<std::uint32_t, std::string> ring_;     // hash → node_name
    std::vector<std::string> physical_nodes_;
};

// ── Shard Router ────────────────────────────────────────────────────────

/// Routes room_id and battle_id to backend nodes using consistent hashing.
class ShardRouter {
public:
    ShardRouter()
        : room_ring_(ConsistentHashRing::Config{.virtual_nodes = 150}),
          battle_ring_(ConsistentHashRing::Config{.virtual_nodes = 100}) {}

    /// Add a backend node to both rings.
    void add_backend(const std::string& node_name) {
        room_ring_.add_node(node_name);
        battle_ring_.add_node(node_name);
    }

    void remove_backend(const std::string& node_name) {
        room_ring_.remove_node(node_name);
        battle_ring_.remove_node(node_name);
    }

    /// Route a room to its owning backend.
    [[nodiscard]] std::string route_room(const std::string& room_id) const {
        return room_ring_.lookup(room_id);
    }

    /// Route a battle to its owning backend.
    [[nodiscard]] std::string route_battle(const std::string& battle_id) const {
        return battle_ring_.lookup(battle_id);
    }

    /// Get the room ring (for diagnostics).
    [[nodiscard]] const ConsistentHashRing& room_ring() const { return room_ring_; }
    [[nodiscard]] const ConsistentHashRing& battle_ring() const { return battle_ring_; }

private:
    ConsistentHashRing room_ring_;
    ConsistentHashRing battle_ring_;
};

}  // namespace v3::cluster
