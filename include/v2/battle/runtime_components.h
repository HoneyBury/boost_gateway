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

struct BattleReplayFrameRecord {
    std::uint32_t frame_number = 0;

    struct ParticipantState {
        std::string user_id;
        std::int32_t x = 0;
        std::int32_t y = 0;
        std::int32_t hp = 0;
        std::int64_t score = 0;
        bool online = true;
    };

    std::vector<ParticipantState> participants;
    BattleLifecycleState lifecycle = BattleLifecycleState::kCreated;
};

struct BattleReplayLogComponent final : v2::ecs::Component {
    std::vector<BattleReplayInputRecord> replay_inputs;
    std::vector<BattleReplayFrameRecord> frame_snapshots;
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
    explicit BattleLifecycleSystem(
        std::uint32_t max_idle_frames = 300,
        std::uint32_t max_offline_frames = 60);

    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;

private:
    std::uint32_t max_idle_frames_;
    std::uint32_t max_offline_frames_;
    std::uint32_t idle_frames_ = 0;
    std::uint32_t offline_frames_ = 0;
    std::uint64_t last_input_seq_ = 0;
};

class BattleReplaySystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

// v3.4.0: Projectile with travel time
struct ProjectileComponent final : v2::ecs::Component {
    std::string projectile_id;           // unique ID
    std::string owner_user_id;           // who fired it
    std::string target_user_id;          // intended target (empty for AoE)
    std::int32_t start_x = 0;            // launch position
    std::int32_t start_y = 0;
    std::int32_t target_x = 0;           // target position
    std::int32_t target_y = 0;
    std::int32_t damage = 10;            // damage on impact
    std::int32_t speed = 50;             // Manhattan distance per frame
    std::int32_t aoe_radius = 0;         // 0 = single target, >0 = AoE
    std::uint32_t duration_frames = 0;   // damage-over-time frames (0 = instant)
    std::uint32_t current_frame = 0;     // frames elapsed
    bool active = true;
};

// v3.4.0: Damage-over-time effect applied to entities
struct DamageOverlayComponent final : v2::ecs::Component {
    std::string source_projectile_id;
    std::int32_t damage_per_tick = 0;
    std::uint32_t remaining_ticks = 0;
    std::uint32_t interval_frames = 1;   // apply every N frames
    std::uint32_t last_applied_frame = 0;
};

}  // namespace v2::battle
