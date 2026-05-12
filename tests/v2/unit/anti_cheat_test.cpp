// v2.3.0: Anti-cheat tests — input validation, speed limits, cooldowns, damage bounds.

#include <gtest/gtest.h>

#include "v2/battle/input_validator.h"
#include "v2/battle/runtime_components.h"
#include "v2/battle/game_systems.h"
#include "v2/ecs/world.h"

#include <memory>

// ─── Input Validation ────────────────────────────────────────────────────

TEST(AntiCheatTest, ValidMoveInput) {
    v2::battle::InputValidator validator;
    auto result = validator.validate("move:100,200", 50, 150);
    EXPECT_TRUE(result.valid) << result.error;
    EXPECT_EQ(result.target_x, 100);
    EXPECT_EQ(result.target_y, 200);
}

TEST(AntiCheatTest, MoveOutOfBoundsRejected) {
    v2::battle::InputValidator validator;
    auto result = validator.validate("move:2000,500", 100, 100);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "move_out_of_bounds");
}

TEST(AntiCheatTest, MoveTooFastRejected) {
    v2::battle::InputValidator validator;
    // Current at (0,0), trying to jump to (500,500) in one frame (dist=1000)
    auto result = validator.validate("move:500,500", 0, 0);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "move_too_fast");
}

TEST(AntiCheatTest, InvalidMoveFormatRejected) {
    v2::battle::InputValidator validator;
    auto result = validator.validate("move:abc,def", 0, 0);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "invalid_move_coord_x");
}

TEST(AntiCheatTest, ValidAttackInput) {
    v2::battle::InputValidator validator;
    auto result = validator.validate("attack:bob", 0, 0);
    EXPECT_TRUE(result.valid);
    EXPECT_EQ(result.attack_target, "bob");
}

TEST(AntiCheatTest, UnknownInputTypeRejected) {
    v2::battle::InputValidator validator;
    auto result = validator.validate("garbage_input", 0, 0);
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "unknown_input_type");
}

TEST(AntiCheatTest, ValidFinishInput) {
    v2::battle::InputValidator validator;
    auto result = validator.validate("finish:surrender", 0, 0);
    EXPECT_TRUE(result.valid);
    EXPECT_EQ(result.finish_reason, "surrender");
}

// ─── Movement Speed Limits ───────────────────────────────────────────────

TEST(AntiCheatTest, NormalSpeedMoveAccepted) {
    v2::battle::InputValidator validator;
    // Move 50 units (well within 100 limit)
    EXPECT_TRUE(validator.is_move_within_bounds(0, 0, 50, 0));
    EXPECT_TRUE(validator.is_move_within_bounds(100, 100, 150, 100));
}

TEST(AntiCheatTest, TeleportMoveRejected) {
    v2::battle::InputValidator validator;
    // Move >200 units (exceeds 200 limit)
    EXPECT_FALSE(validator.is_move_within_bounds(0, 0, 100, 101));  // dist=201
    EXPECT_FALSE(validator.is_move_within_bounds(0, 0, 0, 250));    // dist=250
}

// ─── Attack Cooldown ─────────────────────────────────────────────────────

TEST(AntiCheatTest, AttackCooldownBlocksSecondAttack) {
    v2::battle::InputValidator validator;
    // Attack at frame 10, try again at frame 11 (cooldown=3)
    EXPECT_FALSE(validator.is_attack_allowed(11, 10));
    // Attack at frame 13 (cooldown=3, 13-10=3 >= 3)
    EXPECT_TRUE(validator.is_attack_allowed(13, 10));
}

TEST(AntiCheatTest, FirstAttackAlwaysAllowed) {
    v2::battle::InputValidator validator;
    EXPECT_TRUE(validator.is_attack_allowed(10, 0));  // last_attack_frame=0 means first attack
}

// ─── Damage Bounds ───────────────────────────────────────────────────────

TEST(AntiCheatTest, NormalDamageAccepted) {
    v2::battle::InputValidator validator;
    EXPECT_TRUE(validator.is_damage_valid(10));
    EXPECT_TRUE(validator.is_damage_valid(1));
    EXPECT_TRUE(validator.is_damage_valid(50));
}

TEST(AntiCheatTest, ExcessiveDamageRejected) {
    v2::battle::InputValidator validator;
    EXPECT_FALSE(validator.is_damage_valid(51));
    EXPECT_FALSE(validator.is_damage_valid(0));
    EXPECT_FALSE(validator.is_damage_valid(-1));
}

// ─── Cooldown Component ──────────────────────────────────────────────────

TEST(AntiCheatTest, CooldownComponentDefaults) {
    v2::battle::AttackCooldownComponent cooldown;
    EXPECT_EQ(cooldown.last_attack_frame, 0U);
    EXPECT_EQ(cooldown.cooldown_frames, 3U);
    EXPECT_EQ(cooldown.attacks_this_frame, 0U);
    EXPECT_EQ(v2::battle::AttackCooldownComponent::kMaxAttacksPerFrame, 1U);
}
