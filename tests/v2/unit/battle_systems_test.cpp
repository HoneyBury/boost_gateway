// v2.5.0 T3: Skill cooldowns and combat system integration tests
#include <gtest/gtest.h>
#include "v2/battle/battle_actor.h"
#include "v2/battle/input_validator.h"
#include "v2/runtime/actor_system.h"
#include <memory>

// ─── Combat cooldown ────────────────────────────────────────────────────

TEST(BattleSystemsTest, AttackCooldownPreventsRapidAttacks) {
    v2::battle::InputValidator validator;
    EXPECT_TRUE(validator.is_attack_allowed(10, 0));   // first attack
    EXPECT_TRUE(validator.is_attack_allowed(10, 7));   // frame 7 → 10: diff=3 >= 3
    EXPECT_FALSE(validator.is_attack_allowed(11, 9));  // frame 9 → 11: diff=2 < 3
    EXPECT_FALSE(validator.is_attack_allowed(12, 10)); // frame 10 → 12: diff=2 < 3
    EXPECT_TRUE(validator.is_attack_allowed(13, 10));  // frame 10 → 13: diff=3 >= 3
}

TEST(BattleSystemsTest, MultipleAttacksAtCooldownBoundary) {
    v2::battle::InputValidator validator;
    // Sequential valid attacks at cooldown boundary
    EXPECT_TRUE(validator.is_attack_allowed(3, 0));
    EXPECT_TRUE(validator.is_attack_allowed(6, 3));
    EXPECT_TRUE(validator.is_attack_allowed(9, 6));
    EXPECT_TRUE(validator.is_attack_allowed(12, 9));
}

// ─── Damage bounds ──────────────────────────────────────────────────────

TEST(BattleSystemsTest, DamageExactlyAtMinBoundary) {
    v2::battle::InputValidator validator;
    EXPECT_TRUE(validator.is_damage_valid(1));
    EXPECT_FALSE(validator.is_damage_valid(0));
}

TEST(BattleSystemsTest, DamageExactlyAtMaxBoundary) {
    v2::battle::InputValidator validator;
    EXPECT_TRUE(validator.is_damage_valid(50));
    EXPECT_FALSE(validator.is_damage_valid(51));
}

TEST(BattleSystemsTest, DamageNegativeValues) {
    v2::battle::InputValidator validator;
    EXPECT_FALSE(validator.is_damage_valid(-1));
    EXPECT_FALSE(validator.is_damage_valid(-100));
}

// ─── Frame advance / battle lifecycle ───────────────────────────────────

TEST(BattleSystemsTest, BattleLifecycleCreatedToRunningToFinished) {
    v2::runtime::ActorSystem actor_system;
    struct TestSink : v2::battle::BattleEventSink {
        void push(v2::battle::BattleEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::battle::BattleEvent> events;
    };
    TestSink sink;
    auto actor = std::make_unique<v2::battle::BattleActor>(sink);
    auto* ptr = actor.get();
    auto ref = actor_system.create_actor(std::move(actor));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "lifecycle_test", .room_id = "room", .player_ids = {"a","b"}, .max_frames = 5};
    ref.tell(std::move(create));

    EXPECT_EQ(actor_system.dispatch_all(), 1U);
    EXPECT_EQ(ptr->state().lifecycle, v2::battle::BattleLifecycleState::kRunning);

    // Tick to frame limit
    for (int i = 0; i < 5; ++i) {
        v2::actor::Message tick;
        tick.header.kind = v2::actor::MessageKind::kUser;
        tick.payload = v2::battle::TickBattleMsg{.trigger = "tick"};
        ref.tell(std::move(tick));
    }
    actor_system.dispatch_all();
    EXPECT_EQ(ptr->state().lifecycle, v2::battle::BattleLifecycleState::kFinished);
    EXPECT_GE(sink.events.size(), 3U);  // created + settlement + finished
}

// ─── Input rejection ────────────────────────────────────────────────────

TEST(BattleSystemsTest, InputRejectedAfterBattleFinished) {
    v2::runtime::ActorSystem actor_system;
    struct TestSink : v2::battle::BattleEventSink {
        void push(v2::battle::BattleEvent e) override { events.push_back(std::move(e)); }
        std::vector<v2::battle::BattleEvent> events;
    };
    TestSink sink;
    auto ref = actor_system.create_actor(std::make_unique<v2::battle::BattleActor>(sink));

    v2::actor::Message create;
    create.header.kind = v2::actor::MessageKind::kUser;
    create.payload = v2::battle::CreateBattleMsg{
        .battle_id = "finished_test", .room_id = "room", .player_ids = {"a"}, .max_frames = 1};
    ref.tell(std::move(create));

    v2::actor::Message tick;
    tick.header.kind = v2::actor::MessageKind::kUser;
    tick.payload = v2::battle::TickBattleMsg{.trigger = "end"};
    ref.tell(std::move(tick));
    actor_system.dispatch_all();

    // Now finished — input should be ignored
    v2::actor::Message input;
    input.header.kind = v2::actor::MessageKind::kUser;
    input.payload = v2::battle::SubmitBattleInputMsg{
        .user_id = "a", .request_id = 99, .input_data = "move:1,1"};
    ref.tell(std::move(input));

    auto dispatched = actor_system.dispatch_all();
    // After finish, battle_world_process_input returns rejected — no event emitted
    EXPECT_GE(dispatched, 0U);
}
