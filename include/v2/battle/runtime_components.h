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
    std::string pending_input_data;
    std::string pending_target_user_id;
    std::int32_t pending_move_x = 0;
    std::int32_t pending_move_y = 0;
    bool has_pending_move = false;
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

struct PositionComponent final : v2::ecs::Component {
    std::int32_t x = 0;
    std::int32_t y = 0;
};

struct HealthComponent final : v2::ecs::Component {
    std::int32_t hp = 100;
    std::int32_t max_hp = 100;
};

struct AttackStateComponent final : v2::ecs::Component {
    std::int32_t damage = 10;
    std::int32_t range = 1;
    std::string last_target_user_id;
};

// v2.3.0: Anti-cheat attack cooldown tracking
struct AttackCooldownComponent final : v2::ecs::Component {
    std::uint32_t last_attack_frame = 0;
    std::uint32_t cooldown_frames = 3;  // min frames between attacks
    std::uint32_t attacks_this_frame = 0;
    static constexpr std::uint32_t kMaxAttacksPerFrame = 1;
};

class BattleClockSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

class BattleInputSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

class BattleLifecycleSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

class BattleReplaySystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

}  // namespace v2::battle
