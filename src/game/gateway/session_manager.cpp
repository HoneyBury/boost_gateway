#include "game/gateway/session_manager.h"

#include <utility>

namespace game::gateway {

std::uint64_t SessionManager::add_session(const SessionPtr& session) {
    std::scoped_lock lock(mutex_);

    const auto key = session.get();
    const auto session_id = next_session_id_++;
    sessions_[key] = SessionRecord{
        .session_id = session_id,
        .session = session,
    };
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

    Snapshot snapshot;
    snapshot.active_sessions = sessions_.size();

    for (const auto& [_, record] : sessions_) {
        if (record.authenticated) {
            ++snapshot.authenticated_sessions;
        }
    }

    return snapshot;
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
    return replaced_session;
}

}  // namespace game::gateway
