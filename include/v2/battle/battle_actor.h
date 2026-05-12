#pragma once

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include "v2/actor/actor.h"
#include "v2/battle/message_types.h"
#include "v2/battle/runtime_world.h"
#include "v2/ecs/world.h"

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

    [[nodiscard]] std::string take_snapshot() const override;
    bool restore_from_snapshot(const std::string& snapshot_data) override;

    [[nodiscard]] BattleRuntimeState state() const;

private:
    void finish_battle(BattleFinishReason reason, std::string triggering_user_id);
    [[nodiscard]] BattleRuntimeState runtime_state() const;

    BattleEventSink& sink_;
    std::unique_ptr<v2::ecs::World> world_;
    std::map<std::string, v2::actor::ScheduleId> disconnect_grace_timers_;

    static constexpr std::chrono::seconds kDisconnectGracePeriod{15};
};

}  // namespace v2::battle
