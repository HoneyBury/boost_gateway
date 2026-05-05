#pragma once

#include "net/session.h"

#include <cstddef>
#include <cstdint>
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

    std::uint64_t add_session(const SessionPtr& session);
    void remove_session(const SessionPtr& session);

    [[nodiscard]] std::vector<SessionPtr> all_sessions() const;
    [[nodiscard]] bool is_authenticated(const SessionPtr& session) const;
    [[nodiscard]] std::optional<std::string> user_id_of(const SessionPtr& session) const;
    [[nodiscard]] std::optional<LoginContext> login_context_of(const SessionPtr& session) const;
    [[nodiscard]] Snapshot snapshot() const;

    SessionPtr authenticate(const SessionPtr& session, LoginContext context);

private:
    using SessionKey = const net::Session*;

    struct SessionRecord {
        std::uint64_t session_id = 0;
        SessionPtr session;
        LoginContext login_context;
        bool authenticated = false;
    };

    mutable std::mutex mutex_;
    std::uint64_t next_session_id_ = 1;
    std::unordered_map<SessionKey, SessionRecord> sessions_;
    std::unordered_map<std::string, SessionKey> user_index_;
};

}  // namespace game::gateway
