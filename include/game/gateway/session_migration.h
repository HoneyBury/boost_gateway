#pragma once

#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"
#include "game/battle/battle_manager.h"

#include <nlohmann/json.hpp>

#include <optional>
#include <string>

namespace game::gateway {

struct SessionMigrationState {
    std::string user_id;
    std::string display_name;
    std::string room_id;
    bool battle_started = false;
};

inline std::optional<SessionMigrationState> capture_session_state(
    const SessionManager::SessionPtr& session,
    SessionManager& sm,
    room::RoomManager& rm,
    battle::BattleManager& bm) {

    auto user_id = sm.user_id_of(session);
    if (!user_id) return std::nullopt;

    auto ctx = sm.login_context_of(session);
    auto room_id = rm.room_id_of(session);

    SessionMigrationState state;
    state.user_id = *user_id;
    state.display_name = ctx ? ctx->display_name : *user_id;
    state.room_id = room_id.value_or("");
    if (room_id) state.battle_started = bm.battle_started(*room_id);
    return state;
}

inline std::string serialize_migration_state(const SessionMigrationState& state) {
    return nlohmann::json{
        {"user_id", state.user_id},
        {"display_name", state.display_name},
        {"room_id", state.room_id},
        {"battle_started", state.battle_started},
    }.dump();
}

inline std::optional<SessionMigrationState> deserialize_migration_state(const std::string& data) {
    try {
        auto doc = nlohmann::json::parse(data);
        return SessionMigrationState{
            .user_id = doc.value("user_id", ""),
            .display_name = doc.value("display_name", ""),
            .room_id = doc.value("room_id", ""),
            .battle_started = doc.value("battle_started", false),
        };
    } catch (...) {
        return std::nullopt;
    }
}

}  // namespace game::gateway
