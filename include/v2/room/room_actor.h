#pragma once

#include "v2/actor/actor.h"
#include "v2/room/message_types.h"

namespace v2::room {

class RoomEventSink {
public:
    virtual ~RoomEventSink() = default;

    virtual void push(RoomEvent event) = 0;
};

class RoomActor final : public v2::actor::Actor {
public:
    explicit RoomActor(RoomEventSink& sink)
        : sink_(sink) {}

    void on_message(v2::actor::Message&& message) override;

    [[nodiscard]] const RoomRuntimeState& state() const noexcept { return state_; }

private:
    void handle_create_room(const CreateRoomMsg& message);
    void handle_join_room(const JoinRoomMsg& message);
    void handle_set_ready(const SetReadyMsg& message);
    void handle_start_battle(const StartBattleMsg& message);
    void handle_battle_started(const BattleStartedMsg& message);
    void handle_battle_ended(const BattleEndedMsg& message);

    RoomMemberState* find_member(const std::string& user_id) noexcept;
    [[nodiscard]] bool all_members_ready() const noexcept;

    RoomEventSink& sink_;
    RoomRuntimeState state_;
};

}  // namespace v2::room
