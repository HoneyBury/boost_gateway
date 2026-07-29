#pragma once

#include "v2/battle/runtime_components.h"
#include "v2/ecs/world.h"
#include "v2/realtime/instance_plugin.h"

#include <memory>
#include <string>
#include <unordered_map>

namespace v2::battle {

class BattleInstancePlugin final : public v2::realtime::InstancePlugin {
public:
    ~BattleInstancePlugin() override = default;

    void on_instance_created(v2::realtime::InstanceContext& instance_ctx) override;
    void on_instance_destroyed(v2::realtime::InstanceContext& instance_ctx) noexcept override;
    void on_player_join(v2::realtime::InstanceContext& instance_ctx,
                        const v2::realtime::PlayerContext& player) override;
    void on_player_leave(v2::realtime::InstanceContext& instance_ctx,
                         const v2::realtime::PlayerContext& player) override;

    v2::realtime::InputResult on_input(v2::realtime::InstanceContext& instance_ctx,
                                        const v2::realtime::InputEnvelope& input) override;

    v2::realtime::TickStats on_tick(v2::realtime::InstanceContext& instance_ctx,
                                     const v2::realtime::FrameContext& frame_ctx) noexcept override;

    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext& instance_ctx,
                                           bool is_resume = false) noexcept override;
    std::string build_settlement(v2::realtime::InstanceContext& instance_ctx,
                                  const v2::realtime::SettlementContext& settlement_ctx) noexcept override;
    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext& instance_ctx,
                                                  const v2::realtime::PlayerContext& player) noexcept override;

    // ─── Public state accessors ──────────────────────────────────────────
    //
    // These allow service-level code (e.g. BattleBackendService) to read the
    // plugin state without coupling to internal layout details.

    struct State {
        std::unique_ptr<v2::ecs::World> world;
        std::unordered_map<std::string, v2::ecs::EntityHandle> player_entities;
        bool finish_requested = false;
    };

    [[nodiscard]] static State& get_state(v2::realtime::InstanceContext& instance_ctx);
    [[nodiscard]] static const State& get_state(const v2::realtime::InstanceContext& instance_ctx);
};

}  // namespace v2::battle
