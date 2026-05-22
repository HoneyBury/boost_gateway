#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <nlohmann/json.hpp>

namespace tank {

// ─── Constants ──────────────────────────────────────────────────────

inline constexpr int kMapWidth = 20;
inline constexpr int kMapHeight = 15;
inline constexpr int kMaxPlayers = 4;
inline constexpr int kInitialHp = 100;
inline constexpr int kBulletDamage = 25;
inline constexpr int kFireCooldownTicks = 3;
inline constexpr int kKillScore = 100;
inline constexpr int kDamageScorePerHp = 1;
inline constexpr int kSurvivalBonus = 50;
inline constexpr int kWinBonus = 200;

// ─── Direction ──────────────────────────────────────────────────────

enum class Direction : std::uint16_t {
    kUp = 0,
    kRight = 90,
    kDown = 180,
    kLeft = 270,
};

inline bool is_valid_direction(int d) {
    return d == 0 || d == 90 || d == 180 || d == 270;
}

inline void direction_to_delta(Direction dir, int& dx, int& dy) {
    dx = 0;
    dy = 0;
    switch (dir) {
        case Direction::kUp:    dy = -1; break;
        case Direction::kRight: dx = 1;  break;
        case Direction::kDown:  dy = 1;  break;
        case Direction::kLeft:  dx = -1; break;
    }
}

// ─── Position ───────────────────────────────────────────────────────

struct Position {
    int x = 0;
    int y = 0;

    bool operator==(const Position& o) const { return x == o.x && y == o.y; }
    bool operator!=(const Position& o) const { return !(*this == o); }
};

// ─── Action types ───────────────────────────────────────────────────

enum class ActionType : std::uint8_t {
    kMove = 0,
    kFire = 1,
    kStop = 2,
};

// ─── Tank state ─────────────────────────────────────────────────────

struct TankState {
    std::string user_id;
    Position pos;
    Direction direction = Direction::kUp;
    int hp = kInitialHp;
    bool alive = true;
    int fire_cooldown_remaining = 0;
    int kills = 0;
    int deaths = 0;
    int damage_dealt = 0;
    int score = 0;
};

// ─── Bullet state ───────────────────────────────────────────────────

struct BulletState {
    std::uint64_t id = 0;
    Position pos;
    int dx = 0;
    int dy = 0;
    std::string owner_user_id;
    bool active = true;
};

// ─── World event (for snapshot) ─────────────────────────────────────

struct WorldEvent {
    std::string type;         // "bullet_hit", "tank_destroyed", "tick"
    std::string actor;        // user_id or bullet_id
    std::string target;       // user_id
    int damage = 0;
    int frame = 0;
};

// ─── World snapshot ─────────────────────────────────────────────────

struct WorldSnapshot {
    int frame = 0;
    std::vector<TankState> tanks;
    std::vector<BulletState> bullets;
    std::vector<WorldEvent> events;
    bool finished = false;
    std::string finish_reason;
    std::string winner_user_id;

    nlohmann::json to_json() const;
};

// ─── Input action (from player) ─────────────────────────────────────

struct InputAction {
    ActionType type;
    int dx = 0;        // for move
    int dy = 0;
    int direction = 0; // for fire (0/90/180/270)
};

struct PlayerInput {
    std::string user_id;
    std::uint64_t seq = 0;
    std::vector<InputAction> actions;
};

// ─── Spawn point ────────────────────────────────────────────────────

struct SpawnPoint {
    int x;
    int y;
};

// ─── Deterministic Tank World ───────────────────────────────────────
//
// Pure simulation logic with no external dependencies.
// Same sequence of PlayerInputs produces identical WorldSnapshots.

class TankWorld {
public:
    TankWorld();

    // Initialise the world with players and optional map/wall layout.
    void init(const std::vector<std::string>& player_ids,
              int max_frames = 0,
              int map_width = kMapWidth,
              int map_height = kMapHeight);

    // Process a batch of player inputs and advance one tick.
    // Returns true if the world is still running, false if finished.
    bool tick(const std::vector<PlayerInput>& inputs);

    // Process a single player's input (called by tick).
    // Returns false if the action is rejected (anti-cheat).
    bool apply_action(const std::string& user_id, const InputAction& action);

    // ─── Anti-cheat ──────────────────────────────────────────────

    // Validate a move action before applying
    bool validate_move(const TankState& tank, int dx, int dy) const;

    // Validate a fire action before applying
    bool validate_fire(const TankState& tank) const;

    // ─── Queries ─────────────────────────────────────────────────

    [[nodiscard]] const WorldSnapshot& snapshot() const { return snapshot_; }
    [[nodiscard]] int frame() const { return current_frame_; }
    [[nodiscard]] bool is_finished() const { return finished_; }
    [[nodiscard]] const std::vector<TankState>& tanks() const { return tanks_; }
    [[nodiscard]] const std::vector<BulletState>& bullets() const { return bullets_; }

    // Find a tank by user_id (for anti-cheat / plugin queries)
    TankState* find_tank(const std::string& user_id);
    [[nodiscard]] const TankState* find_tank(const std::string& user_id) const;

    // Get a JSON settlement payload
    [[nodiscard]] nlohmann::json build_settlement() const;

private:
    int current_frame_ = 0;
    int max_frames_ = 0;
    int map_width_ = kMapWidth;
    int map_height_ = kMapHeight;
    bool finished_ = false;
    std::vector<TankState> tanks_;
    std::vector<BulletState> bullets_;
    std::vector<WorldEvent> pending_events_;
    WorldSnapshot snapshot_;
    std::uint64_t next_bullet_id_ = 1;
    std::vector<SpawnPoint> spawn_points_;
    std::vector<Position> walls_;

    // Internal methods
    void generate_walls();
    void assign_spawn_points(const std::vector<std::string>& player_ids);
    void move_bullets();
    void check_collisions();
    void check_finish_condition();
    bool is_wall_at(int x, int y) const;
    bool is_tank_at(int x, int y, const std::string& exclude_user = "") const;
    void build_snapshot();
    void clear_pending_events();
};

}  // namespace tank
