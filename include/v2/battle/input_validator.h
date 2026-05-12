#pragma once
// v2.3.0 Anti-cheat: server-authoritative input validation.
// Validates battle input format, value ranges, and business rules
// before accepting client input into the authoritative simulation.

#include <cstdint>
#include <string>
#include <string_view>

namespace v2::battle {

struct InputValidationResult {
    bool valid = false;
    std::string error;  // reject_reason for invalid input

    // Move coordinates extracted from "move:x,y" format
    std::int32_t target_x = 0;
    std::int32_t target_y = 0;

    // Attack target extracted from "attack:target" format
    std::string attack_target;

    // Finish reason extracted from "finish:reason" format
    std::string finish_reason;
};

// ── Configuration ───────────────────────────────────────────────────────

struct InputValidatorConfig {
    // Move bounds
    std::int32_t min_pos = 0;
    std::int32_t max_pos = 1000;

    // Maximum distance a player can move in a single frame (Euclidean)
    std::int32_t max_move_delta = 200;

    // Attack bounds
    std::int32_t max_damage = 50;
    std::int32_t min_damage = 1;

    // Cooldown: minimum frames between attacks
    std::uint32_t attack_cooldown_frames = 3;
};

// ── Validator ────────────────────────────────────────────────────────────

class InputValidator {
public:
    explicit InputValidator(InputValidatorConfig config = {})
        : config_(config) {}

    /// Validate a battle input data string.
    /// Supported formats:
    ///   "move:123,456"  — move to position (x,y)
    ///   "attack:target" — attack a player
    ///   "finish:surrender" / "finish:timeout" — end battle
    [[nodiscard]] InputValidationResult validate(
        std::string_view input_data,
        std::int32_t current_x, std::int32_t current_y) const;

    /// Check if a move is within speed limits.
    [[nodiscard]] bool is_move_within_bounds(
        std::int32_t from_x, std::int32_t from_y,
        std::int32_t to_x, std::int32_t to_y) const;

    /// Check if damage is within allowed range.
    [[nodiscard]] bool is_damage_valid(std::int32_t damage) const {
        return damage >= config_.min_damage && damage <= config_.max_damage;
    }

    /// Check if enough frames have passed since last attack.
    [[nodiscard]] bool is_attack_allowed(
        std::uint32_t current_frame, std::uint32_t last_attack_frame) const {
        return (current_frame - last_attack_frame) >= config_.attack_cooldown_frames;
    }

    [[nodiscard]] const InputValidatorConfig& config() const { return config_; }

private:
    InputValidatorConfig config_;
};

// ── Implementation ───────────────────────────────────────────────────────

inline InputValidationResult InputValidator::validate(
    std::string_view input_data,
    std::int32_t current_x, std::int32_t current_y) const {

    InputValidationResult result;

    if (input_data.empty()) {
        result.error = "empty_input";
        return result;
    }

    // Parse "move:x,y"
    if (input_data.starts_with("move:")) {
        auto coords = input_data.substr(5);
        auto comma = coords.find(',');
        if (comma == std::string_view::npos || comma == 0 ||
            comma == coords.size() - 1) {
            result.error = "invalid_move_format";
            return result;
        }

        char* end = nullptr;
        auto x_str = std::string(coords.substr(0, comma));
        auto y_str = std::string(coords.substr(comma + 1));
        auto x = std::strtol(x_str.c_str(), &end, 10);
        if (end != x_str.c_str() + x_str.size()) {
            result.error = "invalid_move_coord_x";
            return result;
        }
        auto y = std::strtol(y_str.c_str(), &end, 10);
        if (end != y_str.c_str() + y_str.size()) {
            result.error = "invalid_move_coord_y";
            return result;
        }

        // Position bounds
        if (x < config_.min_pos || x > config_.max_pos ||
            y < config_.min_pos || y > config_.max_pos) {
            result.error = "move_out_of_bounds";
            return result;
        }

        // Speed check (teleport detection)
        if (!is_move_within_bounds(current_x, current_y,
                                    static_cast<std::int32_t>(x),
                                    static_cast<std::int32_t>(y))) {
            result.error = "move_too_fast";
            return result;
        }

        result.valid = true;
        result.target_x = static_cast<std::int32_t>(x);
        result.target_y = static_cast<std::int32_t>(y);
        return result;
    }

    // Parse "attack:target"
    if (input_data.starts_with("attack:")) {
        auto target = input_data.substr(7);
        if (target.empty()) {
            result.error = "invalid_attack_target";
            return result;
        }
        result.valid = true;
        result.attack_target = std::string(target);
        return result;
    }

    // Parse "finish:reason"
    if (input_data.starts_with("finish:")) {
        auto reason = input_data.substr(7);
        if (reason.empty()) {
            result.error = "invalid_finish_reason";
            return result;
        }
        result.valid = true;
        result.finish_reason = std::string(reason);
        return result;
    }

    result.error = "unknown_input_type";
    return result;
}

inline bool InputValidator::is_move_within_bounds(
    std::int32_t from_x, std::int32_t from_y,
    std::int32_t to_x, std::int32_t to_y) const {
    auto dx = to_x > from_x ? to_x - from_x : from_x - to_x;
    auto dy = to_y > from_y ? to_y - from_y : from_y - to_y;
    // Manhattan distance check (fast, conservative)
    return (dx + dy) <= config_.max_move_delta;
}

}  // namespace v2::battle
