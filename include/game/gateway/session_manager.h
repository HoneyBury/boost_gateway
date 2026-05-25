#pragma once

#include "net/session.h"

#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace game::gateway {

class SessionManager {
public:
    using SessionPtr = std::shared_ptr<net::Session>;

    struct LoginContext {
        std::string user_id;
        std::string display_name;
    };

    struct Snapshot {
        std::size_t active_sessions = 0;
        std::size_t authenticated_sessions = 0;
    };

    // ── Lifecycle ─────────────────────────────────────────────────────

    std::uint64_t add_session(const SessionPtr& session);
    void remove_session(const SessionPtr& session);

    [[nodiscard]] std::vector<SessionPtr> all_sessions() const;
    [[nodiscard]] bool contains(const SessionPtr& session) const;
    [[nodiscard]] bool is_authenticated(const SessionPtr& session) const;
    [[nodiscard]] std::optional<std::string> user_id_of(const SessionPtr& session) const;
    [[nodiscard]] std::optional<LoginContext> login_context_of(const SessionPtr& session) const;
    [[nodiscard]] Snapshot snapshot() const;

    SessionPtr authenticate(const SessionPtr& session, LoginContext context);

    // ── Lock-free broadcast ───────────────────────────────────────────

    // Broadcast a packet to all authenticated sessions using an RCU-style
    // shared_ptr snapshot. Does NOT block concurrent add_session /
    // remove_session calls.
    void broadcast(std::uint16_t message_id,
                   std::uint32_t request_id,
                   std::int32_t error_code,
                   const std::string& body,
                   std::uint8_t flags = 0,
                   bool high_priority = false);

    // ── Backpressure ──────────────────────────────────────────────────

    // Overload callback invoked when a session's pending write count
    // exceeds max_pending_per_session_.
    using OverloadHandler =
        std::function<void(const SessionPtr& session, std::size_t pending_count)>;

    void set_overload_handler(OverloadHandler handler) {
        overload_handler_ = std::move(handler);
    }

    // Set the maximum number of pending writes per session before overload
    // triggers (default: 1024).
    void set_max_pending_per_session(std::size_t max_pending) noexcept {
        max_pending_per_session_ = max_pending;
    }

    [[nodiscard]] std::size_t max_pending_per_session() const noexcept {
        return max_pending_per_session_;
    }

private:
    using SessionKey = const net::Session*;

    struct SessionRecord {
        std::uint64_t session_id = 0;
        SessionPtr session;
        LoginContext login_context;
        bool authenticated = false;
    };

    // ── Internal helpers ──────────────────────────────────────────────

    // Atomically publish a new snapshot after the sessions_ map has been
    // mutated (must be called with mutex_ held).
    void publish_snapshot();

    // Handle an overloaded session (rate-limited warn + callback).
    void handle_overload(const SessionPtr& session);

    // ── Mutex-protected state ─────────────────────────────────────────

    mutable std::mutex mutex_;
    std::uint64_t next_session_id_ = 1;
    std::unordered_map<SessionKey, SessionRecord> sessions_;
    std::unordered_map<std::string, SessionKey> user_index_;

    // ── RCU snapshot (lock-free readers) ──────────────────────────────

    using SessionSnapshot =
        std::shared_ptr<const std::unordered_map<SessionKey, SessionRecord>>;

    SessionSnapshot snapshot_{
        std::make_shared<const std::unordered_map<SessionKey, SessionRecord>>()};

    // ── Backpressure ──────────────────────────────────────────────────

    static constexpr std::size_t kDefaultMaxPending = 1024;

    OverloadHandler overload_handler_;
    std::size_t max_pending_per_session_ = kDefaultMaxPending;

    // Rate-limited overload warning state (accessed under mutex_).
    std::chrono::steady_clock::time_point last_overload_warn_time_;
    std::size_t overload_warn_count_ = 0;
};

}  // namespace game::gateway
