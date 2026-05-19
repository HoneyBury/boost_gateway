#pragma once

#include <cstdint>
#include <algorithm>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include <nlohmann/json.hpp>

#include "v2/actor/actor_ref.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/service/service_id.h"

namespace v2::gateway {

struct PendingResponse {
    std::uint64_t session_id = 0;
    std::uint32_t request_id = 0;
};

struct BridgeRouteResult {
    bool handled = false;
};

// ── Session Lookup Helper ─────────────────────────────────────────────
// Centralizes the common session → user → room → player lookup chain
// used by every command handler in Runtime::handle().

class SessionLookup {
public:
    using SessionId = std::uint64_t;

    struct Context {
        std::string user_id;
        std::string room_id;
        v2::actor::ActorRef player_ref;
        v2::actor::ActorRef room_ref;
    };

    void set_session_user(SessionId session_id, const std::string& user_id) {
        users_by_session_id_[session_id] = user_id;
    }

    void set_session_room(SessionId session_id, const std::string& room_id) {
        erase_session_room(session_id);
        rooms_by_session_id_[session_id] = room_id;
        sessions_by_room_id_[room_id].push_back(session_id);
    }

    void set_player(const std::string& user_id, v2::actor::ActorRef ref) {
        players_by_user_id_[user_id] = ref;
    }

    void set_room(const std::string& room_id, v2::actor::ActorRef ref) {
        rooms_by_room_id_[room_id] = ref;
    }

    void erase_session(SessionId session_id) {
        users_by_session_id_.erase(session_id);
        erase_session_room(session_id);
    }

    void erase_session_user(SessionId session_id) {
        users_by_session_id_.erase(session_id);
    }

    void erase_session_room(SessionId session_id) {
        auto room_it = rooms_by_session_id_.find(session_id);
        if (room_it != rooms_by_session_id_.end()) {
            auto sessions_it = sessions_by_room_id_.find(room_it->second);
            if (sessions_it != sessions_by_room_id_.end()) {
                auto& sessions = sessions_it->second;
                sessions.erase(std::remove(sessions.begin(), sessions.end(), session_id),
                               sessions.end());
                if (sessions.empty()) {
                    sessions_by_room_id_.erase(sessions_it);
                }
            }
        }
        rooms_by_session_id_.erase(session_id);
    }

    void erase_user(const std::string& user_id) {
        players_by_user_id_.erase(user_id);
    }

    [[nodiscard]] std::string user_id_for(SessionId session_id) const {
        auto it = users_by_session_id_.find(session_id);
        return it != users_by_session_id_.end() ? it->second : std::string{};
    }

    [[nodiscard]] std::string room_id_for(SessionId session_id) const {
        auto it = rooms_by_session_id_.find(session_id);
        return it != rooms_by_session_id_.end() ? it->second : std::string{};
    }

    [[nodiscard]] std::optional<SessionId> session_for_user(const std::string& user_id) const {
        for (const auto& [sid, uid] : users_by_session_id_) {
            if (uid == user_id) return sid;
        }
        return std::nullopt;
    }

    [[nodiscard]] v2::actor::ActorRef* player(const std::string& user_id) {
        auto it = players_by_user_id_.find(user_id);
        return it != players_by_user_id_.end() ? &it->second : nullptr;
    }

    [[nodiscard]] v2::actor::ActorRef* room(const std::string& room_id) {
        auto it = rooms_by_room_id_.find(room_id);
        return it != rooms_by_room_id_.end() ? &it->second : nullptr;
    }

    [[nodiscard]] std::optional<Context> resolve(SessionId session_id) const {
        auto user_it = users_by_session_id_.find(session_id);
        if (user_it == users_by_session_id_.end()) return std::nullopt;

        Context ctx;
        ctx.user_id = user_it->second;

        auto room_it = rooms_by_session_id_.find(session_id);
        if (room_it != rooms_by_session_id_.end()) ctx.room_id = room_it->second;

        auto player_it = players_by_user_id_.find(ctx.user_id);
        if (player_it != players_by_user_id_.end()) ctx.player_ref = player_it->second;

        auto room_by_id_it = rooms_by_room_id_.find(ctx.room_id);
        if (room_by_id_it != rooms_by_room_id_.end()) ctx.room_ref = room_by_id_it->second;

        return ctx;
    }

    [[nodiscard]] const auto& session_users() const { return users_by_session_id_; }
    [[nodiscard]] const auto& session_rooms() const { return rooms_by_session_id_; }
    [[nodiscard]] std::vector<SessionId> sessions_in_room(const std::string& room_id) const {
        auto it = sessions_by_room_id_.find(room_id);
        return it != sessions_by_room_id_.end() ? it->second : std::vector<SessionId>{};
    }
    [[nodiscard]] const auto& players() const { return players_by_user_id_; }
    [[nodiscard]] const auto& rooms() const { return rooms_by_room_id_; }

private:
    std::unordered_map<SessionId, std::string> users_by_session_id_;
    std::unordered_map<SessionId, std::string> rooms_by_session_id_;
    std::unordered_map<std::string, std::vector<SessionId>> sessions_by_room_id_;
    std::unordered_map<std::string, v2::actor::ActorRef> players_by_user_id_;
    std::unordered_map<std::string, v2::actor::ActorRef> rooms_by_room_id_;
};

// ── Pending Response Guard ────────────────────────────────────────────
// RAII helper that automatically clears a pending response entry from a
// map on scope exit when the response was NOT explicitly handled.

template <typename Map>
class PendingResponseGuard {
public:
    PendingResponseGuard(Map& map, const typename Map::key_type& key, PendingResponse* out)
        : map_(map), key_(key), out_(out) {}

    ~PendingResponseGuard() {
        if (!released_) {
            map_.erase(key_);
        }
    }

    void release() { released_ = true; }

    PendingResponseGuard(const PendingResponseGuard&) = delete;
    PendingResponseGuard& operator=(const PendingResponseGuard&) = delete;

private:
    Map& map_;
    typename Map::key_type key_;
    PendingResponse* out_;
    bool released_ = false;
};

// ── Bridge Route Helpers ──────────────────────────────────────────────

// Standard bridge routing: sends JSON payload to a backend service, parses
// the response, and calls on_ok or on_error callbacks.
// Returns true if the bridge handled the request (success or not).

template <typename OnOk, typename OnError>
bool bridge_route(GatewayServiceBridge* bridge,
                  v2::service::ServiceId service,
                  const std::string& handler_name,
                  const std::string& payload,
                  OnOk&& on_ok,
                  OnError&& on_error) {
    if (bridge == nullptr) return false;

    auto result = bridge->route(service, handler_name, payload);
    if (result.success) {
        auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
        if (!resp.is_discarded() && resp.value("status", "") == "ok") {
            on_ok(resp);
            return true;
        }
        std::string reason = resp.is_discarded() ? handler_name + "_failed"
            : resp.value("reason", handler_name + "_failed");
        on_error(reason);
        return true;
    }
    std::string reason = "backend_error";
    if (!result.response_payload.empty()) {
        auto resp = nlohmann::json::parse(result.response_payload, nullptr, false);
        if (!resp.is_discarded()) {
            reason = resp.value("reason", reason);
        } else {
            reason = result.response_payload;
        }
    }
    on_error(reason);
    return true;
}

}  // namespace v2::gateway
