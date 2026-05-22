// v3.6.0: ECS-integrated AOI system tests
#include <gtest/gtest.h>

#include <algorithm>
#include <memory>

#include "v2/aoi/aoi_system.h"
#include "v2/battle/runtime_components.h"
#include "v2/ecs/world.h"

using v2::ecs::EntityHandle;
using v2::ecs::FrameContext;
using v2::ecs::SimpleWorld;
using v2::aoi::AoiSystem;
using v2::aoi::AoiViewComponent;
using v2::battle::BattleParticipantComponent;
using v2::battle::PositionComponent;

namespace {

// ─── Helpers ──────────────────────────────────────────────────────────

EntityHandle CreatePlayer(SimpleWorld& world, std::int32_t x, std::int32_t y) {
    auto e = world.create_entity();
    auto& pos = world.add_component<PositionComponent>(e);
    pos.x = x;
    pos.y = y;
    world.add_component<BattleParticipantComponent>(e);
    return e;
}

EntityHandle CreateOfflinePlayer(SimpleWorld& world, std::int32_t x, std::int32_t y) {
    auto e = world.create_entity();
    auto& pos = world.add_component<PositionComponent>(e);
    pos.x = x;
    pos.y = y;
    auto& p = world.add_component<BattleParticipantComponent>(e);
    p.online = false;
    return e;
}

EntityHandle CreatePositionOnly(SimpleWorld& world, std::int32_t x, std::int32_t y) {
    auto e = world.create_entity();
    auto& pos = world.add_component<PositionComponent>(e);
    pos.x = x;
    pos.y = y;
    return e;
}

void Tick(SimpleWorld& world, std::uint32_t frame = 1) {
    FrameContext ctx;
    ctx.frame_number = frame;
    world.tick(ctx);
}

bool ContainsId(const std::vector<v2::ecs::EntityId>& entities, v2::ecs::EntityId id) {
    return std::find(entities.begin(), entities.end(), id) != entities.end();
}

}  // anonymous namespace

// ─── Visibility: entities in range ──────────────────────────────────

TEST(V2AoiSystemTest, EntitiesInRangeAreVisible) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 150));

    auto e1 = CreatePlayer(*world, 50, 50);
    auto e2 = CreatePlayer(*world, 100, 100);

    Tick(*world);

    auto* v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_EQ(v1->visible_count, 2U);
    EXPECT_TRUE(ContainsId(v1->visible_entities, e1.id));
    EXPECT_TRUE(ContainsId(v1->visible_entities, e2.id));

    auto* v2 = world->get_component<AoiViewComponent>(e2);
    ASSERT_NE(v2, nullptr);
    EXPECT_EQ(v2->visible_count, 2U);
    EXPECT_TRUE(ContainsId(v2->visible_entities, e1.id));
    EXPECT_TRUE(ContainsId(v2->visible_entities, e2.id));
}

// ─── Visibility: entities out of range ──────────────────────────────

TEST(V2AoiSystemTest, EntitiesOutOfRangeNotVisible) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    auto e1 = CreatePlayer(*world, 50, 50);
    auto e2 = CreatePlayer(*world, 900, 900);

    Tick(*world);

    auto* v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_EQ(v1->visible_count, 1U);
    EXPECT_TRUE(ContainsId(v1->visible_entities, e1.id));
    EXPECT_FALSE(ContainsId(v1->visible_entities, e2.id));

    auto* v2 = world->get_component<AoiViewComponent>(e2);
    ASSERT_NE(v2, nullptr);
    EXPECT_EQ(v2->visible_count, 1U);
    EXPECT_TRUE(ContainsId(v2->visible_entities, e2.id));
    EXPECT_FALSE(ContainsId(v2->visible_entities, e1.id));
}

// ─── Offline participant handling ──────────────────────────────────

TEST(V2AoiSystemTest, OfflineParticipantNotIncluded) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 150));

    auto e1 = CreatePlayer(*world, 50, 50);             // online
    auto e2 = CreateOfflinePlayer(*world, 100, 100);    // offline

    Tick(*world);

    // Online participant can see the offline one (it is in the grid)
    auto* v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_TRUE(ContainsId(v1->visible_entities, e2.id));

    // Offline participant never gets its AoiViewComponent populated
    auto* v2 = world->get_component<AoiViewComponent>(e2);
    EXPECT_EQ(v2, nullptr);
}

// ─── AoiViewComponent creation on first tick ────────────────────────

TEST(V2AoiSystemTest, ViewComponentCreatedOnFirstTick) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    auto e1 = CreatePlayer(*world, 50, 50);

    // No AoiViewComponent before any tick
    EXPECT_EQ(world->get_component<AoiViewComponent>(e1), nullptr);

    Tick(*world);

    // AoiViewComponent exists and is populated after the first tick
    auto* view = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(view, nullptr);
    EXPECT_EQ(view->visible_count, 1U);
    EXPECT_TRUE(ContainsId(view->visible_entities, e1.id));
}

// ─── Movement-driven visibility changes ─────────────────────────────

TEST(V2AoiSystemTest, MovementUpdatesVisibility) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    auto e1 = CreatePlayer(*world, 50, 50);
    auto e2 = CreatePlayer(*world, 900, 900);

    Tick(*world);

    // Before move: e2 is far away
    auto* v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_EQ(v1->visible_count, 1U);
    EXPECT_FALSE(ContainsId(v1->visible_entities, e2.id));

    // Move e2 to within view radius of e1
    auto* pos2 = world->get_component<PositionComponent>(e2);
    ASSERT_NE(pos2, nullptr);
    pos2->x = 100;
    pos2->y = 100;

    Tick(*world, 2);

    // After move: e1 now sees e2
    v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_EQ(v1->visible_count, 2U);
    EXPECT_TRUE(ContainsId(v1->visible_entities, e2.id));
}

// ─── Many entities: cluster + outlier ───────────────────────────────

TEST(V2AoiSystemTest, MultipleEntitiesVisibility) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    // 5 entities clustered near the centre of the world
    auto e1 = CreatePlayer(*world, 50, 50);
    auto e2 = CreatePlayer(*world, 80, 80);
    auto e3 = CreatePlayer(*world, 120, 120);
    auto e4 = CreatePlayer(*world, 60, 90);
    auto e5 = CreatePlayer(*world, 110, 70);
    // 1 entity far away
    auto e6 = CreatePlayer(*world, 900, 900);

    Tick(*world);

    auto check_near = [&](EntityHandle handle) {
        auto* v = world->get_component<AoiViewComponent>(handle);
        ASSERT_NE(v, nullptr);
        EXPECT_EQ(v->visible_count, 5U);
        EXPECT_TRUE(ContainsId(v->visible_entities, e1.id));
        EXPECT_TRUE(ContainsId(v->visible_entities, e2.id));
        EXPECT_TRUE(ContainsId(v->visible_entities, e3.id));
        EXPECT_TRUE(ContainsId(v->visible_entities, e4.id));
        EXPECT_TRUE(ContainsId(v->visible_entities, e5.id));
        EXPECT_FALSE(ContainsId(v->visible_entities, e6.id));
    };

    check_near(e1);
    check_near(e2);
    check_near(e3);
    check_near(e4);
    check_near(e5);

    // Far entity sees only itself
    auto* v6 = world->get_component<AoiViewComponent>(e6);
    ASSERT_NE(v6, nullptr);
    EXPECT_EQ(v6->visible_count, 1U);
    EXPECT_TRUE(ContainsId(v6->visible_entities, e6.id));
}

// ─── Edge cases: empty / no-participant worlds ──────────────────────

TEST(V2AoiSystemTest, EmptyWorldNoCrash) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    EXPECT_NO_THROW(Tick(*world));
}

TEST(V2AoiSystemTest, WorldWithoutParticipantsNoCrash) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    auto e1 = CreatePositionOnly(*world, 50, 50);
    auto e2 = CreatePositionOnly(*world, 100, 100);

    EXPECT_NO_THROW(Tick(*world));

    // No BattleParticipantComponent means no AoiViewComponent should exist
    EXPECT_EQ(world->get_component<AoiViewComponent>(e1), nullptr);
    EXPECT_EQ(world->get_component<AoiViewComponent>(e2), nullptr);
}

// ─── Configurable view radius ──────────────────────────────────────

TEST(V2AoiSystemTest, ViewRadiusConfigurable) {
    // Small radius: entities outside it are not visible
    {
        auto world = std::make_unique<SimpleWorld>();
        world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 30));

        auto e1 = CreatePlayer(*world, 50, 50);
        auto e2 = CreatePlayer(*world, 250, 50);

        Tick(*world);

        auto* v1 = world->get_component<AoiViewComponent>(e1);
        ASSERT_NE(v1, nullptr);
        EXPECT_EQ(v1->visible_count, 1U);
        EXPECT_FALSE(ContainsId(v1->visible_entities, e2.id));
    }

    // Larger radius: the same pair becomes visible to each other
    {
        auto world = std::make_unique<SimpleWorld>();
        world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 200));

        auto e1 = CreatePlayer(*world, 50, 50);
        auto e2 = CreatePlayer(*world, 250, 50);

        Tick(*world);

        auto* v1 = world->get_component<AoiViewComponent>(e1);
        ASSERT_NE(v1, nullptr);
        EXPECT_EQ(v1->visible_count, 2U);
        EXPECT_TRUE(ContainsId(v1->visible_entities, e2.id));
    }
}

// ─── Per-tick view rebuild: stale entries vanish ───────────────────

TEST(V2AoiSystemTest, AoiViewComponentClearedEachTick) {
    auto world = std::make_unique<SimpleWorld>();
    world->add_system(std::make_unique<AoiSystem>(1000, 1000, 100, 100));

    auto e1 = CreatePlayer(*world, 50, 50);
    auto e2 = CreatePlayer(*world, 100, 100);

    // Tick 1: both see each other
    Tick(*world, 1);

    auto* v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_EQ(v1->visible_count, 2U);
    EXPECT_TRUE(ContainsId(v1->visible_entities, e2.id));

    auto* v2 = world->get_component<AoiViewComponent>(e2);
    ASSERT_NE(v2, nullptr);
    EXPECT_EQ(v2->visible_count, 2U);
    EXPECT_TRUE(ContainsId(v2->visible_entities, e1.id));

    // Move e2 far away
    auto* pos2 = world->get_component<PositionComponent>(e2);
    ASSERT_NE(pos2, nullptr);
    pos2->x = 900;
    pos2->y = 900;

    // Tick 2: e1's view is rebuilt, e2 is no longer visible
    Tick(*world, 2);

    v1 = world->get_component<AoiViewComponent>(e1);
    ASSERT_NE(v1, nullptr);
    EXPECT_EQ(v1->visible_count, 1U);
    EXPECT_FALSE(ContainsId(v1->visible_entities, e2.id));
    EXPECT_TRUE(ContainsId(v1->visible_entities, e1.id));
}
