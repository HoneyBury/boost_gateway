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

}  // namespace v2::gateway
