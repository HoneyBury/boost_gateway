#include "v2/player/player_actor.h"

#include <chrono>
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
    if (const auto* battle = std::get_if<BattleAssignedMsg>(&message.payload)) {
        handle_battle_assigned(*battle);
        return;
    }
    if (const auto* settlement = std::get_if<BattleSettlementMsg>(&message.payload)) {
        handle_battle_settlement(*settlement);
        return;
    }
    if (const auto* ended = std::get_if<BattleEndedMsg>(&message.payload)) {
        handle_battle_ended(*ended);
        return;
    }
    if (const auto* closed = std::get_if<SessionClosedMsg>(&message.payload)) {
        handle_session_closed(*closed);
        return;
    }
    if (const auto* reconnect = std::get_if<ReconnectTimerExpiredMsg>(&message.payload)) {
        handle_reconnect_timeout(*reconnect);
        return;
    }
    if (const auto* refresh = std::get_if<TokenRefreshMsg>(&message.payload)) {
        handle_token_refresh(*refresh);
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

    // Cancel reconnect timer if one is active (player reconnected in window)
    if (reconnect_timer_id_.has_value()) {
        cancel_schedule(*reconnect_timer_id_);
        reconnect_timer_id_.reset();
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

    token_meta_ = TokenMeta{
        .token_type = "bearer",
        .issuer = "gateway",
        .issued_at = 0,
        .expires_at = 0,
    };
    resume_meta_.reset();

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

void PlayerActor::handle_battle_assigned(const BattleAssignedMsg& message) {
    state_.battle_actor_id = message.battle_actor_id;
    state_.battle_id = message.battle_id;
    state_.pending_battle_settlement_reason.reset();
    state_.lifecycle = PlayerLifecycleState::kInBattle;
}

void PlayerActor::handle_battle_settlement(const BattleSettlementMsg& message) {
    if (!state_.battle_id.has_value() || *state_.battle_id != message.battle_id) {
        return;
    }

    state_.pending_battle_settlement_reason = message.reason;
    sink_.push(BattleSettlementAppliedMsg{
        .battle_id = message.battle_id,
        .reason = message.reason,
    });
}

void PlayerActor::handle_battle_ended(const BattleEndedMsg& message) {
    if (state_.battle_id.has_value() && *state_.battle_id != message.battle_id) {
        return;
    }

    state_.battle_actor_id.reset();
    state_.battle_id.reset();
    state_.pending_battle_settlement_reason.reset();
    state_.lifecycle = state_.room_id.has_value()
        ? PlayerLifecycleState::kInRoom
        : PlayerLifecycleState::kOnlineIdle;
}

void PlayerActor::handle_session_closed(const SessionClosedMsg& message) {
    if (!state_.binding.has_value() || state_.binding->session_id != message.session_id) {
        return;
    }

    state_.binding.reset();
    pending_binding_.reset();

    if (state_.room_id.has_value()) {
        state_.lifecycle = PlayerLifecycleState::kSuspended;
        schedule_reconnect_timeout();
    } else {
        state_.lifecycle = PlayerLifecycleState::kOffline;
    }
}

void PlayerActor::handle_reconnect_timeout(const ReconnectTimerExpiredMsg& /*message*/) {
    reconnect_timer_id_.reset();
    resume_meta_.reset();
    if (state_.lifecycle == PlayerLifecycleState::kSuspended) {
        state_.lifecycle = PlayerLifecycleState::kOffline;
    }
}

void PlayerActor::schedule_reconnect_timeout() {
    v2::actor::Message timeout_msg;
    timeout_msg.header.kind = v2::actor::MessageKind::kUser;
    timeout_msg.payload = ReconnectTimerExpiredMsg{
        .user_id = state_.user_id,
    };
    reconnect_timer_id_ = schedule_after(self(), std::move(timeout_msg), kReconnectWindow);
}

void PlayerActor::handle_token_refresh(const TokenRefreshMsg& message) {
    if (state_.user_id.empty() || state_.user_id != message.user_id) {
        return;
    }

    // Credentials are issued only by the configured identity provider. This
    // actor has no signing authority and must never fabricate a refresh token.
    (void)message;
}

}  // namespace v2::player
