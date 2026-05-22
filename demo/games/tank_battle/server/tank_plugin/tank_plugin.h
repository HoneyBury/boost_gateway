#pragma once

#include "v2/realtime/instance_plugin.h"
#include "../tank_simulation/tank_world.h"

#include <memory>
#include <unordered_map>

namespace tank {

// ─── TankPlugin ─────────────────────────────────────────────────────
//
// Adapts the deterministic TankWorld simulation to the realtime
// instance runtime SPI.

class TankPlugin : public v2::realtime::InstancePlugin {
public:
    TankPlugin();
    ~TankPlugin() override;

    // InstancePlugin interface
    void on_instance_created(v2::realtime::InstanceContext& ctx) override;
    void on_player_join(v2::realtime::InstanceContext& ctx,
                        const v2::realtime::PlayerContext& player) override;
    void on_player_leave(v2::realtime::InstanceContext& ctx,
                         const v2::realtime::PlayerContext& player) override;
    v2::realtime::InputResult on_input(v2::realtime::InstanceContext& ctx,
                                        const v2::realtime::InputEnvelope& input) override;
    v2::realtime::TickStats on_tick(v2::realtime::InstanceContext& ctx,
                                     const v2::realtime::FrameContext& frame_ctx) override;
    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext& ctx,
                                           bool is_resume) override;
    std::string build_settlement(v2::realtime::InstanceContext& ctx,
                                  const v2::realtime::SettlementContext& sctx) override;
    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext& ctx,
                                                  const v2::realtime::PlayerContext& player) override;

private:
    TankWorld world_;
    std::unordered_map<std::string, std::uint64_t> last_seqs_;
    bool initialised_ = false;
};

}  // namespace tank
