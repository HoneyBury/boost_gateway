#pragma once
// v3.0.0 D2: Remote Actor Transport — cross-node transparent actor messaging.
// Extends v2 ActorRef with node location awareness for distributed routing.

#include "v2/actor/actor.h"
#include "v2/actor/actor_ref.h"
#include "v3/cluster/cluster_router.h"

#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>

namespace v3::cluster {

// ── Node-aware ActorRef ────────────────────────────────────────────────

class RemoteActorRef {
public:
    RemoteActorRef() = default;
    RemoteActorRef(v2::actor::ActorRef local_ref, NodeId node)
        : local_ref_(local_ref), node_(std::move(node)), is_local_(true) {}

    /// Create a remote-only reference (actor lives on another node).
    static RemoteActorRef remote(v2::actor::ActorId actor_id, NodeId node) {
        RemoteActorRef ref;
        ref.remote_actor_id_ = actor_id;
        ref.node_ = std::move(node);
        ref.is_local_ = false;
        return ref;
    }

    [[nodiscard]] bool is_local() const noexcept { return is_local_; }
    [[nodiscard]] const NodeId& node() const noexcept { return node_; }
    [[nodiscard]] v2::actor::ActorId remote_id() const noexcept { return remote_actor_id_; }

    /// Send a message to this actor (local or remote transparently).
    void tell(v2::actor::Message msg) const;

    /// Get the local ActorRef (only valid if is_local()).
    [[nodiscard]] std::optional<v2::actor::ActorRef> local_ref() const {
        if (is_local_) return local_ref_;
        return std::nullopt;
    }

private:
    v2::actor::ActorRef local_ref_;
    v2::actor::ActorId remote_actor_id_ = 0;  // used when !is_local
    NodeId node_;
    bool is_local_ = false;
};

// ── Actor location registry ────────────────────────────────────────────

class ActorLocationRegistry {
public:
    /// Register an actor's location (called when actor is created).
    void register_actor(v2::actor::ActorId actor_id, const NodeId& node) {
        std::lock_guard lock(mutex_);
        locations_[actor_id] = node;
    }

    /// Unregister an actor (called when actor is destroyed).
    void unregister_actor(v2::actor::ActorId actor_id) {
        std::lock_guard lock(mutex_);
        locations_.erase(actor_id);
    }

    /// Look up which node hosts an actor.
    [[nodiscard]] std::optional<NodeId> locate(v2::actor::ActorId actor_id) const {
        std::lock_guard lock(mutex_);
        auto it = locations_.find(actor_id);
        if (it != locations_.end()) return it->second;
        return std::nullopt;
    }

    /// Move an actor to a new node (after migration).
    void relocate(v2::actor::ActorId actor_id, const NodeId& new_node) {
        std::lock_guard lock(mutex_);
        locations_[actor_id] = new_node;
    }

    [[nodiscard]] std::size_t actor_count() const {
        std::lock_guard lock(mutex_);
        return locations_.size();
    }

    [[nodiscard]] std::size_t actors_on_node(const NodeId& node) const {
        std::lock_guard lock(mutex_);
        std::size_t count = 0;
        for (const auto& [id, n] : locations_) {
            if (n.node_name == node.node_name) ++count;
        }
        return count;
    }

private:
    mutable std::mutex mutex_;
    std::unordered_map<v2::actor::ActorId, NodeId> locations_;
};

// ── Remote transport ───────────────────────────────────────────────────

class RemoteActorTransport {
public:
    using MessageSender = std::function<bool(const NodeId& target,
                                              const std::string& serialized_msg)>;

    explicit RemoteActorTransport(std::shared_ptr<ActorLocationRegistry> registry,
                                   std::shared_ptr<ClusterRouter> router)
        : registry_(std::move(registry)),
          router_(std::move(router)) {}

    /// Route a message to an actor (local if possible, remote if needed).
    bool route(v2::actor::ActorId target_actor_id, v2::actor::Message msg);

    /// Set the function that actually sends bytes to a remote node.
    void set_sender(MessageSender sender) { sender_ = std::move(sender); }

    /// Serialize a message for network transport.
    [[nodiscard]] static std::string serialize(const v2::actor::Message& msg);

    /// Deserialize a message from network transport.
    [[nodiscard]] static std::optional<v2::actor::Message> deserialize(
        const std::string& data);

private:
    std::shared_ptr<ActorLocationRegistry> registry_;
    std::shared_ptr<ClusterRouter> router_;
    MessageSender sender_;
};

// ── Implementation ──────────────────────────────────────────────────────

inline bool RemoteActorTransport::route(
    v2::actor::ActorId target_actor_id, v2::actor::Message msg) {
    auto location = registry_->locate(target_actor_id);
    if (!location.has_value()) return false;

    // Check if target is on this node (local delivery)
    // For now, assume non-local; local delivery handled by ActorSystem directly
    if (!sender_) return false;

    auto serialized = serialize(msg);
    return sender_(*location, serialized);
}

inline std::string RemoteActorTransport::serialize(
    const v2::actor::Message& msg) {
    // Simple binary format: kind(2) + trace_id(8) + request_id(4) + source(8) + target(8)
    // + created_at(8) + payload_length(4) + payload_bytes(N)
    // Full protobuf serialization planned for gRPC integration (Phase 12 D6)
    std::string result;
    auto append_u16 = [&](std::uint16_t v) {
        result.push_back(static_cast<char>(v >> 8));
        result.push_back(static_cast<char>(v & 0xFF));
    };
    auto append_u32 = [&](std::uint32_t v) {
        result.push_back(static_cast<char>(v >> 24));
        result.push_back(static_cast<char>((v >> 16) & 0xFF));
        result.push_back(static_cast<char>((v >> 8) & 0xFF));
        result.push_back(static_cast<char>(v & 0xFF));
    };
    auto append_u64 = [&](std::uint64_t v) {
        for (int i = 7; i >= 0; --i)
            result.push_back(static_cast<char>((v >> (i * 8)) & 0xFF));
    };

    append_u16(static_cast<std::uint16_t>(msg.header.kind));
    append_u64(msg.header.trace_id);
    append_u32(msg.header.request_id);
    append_u64(msg.header.source_actor);
    append_u64(msg.header.target_actor);
    append_u64(msg.header.created_at);

    // Payload: for now, use string representation (v2 bootstrap format)
    std::string payload = "v3_actor_msg";  // placeholder for protobuf
    append_u32(static_cast<std::uint32_t>(payload.size()));
    result += payload;

    return result;
}

inline std::optional<v2::actor::Message> RemoteActorTransport::deserialize(
    const std::string& data) {
    if (data.size() < 38) return std::nullopt;  // minimum header size

    auto read_u16 = [&](std::size_t& pos) -> std::uint16_t {
        auto v = (static_cast<std::uint16_t>(static_cast<unsigned char>(data[pos])) << 8) |
                 static_cast<std::uint16_t>(static_cast<unsigned char>(data[pos + 1]));
        pos += 2; return v;
    };
    auto read_u32 = [&](std::size_t& pos) -> std::uint32_t {
        std::uint32_t v = 0;
        for (int i = 0; i < 4; ++i)
            v = (v << 8) | static_cast<unsigned char>(data[pos + i]);
        pos += 4; return v;
    };
    auto read_u64 = [&](std::size_t& pos) -> std::uint64_t {
        std::uint64_t v = 0;
        for (int i = 0; i < 8; ++i)
            v = (v << 8) | static_cast<unsigned char>(data[pos + i]);
        pos += 8; return v;
    };

    std::size_t pos = 0;
    v2::actor::Message msg;
    msg.header.kind = static_cast<v2::actor::MessageKind>(read_u16(pos));
    msg.header.trace_id = read_u64(pos);
    msg.header.request_id = read_u32(pos);
    msg.header.source_actor = read_u64(pos);
    msg.header.target_actor = read_u64(pos);
    msg.header.created_at = read_u64(pos);

    auto payload_len = read_u32(pos);
    // msg.payload = ... (would be deserialized from protobuf)

    return msg;
}

}  // namespace v3::cluster
