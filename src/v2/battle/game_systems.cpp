// v2.3.0: Anti-cheat hardened MovementSystem and CombatSystem.
// MovementSystem: speed limits + teleport detection (no silent clamping).
// CombatSystem: attack cooldown + damage bounds + attacks-per-frame limit.

#include "v2/battle/game_systems.h"

#include "app/audit_log.h"
#include "v2/battle/runtime_components.h"
#include "v2/ecs/world.h"
#include "v2/security/anti_cheat.h"

#include <algorithm>
#include <cstdlib>
#include <string>
#include <unordered_map>
#include <vector>

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

            // v3.4.0: Statistical anomaly detection
            static thread_local std::unordered_map<std::string, std::vector<int>> player_speed_history;
            auto speed = dx + dy;
            auto& history = player_speed_history[participant.user_id];
            history.push_back(speed);
            if (history.size() > 100) history.erase(history.begin());
            static thread_local v2::security::AntiCheatManager ac_manager;
            if (ac_manager.detect_statistical_anomaly(history)) {
                for (auto& report : ac_manager.pending_reports()) {
                    AUDIT_LOG("cheat_speed_anomaly",
                               "player=" + report.player_id + " speed=" + std::to_string(speed));
                }
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
        static thread_local v2::security::AntiCheatManager ac_manager;
        if (!ac_manager.validate_damage(damage, kMinDamage, kMaxDamage)) {
            for (auto& report : ac_manager.pending_reports()) {
                AUDIT_LOG("cheat_damage", "player=<unknown> damage=" + std::to_string(damage));
            }
            continue;
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

// ─── ProjectileSystem ──────────────────────────────────────────────────────

ProjectileComponent& ProjectileSystem::spawn_projectile(
    v2::ecs::World& world,
    v2::ecs::EntityHandle source_entity,
    const std::string& projectile_id,
    const std::string& owner_user_id,
    const std::string& target_user_id,
    std::int32_t start_x, std::int32_t start_y,
    std::int32_t target_x, std::int32_t target_y,
    std::int32_t damage,
    std::int32_t speed,
    std::int32_t aoe_radius,
    std::uint32_t duration_frames) {
    (void)source_entity;

    auto entity = world.create_entity();
    {
        auto& pos = world.add_component<PositionComponent>(entity);
        pos.x = start_x;
        pos.y = start_y;
    }
    auto& proj = world.add_component<ProjectileComponent>(entity);
    proj.projectile_id = projectile_id;
    proj.owner_user_id = owner_user_id;
    proj.target_user_id = target_user_id;
    proj.start_x = start_x;
    proj.start_y = start_y;
    proj.target_x = target_x;
    proj.target_y = target_y;
    proj.damage = damage;
    proj.speed = speed;
    proj.aoe_radius = aoe_radius;
    proj.duration_frames = duration_frames;
    proj.current_frame = 0;
    proj.active = true;
    return proj;
}

void ProjectileSystem::run(v2::ecs::World& world, const v2::ecs::FrameContext& ctx) {
    auto* simple_world = dynamic_cast<v2::ecs::SimpleWorld*>(&world);
    if (simple_world == nullptr) return;

    // ── Phase 1: Process projectile movement and impacts ─────────────
    simple_world->for_each<ProjectileComponent>(
        [&](v2::ecs::EntityHandle handle, ProjectileComponent& proj) {
            if (!proj.active) return;

            proj.current_frame++;

            auto total_distance = static_cast<std::int64_t>(
                std::abs(proj.target_x - proj.start_x) +
                std::abs(proj.target_y - proj.start_y));

            bool arrived = (total_distance == 0) ||
                (static_cast<std::int64_t>(proj.current_frame) * proj.speed >= total_distance);

            if (arrived) {
                auto impact_x = proj.target_x;
                auto impact_y = proj.target_y;

                if (proj.aoe_radius == 0) {
                    // ── Single target ────────────────────────────────
                    if (!proj.target_user_id.empty()) {
                        simple_world->for_each<BattleParticipantComponent>(
                            [&](v2::ecs::EntityHandle target_handle,
                                BattleParticipantComponent& participant) {
                                if (participant.user_id != proj.target_user_id) return;

                                auto* health = simple_world->get_component<HealthComponent>(
                                    target_handle);
                                if (health == nullptr) return;

                                health->hp = std::max(
                                    static_cast<std::int32_t>(0),
                                    health->hp - proj.damage);

                                // Apply DoT if duration > 0
                                if (proj.duration_frames > 0) {
                                    auto& dot = world.add_component<DamageOverlayComponent>(
                                        target_handle);
                                    dot.source_projectile_id = proj.projectile_id;
                                    dot.damage_per_tick = proj.damage;
                                    dot.remaining_ticks = proj.duration_frames;
                                    dot.interval_frames = 1;
                                    dot.last_applied_frame = ctx.frame_number;
                                }
                            });
                    }
                } else {
                    // ── Area of effect ───────────────────────────────
                    simple_world->for_each<PositionComponent>(
                        [&](v2::ecs::EntityHandle target_handle, PositionComponent& pos) {
                            auto dx = std::abs(pos.x - impact_x);
                            auto dy = std::abs(pos.y - impact_y);
                            if ((dx + dy) > proj.aoe_radius) return;

                            auto* health = simple_world->get_component<HealthComponent>(
                                target_handle);
                            if (health == nullptr) return;

                            health->hp = std::max(
                                static_cast<std::int32_t>(0),
                                health->hp - proj.damage);

                            // Apply DoT if duration > 0
                            if (proj.duration_frames > 0) {
                                auto& dot = world.add_component<DamageOverlayComponent>(
                                    target_handle);
                                dot.source_projectile_id = proj.projectile_id;
                                dot.damage_per_tick = proj.damage;
                                dot.remaining_ticks = proj.duration_frames;
                                dot.interval_frames = 1;
                                dot.last_applied_frame = ctx.frame_number;
                            }
                        });
                }

                proj.active = false;
            } else {
                // ── Interpolate position during flight ───────────────
                double progress = std::min(
                    1.0,
                    static_cast<double>(static_cast<std::int64_t>(proj.current_frame) *
                                        proj.speed) /
                        static_cast<double>(total_distance));

                auto* pos = simple_world->get_component<PositionComponent>(handle);
                if (pos != nullptr) {
                    pos->x = static_cast<std::int32_t>(
                        static_cast<double>(proj.start_x) +
                        (static_cast<double>(proj.target_x - proj.start_x) * progress));
                    pos->y = static_cast<std::int32_t>(
                        static_cast<double>(proj.start_y) +
                        (static_cast<double>(proj.target_y - proj.start_y) * progress));
                }
            }
        });

    // ── Phase 2: Process damage-over-time ticks ──────────────────────
    std::vector<v2::ecs::EntityHandle> expired_dots;

    simple_world->for_each<DamageOverlayComponent>(
        [&](v2::ecs::EntityHandle handle, DamageOverlayComponent& dot) {
            if (dot.remaining_ticks == 0) {
                expired_dots.push_back(handle);
                return;
            }

            if (ctx.frame_number - dot.last_applied_frame >= dot.interval_frames) {
                auto* health = simple_world->get_component<HealthComponent>(handle);
                if (health != nullptr) {
                    health->hp = std::max(
                        static_cast<std::int32_t>(0),
                        health->hp - dot.damage_per_tick);
                }
                dot.remaining_ticks--;
                dot.last_applied_frame = ctx.frame_number;
            }
        });

    for (const auto& expired : expired_dots) {
        world.remove_component<DamageOverlayComponent>(expired);
    }
}

}  // namespace v2::battle
