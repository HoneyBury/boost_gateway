#pragma once

#include "v2/battle/message_types.h"
#include "v2/ecs/component.h"
#include "v2/ecs/system.h"

#include <cstdint>
#include <string>
#include <vector>

namespace v2::battle {

struct BattleClockComponent final : v2::ecs::Component {
    std::uint32_t frame_number = 0;
    std::string last_trigger;
};

struct BattleParticipantComponent final : v2::ecs::Component {
    std::string user_id;
    bool online = true;
    std::int64_t score = 0;
    std::uint32_t last_submitted_frame = 0;
    std::uint32_t last_acked_frame = 0;
};

struct BattleMetadataComponent final : v2::ecs::Component {
    std::string battle_id;
    std::string room_id;
    BattleLifecycleState lifecycle = BattleLifecycleState::kCreated;
    std::uint64_t next_input_seq = 1;
    std::uint32_t max_frames = 0;
    std::uint32_t current_frame_number = 0;
};

struct BattleReplayLogComponent final : v2::ecs::Component {
    std::vector<BattleReplayInputRecord> replay_inputs;
};

class AdvanceFrameSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

}  // namespace v2::battle
