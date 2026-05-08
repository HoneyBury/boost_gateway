#include "v2/player/player_actor.h"

#include <utility>

namespace v2::player {

void PlayerActor::on_message(v2::actor::Message&& message) {
    if (const auto* bind = std::get_if<BindSessionMsg>(&message.payload)) {
        handle_bind_session(*bind);
        return;
    }
    if (const auto* login = std::get_if<LoginRequestMsg>(&message.payload)) {
        handle_login_request(*login);
        return;
    }
    if (const auto* room = std::get_if<RoomAssignedMsg>(&message.payload)) {
        handle_room_assigned(*room);
        return;
    }
    if (const auto* closed = std::get_if<SessionClosedMsg>(&message.payload)) {
        handle_session_closed(*closed);
    }
}

void PlayerActor::handle_bind_session(const BindSessionMsg& message) {
    PlayerSessionBinding binding{
        .session_id = message.session_id,
        .connection_id = message.connection_id,
        .bound_at = message.connection_id,
    };

    if (!state_.binding.has_value() ||
        state_.lifecycle == PlayerLifecycleState::kOffline ||
        state_.lifecycle == PlayerLifecycleState::kAuthenticating) {
        state_.binding = binding;
    } else {
        pending_binding_ = binding;
    }

    if (state_.lifecycle == PlayerLifecycleState::kOffline ||
        state_.lifecycle == PlayerLifecycleState::kSuspended) {
        state_.lifecycle = PlayerLifecycleState::kAuthenticating;
    }
}

void PlayerActor::handle_login_request(const LoginRequestMsg& message) {
    const auto incoming_binding = (pending_binding_.has_value() &&
                                   pending_binding_->session_id == message.session_id)
        ? pending_binding_
        : state_.binding;

    if (state_.binding.has_value() && state_.binding->session_id != 0 &&
        state_.binding->session_id != message.session_id) {
        sink_.push(SessionKickPushMsg{
            .old_session_id = state_.binding->session_id,
            .new_session_id = message.session_id,
        });
    }

    state_.user_id = message.user_id;
    state_.display_name = message.display_name.value_or(message.user_id);
    state_.binding = PlayerSessionBinding{
        .session_id = message.session_id,
        .connection_id = incoming_binding.has_value()
            ? incoming_binding->connection_id
            : message.session_id,
        .bound_at = message.session_id,
    };
    pending_binding_.reset();
    state_.lifecycle = state_.room_id.has_value()
        ? PlayerLifecycleState::kInRoom
        : PlayerLifecycleState::kOnlineIdle;

    sink_.push(LoginAcceptedMsg{
        .session_id = message.session_id,
        .user_id = state_.user_id,
        .display_name = state_.display_name,
        .room_id = state_.room_id,
    });

    if (state_.room_id.has_value()) {
        sink_.push(SessionResumePushMsg{
            .session_id = message.session_id,
            .room_id = *state_.room_id,
            .in_battle = state_.lifecycle == PlayerLifecycleState::kInBattle,
        });
    }
}

void PlayerActor::handle_room_assigned(const RoomAssignedMsg& message) {
    state_.room_actor_id = message.room_actor_id;
    state_.room_id = message.room_id;
    state_.lifecycle = PlayerLifecycleState::kInRoom;
}

void PlayerActor::handle_session_closed(const SessionClosedMsg& message) {
    if (!state_.binding.has_value() || state_.binding->session_id != message.session_id) {
        return;
    }

    state_.binding.reset();
    pending_binding_.reset();
    state_.lifecycle = state_.room_id.has_value()
        ? PlayerLifecycleState::kSuspended
        : PlayerLifecycleState::kOffline;
}

}  // namespace v2::player
