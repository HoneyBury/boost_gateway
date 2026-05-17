#pragma once

#include "v2/battle/message_types.h"

#include <optional>
#include <string>
#include <string_view>

namespace v2::gateway {

struct ParsedLoginCommandBody {
    std::string user_id;
    std::string token;
    std::optional<std::string> display_name;
};

[[nodiscard]] std::optional<ParsedLoginCommandBody> parse_login_command_body(std::string_view body);
[[nodiscard]] bool validate_login_command_body(const ParsedLoginCommandBody& body) noexcept;

[[nodiscard]] std::optional<std::string> parse_room_id_body(std::string_view body);
[[nodiscard]] bool validate_room_id_body(std::string_view body) noexcept;

[[nodiscard]] std::optional<bool> parse_room_ready_body(std::string_view body) noexcept;

struct ParsedBattleStartCommandBody {
    std::optional<std::string> room_id;
};

[[nodiscard]] std::optional<ParsedBattleStartCommandBody> parse_battle_start_command_body(std::string_view body);
[[nodiscard]] bool validate_battle_start_command_body(const ParsedBattleStartCommandBody& body) noexcept;

struct ParsedBattleInputCommandBody {
    bool is_finish_request = false;
    v2::battle::BattleFinishReason finish_reason = v2::battle::BattleFinishReason::kFinished;
    std::int64_t score = 0;
    std::string input_data;
};

[[nodiscard]] std::optional<ParsedBattleInputCommandBody> parse_battle_input_command_body(std::string_view body);
[[nodiscard]] bool validate_battle_input_command_body(const ParsedBattleInputCommandBody& body) noexcept;

struct ParsedMatchCommandBody {
    std::string user_id;
    std::int64_t mmr = 1000;
    std::string mode = "1v1";
};

[[nodiscard]] std::optional<ParsedMatchCommandBody> parse_match_command_body(std::string_view body);
[[nodiscard]] bool validate_match_command_body(const ParsedMatchCommandBody& body) noexcept;

struct ParsedLeaderboardSubmitCommandBody {
    std::string user_id;
    std::string display_name;
    std::int64_t score = 0;
};

[[nodiscard]] std::optional<ParsedLeaderboardSubmitCommandBody>
parse_leaderboard_submit_command_body(std::string_view body);
[[nodiscard]] bool validate_leaderboard_submit_command_body(
    const ParsedLeaderboardSubmitCommandBody& body) noexcept;

[[nodiscard]] std::optional<std::size_t> parse_leaderboard_top_command_body(std::string_view body);
[[nodiscard]] std::optional<std::string> parse_leaderboard_rank_command_body(std::string_view body);

}  // namespace v2::gateway
