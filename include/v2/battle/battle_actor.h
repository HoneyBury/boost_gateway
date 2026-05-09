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
    void finish_battle(std::string reason, std::string triggering_user_id);

    static constexpr std::uint32_t kFrameLimit = 3;
    std::uint64_t next_input_seq_ = 1;
    BattleEventSink& sink_;
    BattleRuntimeState state_;
};

}  // namespace v2::battle
