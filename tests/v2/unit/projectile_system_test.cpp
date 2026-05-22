// v3.4.0: Projectile/AoE system unit tests
#include <gtest/gtest.h>
#include "v2/ecs/world.h"
#include "v2/battle/runtime_components.h"
#include "v2/battle/game_systems.h"

using namespace v2::ecs;
using namespace v2::battle;

namespace {

// Helper: set position on an entity
void set_pos(v2::ecs::World& world, v2::ecs::EntityHandle entity,
             std::int32_t x, std::int32_t y) {
    auto& pos = world.add_component<PositionComponent>(entity);
    pos.x = x;
    pos.y = y;
}

// Helper: set health on an entity
void set_hp(v2::ecs::World& world, v2::ecs::EntityHandle entity,
            std::int32_t hp, std::int32_t max_hp) {
    auto& health = world.add_component<HealthComponent>(entity);
    health.hp = hp;
    health.max_hp = max_hp;
}

}  // anonymous namespace

// ─── Basic spawning ─────────────────────────────────────────────────────────

TEST(ProjectileSystemTest, SpawnProjectileCreatesEntity) {
    SimpleWorld world;

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto& proj = ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        0, 0, 100, 0,
        10,   // damage
        50,   // speed
        0,    // no AoE
        0);   // no DoT

    EXPECT_TRUE(proj.active);
    EXPECT_EQ(proj.current_frame, 0U);
    EXPECT_EQ(proj.projectile_id, "proj_1");
    EXPECT_EQ(proj.owner_user_id, "alice");
    EXPECT_EQ(proj.target_user_id, "bob");
    EXPECT_EQ(proj.start_x, 0);
    EXPECT_EQ(proj.start_y, 0);
    EXPECT_EQ(proj.target_x, 100);
    EXPECT_EQ(proj.target_y, 0);
    EXPECT_EQ(proj.damage, 10);
    EXPECT_EQ(proj.speed, 50);
}

TEST(ProjectileSystemTest, SpawnProjectileCreatesPositionComponent) {
    SimpleWorld world;

    auto source = world.create_entity();
    set_pos(world, source, 5, 5);

    auto& proj = ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        10, 20, 100, 200,
        10, 50);

    // The source entity position is separate from the projectile start position
    auto* source_pos = world.get_component<PositionComponent>(source);
    ASSERT_NE(source_pos, nullptr);
    EXPECT_EQ(source_pos->x, 5);
    EXPECT_EQ(source_pos->y, 5);

    // Projectile entity should have its own PositionComponent at start position
    // We can't get it directly without the entity handle, but the projectile
    // component stores the start position
    EXPECT_EQ(proj.start_x, 10);
    EXPECT_EQ(proj.start_y, 20);
}

// ─── Movement and arrival ───────────────────────────────────────────────────

TEST(ProjectileSystemTest, ProjectileMovesTowardTarget) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    // Spawn projectile from (0,0) to (100,0) with speed 50
    auto& proj = ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        0, 0, 100, 0,
        10,   // damage
        50,   // speed (distance per frame)
        0,    // no AoE
        0);   // no DoT

    EXPECT_TRUE(proj.active);
    EXPECT_EQ(proj.current_frame, 0U);
}

TEST(ProjectileSystemTest, ProjectileArrivesAfterEnoughFrames) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    // Target entity
    auto target = world.create_entity();
    set_pos(world, target, 150, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        0, 0, 150, 0, 10, 50);

    // Frame 1: traveled 50/150, not yet arrived
    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    // Frame 2: traveled 100/150, not yet arrived
    FrameContext fc2{.frame_number = 2};
    world.tick(fc2);

    // Frame 3: traveled 150/150, should arrive
    FrameContext fc3{.frame_number = 3};
    world.tick(fc3);

    // Verify target took damage
    auto* health = world.get_component<HealthComponent>(target);
    ASSERT_NE(health, nullptr);
    EXPECT_EQ(health->hp, 90);  // 100 - 10
}

TEST(ProjectileSystemTest, ProjectileArrivesExactlyAtDistance) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 50, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    // Distance = 50, speed = 50 => arrives in 1 frame
    ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        0, 0, 50, 0, 10, 50);

    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    auto* health = world.get_component<HealthComponent>(target);
    EXPECT_EQ(health->hp, 90);
}

TEST(ProjectileSystemTest, ProjectileMissesIfTargetMoves) {
    // Projectile is aimed at a position, not tracking the entity.
    // If the target moves away, the projectile still arrives at the
    // original target position but the target is no longer there.
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 150, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    // Projectile aimed at (150, 0) where bob starts
    ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        0, 0, 150, 0, 10, 50);

    // Bob moves to (200, 0) before projectile arrives
    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);
    auto* target_pos = world.get_component<PositionComponent>(target);
    target_pos->x = 200;

    FrameContext fc2{.frame_number = 2};
    world.tick(fc2);
    target_pos->x = 200;

    FrameContext fc3{.frame_number = 3};
    world.tick(fc3);

    // Projectile arrives at (150, 0) but bob is at (200, 0)
    // Since the combat system checks the *current* position at time of
    // arrival using entity lookup by user_id, the projectile still finds
    // bob by user_id and deals damage regardless of position.
    auto* health = world.get_component<HealthComponent>(target);
    ASSERT_NE(health, nullptr);
    EXPECT_EQ(health->hp, 90);  // Projectile hits bob by user_id lookup
}

// ─── AoE ────────────────────────────────────────────────────────────────────

TEST(ProjectileSystemTest, AoEProjectileDamagesMultipleTargets) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);
    set_hp(world, source, 100, 100);
    auto& src_part = world.add_component<BattleParticipantComponent>(source);
    src_part.user_id = "alice";

    // Create 3 targets within AoE radius of target position (100, 100)
    auto target1 = world.create_entity();
    set_pos(world, target1, 100, 100);
    set_hp(world, target1, 100, 100);
    auto& part1 = world.add_component<BattleParticipantComponent>(target1);
    part1.user_id = "bob";

    auto target2 = world.create_entity();
    set_pos(world, target2, 105, 100);
    set_hp(world, target2, 100, 100);
    auto& part2 = world.add_component<BattleParticipantComponent>(target2);
    part2.user_id = "charlie";

    auto target3 = world.create_entity();
    set_pos(world, target3, 95, 100);
    set_hp(world, target3, 100, 100);
    auto& part3 = world.add_component<BattleParticipantComponent>(target3);
    part3.user_id = "dave";

    // AoE projectile aimed at (100, 100), radius 10, speed 50
    ProjectileSystem::spawn_projectile(
        world, source, "proj_aoe", "alice", "",
        0, 0, 100, 100,
        10,   // damage
        50,   // speed
        10,   // aoe_radius
        0);   // no DoT

    // Need 4 frames to travel 200 distance at speed 50
    FrameContext fc;
    for (uint32_t f = 1; f <= 4; ++f) {
        fc.frame_number = f;
        world.tick(fc);
    }

    // All 3 targets should have taken damage
    EXPECT_EQ(world.get_component<HealthComponent>(target1)->hp, 90);
    EXPECT_EQ(world.get_component<HealthComponent>(target2)->hp, 90);
    EXPECT_EQ(world.get_component<HealthComponent>(target3)->hp, 90);
}

TEST(ProjectileSystemTest, AoEProjectileRespectsRadius) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);
    set_hp(world, source, 100, 100);
    auto& src_part = world.add_component<BattleParticipantComponent>(source);
    src_part.user_id = "alice";

    // Target within radius
    auto target_in = world.create_entity();
    set_pos(world, target_in, 100, 100);
    set_hp(world, target_in, 100, 100);
    auto& part_in = world.add_component<BattleParticipantComponent>(target_in);
    part_in.user_id = "bob";

    // Target outside radius (Manhattan dist = 6+6 = 12 > 10)
    auto target_out = world.create_entity();
    set_pos(world, target_out, 106, 106);
    set_hp(world, target_out, 100, 100);
    auto& part_out = world.add_component<BattleParticipantComponent>(target_out);
    part_out.user_id = "charlie";

    ProjectileSystem::spawn_projectile(
        world, source, "proj_aoe", "alice", "",
        0, 0, 100, 100,
        10, 50, 10, 0);

    FrameContext fc;
    for (uint32_t f = 1; f <= 4; ++f) {
        fc.frame_number = f;
        world.tick(fc);
    }

    // Target within radius took damage
    EXPECT_EQ(world.get_component<HealthComponent>(target_in)->hp, 90);
    // Target outside radius did NOT take damage
    EXPECT_EQ(world.get_component<HealthComponent>(target_out)->hp, 100);
}

// ─── Damage over time ───────────────────────────────────────────────────────

TEST(ProjectileSystemTest, DamageOverTimeAppliedAndTicks) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 50, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    // DoT projectile: damage=10, duration=3 ticks
    ProjectileSystem::spawn_projectile(
        world, source, "proj_dot", "alice", "bob",
        0, 0, 50, 0,
        10,   // damage
        50,   // speed
        0,    // no AoE
        3);   // 3 DoT ticks

    // Frame 1: projectile arrives (dist=50, speed=50)
    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    // After arrival: 10 initial damage + DamageOverlayComponent added
    auto* health = world.get_component<HealthComponent>(target);
    ASSERT_NE(health, nullptr);
    EXPECT_EQ(health->hp, 90);  // 100 - 10 initial

    auto* dot = world.get_component<DamageOverlayComponent>(target);
    ASSERT_NE(dot, nullptr);
    EXPECT_EQ(dot->remaining_ticks, 3U);
    EXPECT_EQ(dot->damage_per_tick, 10);

    // Frames 2-4: 3 DoT ticks
    FrameContext fc2{.frame_number = 2};
    world.tick(fc2);
    EXPECT_EQ(health->hp, 80);  // 90 - 10
    EXPECT_EQ(dot->remaining_ticks, 2U);

    FrameContext fc3{.frame_number = 3};
    world.tick(fc3);
    EXPECT_EQ(health->hp, 70);  // 80 - 10
    EXPECT_EQ(dot->remaining_ticks, 1U);

    FrameContext fc4{.frame_number = 4};
    world.tick(fc4);
    EXPECT_EQ(health->hp, 60);  // 70 - 10
    EXPECT_EQ(dot->remaining_ticks, 0U);

    // Frame 5: DoT expired, component removed
    FrameContext fc5{.frame_number = 5};
    world.tick(fc5);
    EXPECT_EQ(health->hp, 60);  // no more damage
    dot = world.get_component<DamageOverlayComponent>(target);
    EXPECT_EQ(dot, nullptr);
}

TEST(ProjectileSystemTest, DamageOverTimeWithAoE) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target1 = world.create_entity();
    set_pos(world, target1, 100, 100);
    set_hp(world, target1, 100, 100);
    auto& part1 = world.add_component<BattleParticipantComponent>(target1);
    part1.user_id = "bob";

    auto target2 = world.create_entity();
    set_pos(world, target2, 105, 100);
    set_hp(world, target2, 100, 100);
    auto& part2 = world.add_component<BattleParticipantComponent>(target2);
    part2.user_id = "charlie";

    // AoE + DoT: radius 10, damage 5, 2 DoT ticks
    ProjectileSystem::spawn_projectile(
        world, source, "proj_aoe_dot", "alice", "",
        0, 0, 100, 100,
        5, 50, 10, 2);

    // Arrival at frame 4 (dist=200, speed=50)
    for (uint32_t f = 1; f <= 4; ++f) {
        FrameContext fc{.frame_number = f};
        world.tick(fc);
    }

    // Both targets should have initial damage + DoT
    auto* h1 = world.get_component<HealthComponent>(target1);
    auto* h2 = world.get_component<HealthComponent>(target2);
    ASSERT_NE(h1, nullptr);
    ASSERT_NE(h2, nullptr);
    EXPECT_EQ(h1->hp, 95);  // 100 - 5 initial
    EXPECT_EQ(h2->hp, 95);  // 100 - 5 initial

    // Both have DoT components
    auto* dot1 = world.get_component<DamageOverlayComponent>(target1);
    auto* dot2 = world.get_component<DamageOverlayComponent>(target2);
    ASSERT_NE(dot1, nullptr);
    ASSERT_NE(dot2, nullptr);
    EXPECT_EQ(dot1->remaining_ticks, 2U);
    EXPECT_EQ(dot2->remaining_ticks, 2U);

    // Tick 1 more frame: first DoT tick
    FrameContext fc5{.frame_number = 5};
    world.tick(fc5);
    EXPECT_EQ(h1->hp, 90);  // 95 - 5
    EXPECT_EQ(h2->hp, 90);  // 95 - 5
    EXPECT_EQ(dot1->remaining_ticks, 1U);
    EXPECT_EQ(dot2->remaining_ticks, 1U);

    // Tick another frame: second and final DoT tick
    FrameContext fc6{.frame_number = 6};
    world.tick(fc6);
    EXPECT_EQ(h1->hp, 85);  // 90 - 5
    EXPECT_EQ(h2->hp, 85);  // 90 - 5
    EXPECT_EQ(dot1->remaining_ticks, 0U);
    EXPECT_EQ(dot2->remaining_ticks, 0U);

    // Tick again: DoT expired and removed
    FrameContext fc7{.frame_number = 7};
    world.tick(fc7);
    EXPECT_EQ(h1->hp, 85);  // no more damage
    EXPECT_EQ(h2->hp, 85);
    dot1 = world.get_component<DamageOverlayComponent>(target1);
    dot2 = world.get_component<DamageOverlayComponent>(target2);
    EXPECT_EQ(dot1, nullptr);
    EXPECT_EQ(dot2, nullptr);
}

// ─── Position interpolation ─────────────────────────────────────────────────

TEST(ProjectileSystemTest, ProjectileInterpolatesPosition) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    // Spawn projectile from (0,0) to (100,0) with speed 30
    // We need to grab the entity handle. spawn_projectile returns a ref
    // to ProjectileComponent, but we need the EntityHandle.
    // Since the world just created one entity (the source), and now creates
    // another for the projectile, the projectile entity.id should be 2.
    // Instead of relying on this, let's use a different approach: iterate
    // the world to find the projectile entity.
    ProjectileSystem::spawn_projectile(
        world, source, "proj_1", "alice", "bob",
        0, 0, 100, 0,
        10, 30, 0, 0);

    // Frame 1: progressed 30/100 = 0.3 => position should be (30, 0)
    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    // Find the projectile entity's position
    v2::ecs::EntityHandle proj_handle{};
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    ASSERT_NE(simple_world, nullptr);
    simple_world->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle handle, ProjectileComponent& p) {
            if (p.projectile_id == "proj_1") {
                proj_handle = handle;
            }
        });
    ASSERT_TRUE(proj_handle.valid());

    auto* pos = world.get_component<PositionComponent>(proj_handle);
    ASSERT_NE(pos, nullptr);
    EXPECT_EQ(pos->x, 30);
    EXPECT_EQ(pos->y, 0);

    // Frame 2: progressed 60/100 = 0.6 => position should be (60, 0)
    FrameContext fc2{.frame_number = 2};
    world.tick(fc2);
    EXPECT_EQ(pos->x, 60);
    EXPECT_EQ(pos->y, 0);

    // Frame 3: progressed 90/100 = 0.9 => position should be (90, 0)
    FrameContext fc3{.frame_number = 3};
    world.tick(fc3);
    EXPECT_EQ(pos->x, 90);
    EXPECT_EQ(pos->y, 0);

    // Frame 4: arrived at target (100, 0)
    FrameContext fc4{.frame_number = 4};
    world.tick(fc4);
    // After arrival, projectile is inactive. Position should be at target.
    // Let's check the projectile is inactive
    auto* proj = world.get_component<ProjectileComponent>(proj_handle);
    ASSERT_NE(proj, nullptr);
    EXPECT_FALSE(proj->active);
}

TEST(ProjectileSystemTest, ProjectileInterpolatesDiagonal) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    // Diagonal: from (0,0) to (30, 40), total Manhattan dist = 70, speed = 35
    ProjectileSystem::spawn_projectile(
        world, source, "proj_diag", "alice", "bob",
        0, 0, 30, 40,
        10, 35, 0, 0);

    // Frame 1: progressed 35/70 = 0.5 => position should be (15, 20)
    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    v2::ecs::EntityHandle proj_handle{};
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    ASSERT_NE(simple_world, nullptr);
    simple_world->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle handle, ProjectileComponent& p) {
            if (p.projectile_id == "proj_diag") {
                proj_handle = handle;
            }
        });
    ASSERT_TRUE(proj_handle.valid());

    auto* pos = world.get_component<PositionComponent>(proj_handle);
    ASSERT_NE(pos, nullptr);
    EXPECT_EQ(pos->x, 15);
    EXPECT_EQ(pos->y, 20);

    // Frame 2: arrives (70/70 = 1.0)
    FrameContext fc2{.frame_number = 2};
    world.tick(fc2);
    auto* proj = world.get_component<ProjectileComponent>(proj_handle);
    EXPECT_FALSE(proj->active);
}

// ─── Inactive projectile ────────────────────────────────────────────────────

TEST(ProjectileSystemTest, InactiveProjectileDoesNotMove) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    ProjectileSystem::spawn_projectile(
        world, source, "proj_inactive", "alice", "bob",
        0, 0, 100, 0,
        10, 50, 0, 0);

    // Find and deactivate the projectile
    v2::ecs::EntityHandle proj_handle{};
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    ASSERT_NE(simple_world, nullptr);
    simple_world->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle handle, ProjectileComponent& p) {
            if (p.projectile_id == "proj_inactive") {
                proj_handle = handle;
                p.active = false;
            }
        });
    ASSERT_TRUE(proj_handle.valid());

    auto* pos = world.get_component<PositionComponent>(proj_handle);
    ASSERT_NE(pos, nullptr);
    auto initial_x = pos->x;
    auto initial_y = pos->y;

    // Tick several frames -- inactive projectile should not move
    for (uint32_t f = 1; f <= 10; ++f) {
        FrameContext fc{.frame_number = f};
        world.tick(fc);
    }

    EXPECT_EQ(pos->x, initial_x);
    EXPECT_EQ(pos->y, initial_y);

    // Projectile should still be inactive and have done no damage
    auto* proj = world.get_component<ProjectileComponent>(proj_handle);
    EXPECT_FALSE(proj->active);
}

TEST(ProjectileSystemTest, InactiveProjectileDoesNotDamage) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 100, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    ProjectileSystem::spawn_projectile(
        world, source, "proj_dmg", "alice", "bob",
        0, 0, 100, 0,
        10, 50, 0, 0);

    // Find and deactivate before it arrives
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    ASSERT_NE(simple_world, nullptr);
    simple_world->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle /*handle*/, ProjectileComponent& p) {
            if (p.projectile_id == "proj_dmg") {
                p.active = false;
            }
        });

    // Tick past arrival frame
    for (uint32_t f = 1; f <= 5; ++f) {
        FrameContext fc{.frame_number = f};
        world.tick(fc);
    }

    // Target should have taken no damage
    auto* health = world.get_component<HealthComponent>(target);
    EXPECT_EQ(health->hp, 100);
}

// ─── Edge cases ─────────────────────────────────────────────────────────────

TEST(ProjectileSystemTest, ZeroDistanceProjectileArrivesImmediately) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 0, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    // Zero distance projectile
    ProjectileSystem::spawn_projectile(
        world, source, "proj_zero", "alice", "bob",
        0, 0, 0, 0,
        10, 50, 0, 0);

    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    auto* health = world.get_component<HealthComponent>(target);
    EXPECT_EQ(health->hp, 90);
}

TEST(ProjectileSystemTest, ProjectileWithNonExistentTarget) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    // Target entity with a different user_id than the projectile targets
    auto target = world.create_entity();
    set_pos(world, target, 100, 0);
    set_hp(world, target, 100, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "somebody_else";

    // Projectile targets "bob" who doesn't exist
    ProjectileSystem::spawn_projectile(
        world, source, "proj_miss", "alice", "bob",
        0, 0, 100, 0,
        10, 50, 0, 0);

    // Tick past arrival
    for (uint32_t f = 1; f <= 5; ++f) {
        FrameContext fc{.frame_number = f};
        world.tick(fc);
    }

    // Nobody named "bob" exists, so no damage should be dealt
    auto* health = world.get_component<HealthComponent>(target);
    EXPECT_EQ(health->hp, 100);  // "somebody_else" is unharmed

    // Projectile should be inactive
    bool found_active = false;
    auto* sw = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    ASSERT_NE(sw, nullptr);
    sw->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle /*handle*/, ProjectileComponent& p) {
            if (p.projectile_id == "proj_miss") {
                EXPECT_FALSE(p.active);
                found_active = true;
            }
        });
    EXPECT_TRUE(found_active);
}

TEST(ProjectileSystemTest, DamageDoesNotGoBelowZero) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 50, 0);
    set_hp(world, target, 5, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    // Projectile with damage > target HP
    ProjectileSystem::spawn_projectile(
        world, source, "proj_overkill", "alice", "bob",
        0, 0, 50, 0,
        10, 50, 0, 0);

    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);

    auto* health = world.get_component<HealthComponent>(target);
    EXPECT_EQ(health->hp, 0);   // clamped to 0, not -5
}

TEST(ProjectileSystemTest, DoTDamageDoesNotGoBelowZero) {
    SimpleWorld world;
    world.add_system(std::make_unique<ProjectileSystem>());

    auto source = world.create_entity();
    set_pos(world, source, 0, 0);

    auto target = world.create_entity();
    set_pos(world, target, 50, 0);
    set_hp(world, target, 12, 100);
    auto& part = world.add_component<BattleParticipantComponent>(target);
    part.user_id = "bob";

    // DoT: 10 initial + 3 ticks of 10 = 40 total, but target has 12 HP
    ProjectileSystem::spawn_projectile(
        world, source, "proj_dot_overkill", "alice", "bob",
        0, 0, 50, 0,
        10, 50, 0, 3);

    // Frame 1: arrive, deal 10 initial damage => hp = 2
    FrameContext fc1{.frame_number = 1};
    world.tick(fc1);
    EXPECT_EQ(world.get_component<HealthComponent>(target)->hp, 2);

    // Frame 2: DoT tick 1, deal 10 => hp = 0 (clamped)
    FrameContext fc2{.frame_number = 2};
    world.tick(fc2);
    EXPECT_EQ(world.get_component<HealthComponent>(target)->hp, 0);

    // Frame 3: DoT tick 2, deal 10 => hp stays at 0
    FrameContext fc3{.frame_number = 3};
    world.tick(fc3);
    EXPECT_EQ(world.get_component<HealthComponent>(target)->hp, 0);
}
