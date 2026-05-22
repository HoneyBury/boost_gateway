#pragma once

#include <chrono>
#include "v2/actor/actor.h"
#include "v2/player/message_types.h"

namespace v2::player {

class PlayerEventSink {
public:
    virtual ~PlayerEventSink() = default;

    virtual void push(PlayerEvent event) = 0;
};

class PlayerActor final : public v2::actor::Actor {
public:
    explicit PlayerActor(PlayerEventSink& sink)
        : sink_(sink) {}

    void on_message(v2::actor::Message&& message) override;

    [[nodiscard]] const PlayerRuntimeState& state() const noexcept { return state_; }

private:
    void handle_bind_session(const BindSessionMsg& message);
    void handle_login_request(const LoginRequestMsg& message);
    void handle_room_assigned(const RoomAssignedMsg& message);
    void handle_battle_assigned(const BattleAssignedMsg& message);
    void handle_battle_settlement(const BattleSettlementMsg& message);
    void handle_battle_ended(const BattleEndedMsg& message);
    void handle_session_closed(const SessionClosedMsg& message);
    void handle_reconnect_timeout(const ReconnectTimerExpiredMsg& message);
    void handle_token_refresh(const TokenRefreshMsg& message);

    void schedule_reconnect_timeout();

    static constexpr std::chrono::seconds kReconnectWindow{30};

    PlayerEventSink& sink_;
    PlayerRuntimeState state_;
    std::optional<PlayerSessionBinding> pending_binding_;
    std::optional<TokenMeta> token_meta_;
    std::optional<ResumeMeta> resume_meta_;
    std::optional<v2::actor::ScheduleId> reconnect_timer_id_;
};

}  // namespace v2::player
