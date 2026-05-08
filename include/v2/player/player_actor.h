#pragma once

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
    void handle_session_closed(const SessionClosedMsg& message);

    PlayerEventSink& sink_;
    PlayerRuntimeState state_;
    std::optional<PlayerSessionBinding> pending_binding_;
};

}  // namespace v2::player
