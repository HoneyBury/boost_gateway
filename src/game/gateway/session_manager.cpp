#include "game/gateway/session_manager.h"

#include <utility>

#include <spdlog/spdlog.h>

namespace game::gateway {

// ── Helpers ────────────────────────────────────────────────────────────

void SessionManager::publish_snapshot() {
    // mutex_ must be held by the caller.
    auto new_snap = std::make_shared<const std::unordered_map<SessionKey, SessionRecord>>(sessions_);
    std::atomic_store_explicit(&snapshot_, std::move(new_snap), std::memory_order_release);
}

void SessionManager::handle_overload(const SessionPtr& session) {
    auto now = std::chrono::steady_clock::now();
    auto elapsed = std::chrono::duration_cast<std::chrono::seconds>(
                       now - last_overload_warn_time_)
                       .count();

    // Rate-limit warning to once every 10 seconds.
    if (elapsed >= 10) {
        SPDLOG_WARN("[SessionManager] Session overload: pending={} remote={}",
                    session->pending_write_count(),
                    session->remote_endpoint());
        last_overload_warn_time_ = now;
        ++overload_warn_count_;
    }

    if (overload_handler_) {
        overload_handler_(session, session->pending_write_count());
    }
}

// ── Lifecycle ──────────────────────────────────────────────────────────

std::uint64_t SessionManager::add_session(const SessionPtr& session) {
    std::scoped_lock lock(mutex_);

    const auto key = session.get();
    const auto session_id = next_session_id_++;
    sessions_[key] = SessionRecord{
        .session_id = session_id,
        .session = session,
    };

    publish_snapshot();
    return session_id;
}

void SessionManager::remove_session(const SessionPtr& session) {
    std::scoped_lock lock(mutex_);

    const auto key = session.get();
    const auto it = sessions_.find(key);
    if (it == sessions_.end()) {
        return;
    }

    const auto& record = it->second;
    if (!record.login_context.user_id.empty()) {
        const auto user_it = user_index_.find(record.login_context.user_id);
        if (user_it != user_index_.end() && user_it->second == key) {
            user_index_.erase(user_it);
        }
    }

    sessions_.erase(it);
    publish_snapshot();
}

std::vector<SessionManager::SessionPtr> SessionManager::all_sessions() const {
    std::vector<SessionPtr> sessions;
    std::scoped_lock lock(mutex_);
    sessions.reserve(sessions_.size());
    for (const auto& [_, record] : sessions_) {
        sessions.push_back(record.session);
    }
    return sessions;
}

bool SessionManager::contains(const SessionPtr& session) const {
    std::scoped_lock lock(mutex_);
    return sessions_.contains(session.get());
}

bool SessionManager::is_authenticated(const SessionPtr& session) const {
    std::scoped_lock lock(mutex_);
    const auto it = sessions_.find(session.get());
    return it != sessions_.end() && it->second.authenticated;
}

std::optional<std::string> SessionManager::user_id_of(const SessionPtr& session) const {
    std::scoped_lock lock(mutex_);
    const auto it = sessions_.find(session.get());
    if (it == sessions_.end() || !it->second.authenticated) {
        return std::nullopt;
    }
    return it->second.login_context.user_id;
}

std::optional<SessionManager::LoginContext> SessionManager::login_context_of(const SessionPtr& session) const {
    std::scoped_lock lock(mutex_);
    const auto it = sessions_.find(session.get());
    if (it == sessions_.end() || !it->second.authenticated) {
        return std::nullopt;
    }
    return it->second.login_context;
}

SessionManager::Snapshot SessionManager::snapshot() const {
    std::scoped_lock lock(mutex_);

    Snapshot snap;
    snap.active_sessions = sessions_.size();

    for (const auto& [_, record] : sessions_) {
        if (record.authenticated) {
            ++snap.authenticated_sessions;
        }
    }

    return snap;
}

SessionManager::SessionPtr SessionManager::authenticate(const SessionPtr& session, LoginContext context) {
    SessionPtr replaced_session;

    std::scoped_lock lock(mutex_);

    const auto key = session.get();
    const auto it = sessions_.find(key);
    if (it == sessions_.end() || context.user_id.empty()) {
        return nullptr;
    }

    auto& record = it->second;

    if (!record.login_context.user_id.empty()) {
        const auto user_it = user_index_.find(record.login_context.user_id);
        if (user_it != user_index_.end() && user_it->second == key) {
            user_index_.erase(user_it);
        }
    }

    const auto existing_user_it = user_index_.find(context.user_id);
    if (existing_user_it != user_index_.end() && existing_user_it->second != key) {
        const auto old_session_it = sessions_.find(existing_user_it->second);
        if (old_session_it != sessions_.end()) {
            old_session_it->second.authenticated = false;
            old_session_it->second.login_context = {};
            replaced_session = old_session_it->second.session;
        }
    }

    record.authenticated = true;
    record.login_context = std::move(context);
    user_index_[record.login_context.user_id] = key;

    publish_snapshot();
    return replaced_session;
}

// ── Lock-free broadcast ────────────────────────────────────────────────

void SessionManager::broadcast(std::uint16_t message_id,
                                std::uint32_t request_id,
                                std::int32_t error_code,
                                const std::string& body,
                                std::uint8_t flags,
                                bool high_priority) {
    // Grab the current atomically-published snapshot.
    auto snap = std::atomic_load_explicit(&snapshot_, std::memory_order_acquire);
    if (!snap || snap->empty()) {
        return;
    }

    for (const auto& [key, record] : *snap) {
        (void)key;
        if (!record.authenticated || !record.session) {
            continue;
        }

        // Backpressure check: skip overloaded sessions.
        if (record.session->pending_write_count() >= max_pending_per_session_) {
            handle_overload(record.session);
            continue;
        }

        record.session->send(message_id, request_id, error_code,
                             body, flags, high_priority);
    }
}

}  // namespace game::gateway
