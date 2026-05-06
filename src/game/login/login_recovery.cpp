#include "game/login/login_recovery.h"

#include "game/room/room_manager.h"

namespace game::login {

bool transfer_room_for_duplicate_login(game::room::RoomManager& rooms,
                                       const SessionPtr& old_session,
                                       const SessionPtr& new_session) {
    return rooms.transfer_session(old_session, new_session);
}

LoginRoomNotifyArtifacts build_login_room_notify_paths(const game::room::RoomManager& rooms,
                                                       const SessionPtr& session) {
    const auto room_snapshot = rooms.room_snapshot_of(session);
    if (!room_snapshot) {
        return {};
    }

    LoginRoomNotifyArtifacts out;
    out.login_ok_room_suffix = std::string(":room=") + room_snapshot->room_id;
    out.session_resumed_body =
        std::string("session_resumed:") + room_snapshot->room_id +
        (room_snapshot->battle_started ? ":battle=1" : ":battle=0");
    return out;
}

}  // namespace game::login
