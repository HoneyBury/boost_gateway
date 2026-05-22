#pragma once

#include "v2/ecs/entity.h"
#include "v2/ecs/system.h"

#include <cstdint>
#include <string>

namespace v2::battle {

struct ProjectileComponent;  // forward declaration

class MovementSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

class CombatSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;
};

// v3.4.0: Projectile/AoE system with travel time and damage-over-time
class ProjectileSystem final : public v2::ecs::System {
public:
    void run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) override;

    // Helper: spawn a projectile and add it to the world
    // Returns the ProjectileComponent reference so the caller can modify if needed
    static ProjectileComponent& spawn_projectile(
        v2::ecs::World& world,
        v2::ecs::EntityHandle source_entity,
        const std::string& projectile_id,
        const std::string& owner_user_id,
        const std::string& target_user_id,
        std::int32_t start_x, std::int32_t start_y,
        std::int32_t target_x, std::int32_t target_y,
        std::int32_t damage,
        std::int32_t speed,
        std::int32_t aoe_radius = 0,
        std::uint32_t duration_frames = 0);
};

}  // namespace v2::battle
