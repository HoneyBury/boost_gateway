// v2.5.0: BattleLifecycleSystem unit tests
//
// Tests for lifecycle state transitions, idle/offline timeouts, and
// cleanup on finished battles.

#include <gtest/gtest.h>

#include "v2/battle/runtime_components.h"
#include "v2/battle/runtime_world.h"

#include <memory>

namespace v2_battle_lifecycle_test {

using namespace v2::battle;
using v2::ecs::FrameContext;
using v2::ecs::SimpleWorld;

// ─── Auto-transition ───────────────────────────────────────────────

TEST(V2BattleLifecycleTest, AutoTransitionFromCreatedToRunning) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    const auto entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(entity);
    meta.lifecycle = BattleLifecycleState::kCreated;

    world->tick(FrameContext{
        .battle_id = "b1",
        .room_id = "r1",
        .frame_number = 1,
    });

    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);
}

// ─── Stays running during normal operation ─────────────────────────

TEST(V2BattleLifecycleTest, StaysRunningDuringNormalOperation) {
    auto world = create_battle_world("b1", "r1", {"alice"}, 100);

    for (std::uint32_t f = 1; f <= 10; ++f) {
        battle_world_process_input(*world, "alice", "move:1,1", 0, f);
        battle_world_advance_frame(*world, f, "tick");
    }

    EXPECT_EQ(battle_world_lifecycle(*world), BattleLifecycleState::kRunning);
}

// ─── Idle timeout ──────────────────────────────────────────────────

TEST(V2BattleLifecycleTest, DetectsIdleTimeout) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>(5, 60));

    const auto entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(entity);
    meta.lifecycle = BattleLifecycleState::kRunning;
    meta.next_input_seq = 1;

    // Tick 5 times: idle_frames_ = 4 still < 5
    for (std::uint32_t f = 1; f <= 5; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // 6th tick: idle_frames_ = 5 >= 5 -> kFinished
    world->tick(FrameContext{.frame_number = 6});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kFinished);
}

// ─── All-players-offline timeout ──────────────────────────────────

TEST(V2BattleLifecycleTest, DetectsAllPlayersOffline) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>(300, 3));

    const auto meta_entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(meta_entity);
    meta.lifecycle = BattleLifecycleState::kRunning;
    meta.next_input_seq = 1;

    const auto p1 = world->create_entity();
    auto& p1c = world->add_component<BattleParticipantComponent>(p1);
    p1c.online = false;

    const auto p2 = world->create_entity();
    auto& p2c = world->add_component<BattleParticipantComponent>(p2);
    p2c.online = false;

    // Tick 2 times: offline_frames_ = 2 < 3
    for (std::uint32_t f = 1; f <= 2; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // 3rd tick: offline_frames_ = 3 >= 3 -> kFinished
    world->tick(FrameContext{.frame_number = 3});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kFinished);
}

// ─── Cleanup on finished ──────────────────────────────────────────

TEST(V2BattleLifecycleTest, CleanupOnFinished) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    const auto meta_entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(meta_entity);
    meta.lifecycle = BattleLifecycleState::kFinished;

    const auto proj_entity = world->create_entity();
    auto& proj = world->add_component<ProjectileComponent>(proj_entity);
    proj.projectile_id = "proj_1";

    const auto dot_entity = world->create_entity();
    world->add_component<DamageOverlayComponent>(dot_entity);

    // Verify components exist before tick
    EXPECT_NE(world->get_component<ProjectileComponent>(proj_entity), nullptr);
    EXPECT_NE(world->get_component<DamageOverlayComponent>(dot_entity), nullptr);

    // Tick -> cleanup
    world->tick(FrameContext{});

    // Verify components are removed
    EXPECT_EQ(world->get_component<ProjectileComponent>(proj_entity), nullptr);
    EXPECT_EQ(world->get_component<DamageOverlayComponent>(dot_entity), nullptr);
}

// ─── No crash with empty world ────────────────────────────────────

TEST(V2BattleLifecycleTest, EmptyWorldNoCrash) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    // No entities or components at all
    world->tick(FrameContext{});
    // Should not crash
    SUCCEED();
}

// ─── No crash when world has no BattleMetadataComponent ───────────

TEST(V2BattleLifecycleTest, NoMetadataNoCrash) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>());

    // Add an entity with a non-metadata component
    const auto entity = world->create_entity();
    world->add_component<BattleParticipantComponent>(entity);

    world->tick(FrameContext{});
    SUCCEED();
}

// ─── Idle counter resets when inputs arrive ───────────────────────

TEST(V2BattleLifecycleTest, IdleCounterResetsOnInput) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<BattleLifecycleSystem>(5, 60));

    const auto entity = world->create_entity();
    auto& meta = world->add_component<BattleMetadataComponent>(entity);
    meta.lifecycle = BattleLifecycleState::kRunning;
    meta.next_input_seq = 1;

    // Tick 3 times without input: idle_frames_ becomes 2 (tick 1 has
    // has_input=true from initial seq 0->1, ticks 2-3 accumulate)
    for (std::uint32_t f = 1; f <= 3; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // Simulate input arriving
    meta.next_input_seq = 2;

    // Tick: input detected, idle_frames_ resets to 0
    world->tick(FrameContext{.frame_number = 4});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // Tick 4 more times without input: idle_frames_ = 4 < 5
    for (std::uint32_t f = 5; f <= 8; ++f) {
        world->tick(FrameContext{.frame_number = f});
    }
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kRunning);

    // One more tick: idle_frames_ = 5 >= 5 -> kFinished
    world->tick(FrameContext{.frame_number = 9});
    EXPECT_EQ(meta.lifecycle, BattleLifecycleState::kFinished);
}

}  // namespace v2_battle_lifecycle_test
