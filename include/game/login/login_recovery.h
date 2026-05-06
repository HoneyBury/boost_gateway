#pragma once

#include <memory>
#include <optional>
#include <string>

namespace net {
class Session;
}

namespace game::room {
class RoomManager;
}

namespace game::login {

using SessionPtr = std::shared_ptr<net::Session>;

/// v1.1.7 / T07：顶号时将旧会话的房间席位迁移到新会话；返回是否发生过房间迁移。
[[nodiscard]] bool transfer_room_for_duplicate_login(game::room::RoomManager& rooms,
                                                     const SessionPtr& old_session,
                                                     const SessionPtr& new_session);

/// 登录成功后，若当前会话已在房间内，生成 `login_ok` 后缀与可选的 `session_resumed` push body。
struct LoginRoomNotifyArtifacts {
    std::optional<std::string> login_ok_room_suffix;  ///< 形如 `:room=` + room_id，拼在 `login_ok:…` 后
    std::optional<std::string> session_resumed_body;  ///< 整段 push body
};

[[nodiscard]] LoginRoomNotifyArtifacts build_login_room_notify_paths(const game::room::RoomManager& rooms,
                                                                     const SessionPtr& session);

}  // namespace game::login
