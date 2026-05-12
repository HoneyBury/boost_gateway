// v2.3.0: Anti-cheat hardened MovementSystem and CombatSystem.
// MovementSystem: speed limits + teleport detection (no silent clamping).
// CombatSystem: attack cooldown + damage bounds + attacks-per-frame limit.

#include "v2/battle/game_systems.h"

#include "v2/battle/runtime_components.h"
#include "v2/ecs/world.h"

#include <algorithm>
#include <cstdlib>
#include <string>

namespace v2::battle {

namespace {
constexpr std::int32_t kMaxX = 1000;
constexpr std::int32_t kMaxY = 1000;
constexpr std::int32_t kMaxMoveDelta = 200;  // max Manhattan distance per frame
constexpr std::int32_t kMaxDamage = 50;
constexpr std::int32_t kMinDamage = 1;
}  // namespace

void MovementSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    (void)ctx;
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) return;

    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& participant) {
            if (!participant.online || !participant.has_pending_move) return;

            auto* pos = simple_world->get_component<PositionComponent>(handle);
            if (pos == nullptr) return;

            auto target_x = participant.pending_move_x;
            auto target_y = participant.pending_move_y;

            // ── Position bounds check ──────────────────────────
            if (target_x < 0 || target_x > kMaxX ||
                target_y < 0 || target_y > kMaxY) {
                participant.has_pending_move = false;
                participant.pending_move_x = pos->x;  // reset
                participant.pending_move_y = pos->y;
                return;
            }

            // ── Speed limit / teleport detection ───────────────
            auto dx = std::abs(target_x - pos->x);
            auto dy = std::abs(target_y - pos->y);
            if ((dx + dy) > kMaxMoveDelta) {
                // Reject: move too fast (potential teleport cheat)
                participant.has_pending_move = false;
                participant.pending_move_x = pos->x;
                participant.pending_move_y = pos->y;
                return;
            }

            // ── Accept valid move ──────────────────────────────
            pos->x = target_x;
            pos->y = target_y;
            participant.has_pending_move = false;
        });
}

void CombatSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) return;

    auto current_frame = ctx.frame_number;

    // Collect attack intents (source entity → target user_id)
    struct AttackIntent {
        v2::ecs::EntityHandle source;
        std::string target_user_id;
    };
    std::vector<AttackIntent> intents;

    simple_world->for_each<BattleParticipantComponent>(
        [&](v2::ecs::EntityHandle handle, BattleParticipantComponent& participant) {
            if (!participant.online || participant.pending_target_user_id.empty()) return;

            auto* cooldown = simple_world->get_component<AttackCooldownComponent>(handle);

            // ── Attacks-per-frame limit ────────────────────────
            if (cooldown != nullptr) {
                if (cooldown->attacks_this_frame >=
                    AttackCooldownComponent::kMaxAttacksPerFrame) {
                    participant.pending_target_user_id.clear();
                    return;
                }

                // ── Cooldown check ─────────────────────────────
                if (cooldown->last_attack_frame > 0 &&
                    (current_frame - cooldown->last_attack_frame) <
                        cooldown->cooldown_frames) {
                    participant.pending_target_user_id.clear();
                    return;
                }

                cooldown->attacks_this_frame++;
            }

            intents.push_back({handle, participant.pending_target_user_id});
            participant.pending_target_user_id.clear();
        });

    // Resolve each attack intent
    for (const auto& intent : intents) {
        auto* source_pos = simple_world->get_component<PositionComponent>(intent.source);
        auto* source_attack = simple_world->get_component<AttackStateComponent>(intent.source);
        if (source_pos == nullptr || source_attack == nullptr) continue;

        // ── Damage bounds check ───────────────────────────────
        auto damage = source_attack->damage;
        if (damage < kMinDamage || damage > kMaxDamage) {
            continue;  // Reject: out-of-bounds damage (potential cheat)
        }

        // Find target entity by user_id
        simple_world->for_each<BattleParticipantComponent>(
            [&](v2::ecs::EntityHandle target_handle, BattleParticipantComponent& target_participant) {
                if (target_participant.user_id != intent.target_user_id) return;
                if (!target_participant.online) return;

                auto* target_pos = simple_world->get_component<PositionComponent>(target_handle);
                auto* target_health = simple_world->get_component<HealthComponent>(target_handle);
                if (target_pos == nullptr || target_health == nullptr) return;

                // Manhattan distance range check
                auto dx = std::abs(source_pos->x - target_pos->x);
                auto dy = std::abs(source_pos->y - target_pos->y);
                if (dx <= source_attack->range && dy <= source_attack->range) {
                    target_health->hp = std::max(
                        static_cast<std::int32_t>(0),
                        target_health->hp - damage);

                    // ── Record cooldown ────────────────────────
                    auto* cooldown = simple_world->get_component<
                        AttackCooldownComponent>(intent.source);
                    if (cooldown != nullptr) {
                        cooldown->last_attack_frame = current_frame;
                    }
                }
            });
    }
}

}  // namespace v2::battle
