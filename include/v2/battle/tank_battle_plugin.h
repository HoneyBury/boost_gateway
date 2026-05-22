#pragma once

#include "v2/battle/runtime_components.h"
#include "v2/ecs/world.h"
#include "v2/realtime/instance_plugin.h"

#include <memory>
#include <string>
#include <unordered_map>

namespace v2::battle {

// ─── TankBattlePlugin ──────────────────────────────────────────────────
//
// Implements InstancePlugin for a tank battle game. Manages an ECS World
// (MovementSystem, CombatSystem, BattleClockSystem) and maps player
// user_ids to entities.
//
// Input format (JSON payload):
//   {"action":"move","x":10,"y":20}
//   {"action":"attack","target_user_id":"bob"}
//   {"action":"finish","reason":"surrender"}
//
// Snapshot payload (JSON):
//   {"type":"tank.snapshot","players":[...],"frame":N}
//
// Settlement payload (JSON):
//   {"type":"tank.settlement","total_frames":N,"players":[...]}

class TankBattlePlugin final : public v2::realtime::InstancePlugin {
public:
    ~TankBattlePlugin() override = default;

    // ── Lifecycle hooks ──────────────────────────────────────────
    void on_instance_created(v2::realtime::InstanceContext& instance_ctx) override;
    void on_player_join(v2::realtime::InstanceContext& instance_ctx,
                        const v2::realtime::PlayerContext& player) override;
    void on_player_leave(v2::realtime::InstanceContext& instance_ctx,
                         const v2::realtime::PlayerContext& player) override;

    // ── Input processing ────────────────────────────────────────
    v2::realtime::InputResult on_input(v2::realtime::InstanceContext& instance_ctx,
                                        const v2::realtime::InputEnvelope& input) override;

    // ── Tick / simulation ───────────────────────────────────────
    v2::realtime::TickStats on_tick(v2::realtime::InstanceContext& instance_ctx,
                                     const v2::realtime::FrameContext& frame_ctx) noexcept override;

    // ── Snapshot / settlement ───────────────────────────────────
    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext& instance_ctx,
                                           bool is_resume = false) noexcept override;
    std::string build_settlement(v2::realtime::InstanceContext& instance_ctx,
                                  const v2::realtime::SettlementContext& settlement_ctx) noexcept override;
    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext& instance_ctx,
                                                  const v2::realtime::PlayerContext& player) noexcept override;

private:
    // Internal plugin state stored here. Also pointed to by
    // instance_ctx.plugin_state for SPI compliance.
    struct State {
        std::unique_ptr<v2::ecs::SimpleWorld> world;
        std::unordered_map<std::string, v2::ecs::EntityHandle> player_entities;
        bool finish_requested = false;
    };

    [[nodiscard]] State& get_state(v2::realtime::InstanceContext& instance_ctx);
    [[nodiscard]] const State& get_state(const v2::realtime::InstanceContext& instance_ctx) const;

    void create_player_entity(State& state, const std::string& user_id);
};

}  // namespace v2::battle
