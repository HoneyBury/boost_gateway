#include "tank_world.h"

#include <algorithm>
#include <cstdlib>
#include <sstream>

namespace tank {

// ─── JSON serialisation ─────────────────────────────────────────────

nlohmann::json tank_state_to_json(const TankState& t) {
    return {
        {"user_id", t.user_id},
        {"x", t.pos.x},
        {"y", t.pos.y},
        {"direction", static_cast<int>(t.direction)},
        {"hp", t.hp},
        {"alive", t.alive},
        {"kills", t.kills},
        {"deaths", t.deaths},
        {"score", t.score},
    };
}

nlohmann::json bullet_state_to_json(const BulletState& b) {
    return {
        {"id", b.id},
        {"x", b.pos.x},
        {"y", b.pos.y},
        {"dx", b.dx},
        {"dy", b.dy},
        {"owner", b.owner_user_id},
    };
}

nlohmann::json WorldSnapshot::to_json() const {
    nlohmann::json j;
    j["frame"] = frame;
    j["finished"] = finished;
    if (!finish_reason.empty()) j["finish_reason"] = finish_reason;
    if (!winner_user_id.empty()) j["winner_user_id"] = winner_user_id;

    nlohmann::json tanks_json = nlohmann::json::array();
    for (const auto& t : tanks) {
        tanks_json.push_back(tank_state_to_json(t));
    }
    j["tanks"] = std::move(tanks_json);

    nlohmann::json bullets_json = nlohmann::json::array();
    for (const auto& b : bullets) {
        if (b.active) {
            bullets_json.push_back(bullet_state_to_json(b));
        }
    }
    j["bullets"] = std::move(bullets_json);

    nlohmann::json events_json = nlohmann::json::array();
    for (const auto& e : events) {
        nlohmann::json ej;
        ej["type"] = e.type;
        ej["actor"] = e.actor;
        ej["target"] = e.target;
        ej["damage"] = e.damage;
        ej["frame"] = e.frame;
        events_json.push_back(std::move(ej));
    }
    j["events"] = std::move(events_json);

    return j;
}

// ─── TankWorld implementation ───────────────────────────────────────

TankWorld::TankWorld() = default;

void TankWorld::init(const std::vector<std::string>& player_ids,
                     int max_frames,
                     int map_width,
                     int map_height) {
    current_frame_ = 0;
    max_frames_ = max_frames;
    map_width_ = map_width;
    map_height_ = map_height;
    finished_ = false;
    tanks_.clear();
    bullets_.clear();
    pending_events_.clear();
    walls_.clear();
    spawn_points_.clear();
    next_bullet_id_ = 1;

    generate_walls();
    assign_spawn_points(player_ids);
    build_snapshot();
}

void TankWorld::generate_walls() {
    // Simple wall pattern: border walls + some internal walls
    for (int x = 0; x < map_width_; x++) {
        walls_.push_back({x, 0});             // top
        walls_.push_back({x, map_height_ - 1}); // bottom
    }
    for (int y = 0; y < map_height_; y++) {
        walls_.push_back({0, y});             // left
        walls_.push_back({map_width_ - 1, y}); // right
    }
    // Internal walls: a few columns
    for (int y = 3; y < map_height_ - 3; y++) {
        walls_.push_back({5, y});
        walls_.push_back({14, y});
    }
    for (int x = 3; x < map_width_ - 3; x++) {
        walls_.push_back({x, 5});
        walls_.push_back({x, 9});
    }
}

void TankWorld::assign_spawn_points(const std::vector<std::string>& player_ids) {
    // Spawn points
    std::vector<SpawnPoint> candidates = {
        {2, 2}, {map_width_ - 3, 2},
        {2, map_height_ - 3}, {map_width_ - 3, map_height_ - 3},
    };

    for (std::size_t i = 0; i < player_ids.size() && i < candidates.size(); i++) {
        const auto& sp = candidates[i];
        TankState tank;
        tank.user_id = player_ids[i];
        tank.pos = {sp.x, sp.y};
        tank.direction = Direction::kDown;
        tank.hp = kInitialHp;
        tank.alive = true;
        tank.fire_cooldown_remaining = 0;
        tank.kills = 0;
        tank.deaths = 0;
        tank.damage_dealt = 0;
        tank.score = 0;
        tanks_.push_back(std::move(tank));

        spawn_points_.push_back(sp);
    }
}

bool TankWorld::tick(const std::vector<PlayerInput>& inputs) {
    if (finished_) return false;

    current_frame_++;
    clear_pending_events();

    // Process inputs
    for (const auto& input : inputs) {
        for (const auto& action : input.actions) {
            apply_action(input.user_id, action);
        }
    }

    // Advance bullets
    move_bullets();

    // Check collisions
    check_collisions();

    // Decrease fire cooldowns
    for (auto& tank : tanks_) {
        if (tank.fire_cooldown_remaining > 0) {
            tank.fire_cooldown_remaining--;
        }
    }

    // Check finish condition
    check_finish_condition();

    // Build snapshot
    build_snapshot();

    return !finished_;
}

bool TankWorld::apply_action(const std::string& user_id, const InputAction& action) {
    auto* tank = find_tank(user_id);
    if (tank == nullptr || !tank->alive) return false;

    switch (action.type) {
        case ActionType::kMove: {
            if (!validate_move(*tank, action.dx, action.dy)) return false;

            int new_x = tank->pos.x + action.dx;
            int new_y = tank->pos.y + action.dy;

            // Update direction based on movement
            if (action.dx > 0) tank->direction = Direction::kRight;
            else if (action.dx < 0) tank->direction = Direction::kLeft;
            else if (action.dy > 0) tank->direction = Direction::kDown;
            else if (action.dy < 0) tank->direction = Direction::kUp;

            tank->pos = {new_x, new_y};
            return true;
        }
        case ActionType::kFire: {
            if (!validate_fire(*tank)) return false;

            int dx = 0, dy = 0;
            Direction dir = static_cast<Direction>(action.direction);
            if (!is_valid_direction(action.direction)) return false;
            direction_to_delta(dir, dx, dy);

            BulletState bullet;
            bullet.id = next_bullet_id_++;
            bullet.pos = {tank->pos.x + dx, tank->pos.y + dy};
            bullet.dx = dx;
            bullet.dy = dy;
            bullet.owner_user_id = user_id;
            bullet.active = true;
            bullets_.push_back(bullet);

            tank->fire_cooldown_remaining = kFireCooldownTicks;

            pending_events_.push_back({
                "bullet_fired", user_id,
                "bullet_" + std::to_string(bullet.id), 0, current_frame_
            });
            return true;
        }
        case ActionType::kStop:
            // No-op: just don't move
            return true;
    }
    return false;
}

bool TankWorld::validate_move(const TankState& tank, int dx, int dy) const {
    // Must be -1, 0, or 1
    if (std::abs(dx) > 1 || std::abs(dy) > 1) return false;
    // Can't move diagonally in one action
    if (dx != 0 && dy != 0) return false;
    // Can't stay still (use stop action instead)
    if (dx == 0 && dy == 0) return false;

    int new_x = tank.pos.x + dx;
    int new_y = tank.pos.y + dy;

    // Bounds check
    if (new_x < 0 || new_x >= map_width_ || new_y < 0 || new_y >= map_height_) return false;

    // Wall check
    if (is_wall_at(new_x, new_y)) return false;

    // Tank check (can't move through another tank)
    if (is_tank_at(new_x, new_y, tank.user_id)) return false;

    return true;
}

bool TankWorld::validate_fire(const TankState& tank) const {
    if (tank.fire_cooldown_remaining > 0) return false;
    return true;
}

void TankWorld::move_bullets() {
    for (auto& bullet : bullets_) {
        if (!bullet.active) continue;

        bullet.pos.x += bullet.dx;
        bullet.pos.y += bullet.dy;

        // Check if bullet went out of bounds
        if (bullet.pos.x < 0 || bullet.pos.x >= map_width_ ||
            bullet.pos.y < 0 || bullet.pos.y >= map_height_) {
            bullet.active = false;
            continue;
        }

        // Check if bullet hit a wall
        if (is_wall_at(bullet.pos.x, bullet.pos.y)) {
            bullet.active = false;
            pending_events_.push_back({
                "bullet_hit_wall", "bullet_" + std::to_string(bullet.id),
                "", 0, current_frame_
            });
        }
    }
}

void TankWorld::check_collisions() {
    for (auto& bullet : bullets_) {
        if (!bullet.active) continue;

        // Check if bullet hit a tank
        for (auto& tank : tanks_) {
            if (!tank.alive) continue;
            if (tank.user_id == bullet.owner_user_id) continue;  // no self-hit

            if (bullet.pos == tank.pos) {
                // Hit!
                tank.hp -= kBulletDamage;
                bullet.active = false;

                // Track damage
                auto* owner = find_tank(bullet.owner_user_id);
                if (owner) {
                    owner->damage_dealt += kBulletDamage;
                }

                pending_events_.push_back({
                    "bullet_hit", bullet.owner_user_id,
                    tank.user_id, kBulletDamage, current_frame_
                });

                if (tank.hp <= 0) {
                    tank.alive = false;
                    tank.deaths++;
                    if (owner) {
                        owner->kills++;
                    }
                    pending_events_.push_back({
                        "tank_destroyed", bullet.owner_user_id,
                        tank.user_id, 0, current_frame_
                    });
                }
                break;
            }
        }
    }
}

void TankWorld::check_finish_condition() {
    // Count alive tanks
    int alive_count = 0;
    std::string last_alive;
    for (const auto& tank : tanks_) {
        if (tank.alive) {
            alive_count++;
            last_alive = tank.user_id;
        }
    }

    // Last tank standing
    if (alive_count <= 1 && tanks_.size() >= 2) {
        finished_ = true;
        // Assign scores
        for (auto& tank : tanks_) {
            tank.score += tank.kills * kKillScore;
            tank.score += tank.damage_dealt * kDamageScorePerHp;
            if (tank.alive) {
                tank.score += kSurvivalBonus + kWinBonus;
            }
        }
        snapshot_.winner_user_id = last_alive;
        snapshot_.finish_reason = "last_standing";
        return;
    }

    // Frame limit reached
    if (max_frames_ > 0 && current_frame_ >= max_frames_) {
        finished_ = true;
        // Find player with most kills (or most damage as tiebreaker)
        int max_kills = -1;
        int max_damage = -1;
        for (const auto& tank : tanks_) {
            if (tank.kills > max_kills ||
                (tank.kills == max_kills && tank.damage_dealt > max_damage)) {
                max_kills = tank.kills;
                max_damage = tank.damage_dealt;
                snapshot_.winner_user_id = tank.user_id;
            }
        }
        for (auto& tank : tanks_) {
            tank.score += tank.kills * kKillScore;
            tank.score += tank.damage_dealt * kDamageScorePerHp;
            if (tank.alive) {
                tank.score += kSurvivalBonus;
            }
            if (tank.user_id == snapshot_.winner_user_id) {
                tank.score += kWinBonus;
            }
        }
        snapshot_.finish_reason = "time_limit";
    }
}

bool TankWorld::is_wall_at(int x, int y) const {
    for (const auto& w : walls_) {
        if (w.x == x && w.y == y) return true;
    }
    return false;
}

bool TankWorld::is_tank_at(int x, int y, const std::string& exclude_user) const {
    for (const auto& t : tanks_) {
        if (!t.alive) continue;
        if (t.user_id == exclude_user) continue;
        if (t.pos.x == x && t.pos.y == y) return true;
    }
    return false;
}

TankState* TankWorld::find_tank(const std::string& user_id) {
    for (auto& t : tanks_) {
        if (t.user_id == user_id) return &t;
    }
    return nullptr;
}

const TankState* TankWorld::find_tank(const std::string& user_id) const {
    for (const auto& t : tanks_) {
        if (t.user_id == user_id) return &t;
    }
    return nullptr;
}

void TankWorld::build_snapshot() {
    snapshot_.frame = current_frame_;
    snapshot_.finished = finished_;
    snapshot_.tanks = tanks_;
    snapshot_.bullets = bullets_;
    snapshot_.events = pending_events_;
}

void TankWorld::clear_pending_events() {
    pending_events_.clear();
}

nlohmann::json TankWorld::build_settlement() const {
    nlohmann::json players_json = nlohmann::json::array();
    for (const auto& t : tanks_) {
        players_json.push_back({
            {"user_id", t.user_id},
            {"kills", t.kills},
            {"deaths", t.deaths},
            {"damage", t.damage_dealt},
            {"score", t.score},
            {"win", t.user_id == snapshot_.winner_user_id},
        });
    }

    nlohmann::json j;
    j["reason"] = snapshot_.finish_reason;
    j["total_frames"] = current_frame_;
    j["players"] = std::move(players_json);
    if (!snapshot_.winner_user_id.empty()) {
        j["winner_user_id"] = snapshot_.winner_user_id;
    }
    return j;
}

}  // namespace tank
