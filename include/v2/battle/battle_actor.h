#pragma once

#include "v2/actor/actor.h"
#include "v2/battle/message_types.h"

namespace v2::battle {

class BattleEventSink {
public:
    virtual ~BattleEventSink() = default;

    virtual void push(BattleEvent event) = 0;
};

class BattleActor final : public v2::actor::Actor {
public:
    explicit BattleActor(BattleEventSink& sink)
        : sink_(sink) {}

    void on_message(v2::actor::Message&& message) override;

    [[nodiscard]] const BattleRuntimeState& state() const noexcept { return state_; }

private:
    BattleEventSink& sink_;
    BattleRuntimeState state_;
};

}  // namespace v2::battle
