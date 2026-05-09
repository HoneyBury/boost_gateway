#pragma once

#include "v2/battle/message_types.h"

#include <optional>
#include <string>
#include <string_view>

namespace v2::gateway {

[[nodiscard]] std::optional<v2::battle::BattleFinishReason> parse_battle_finish_request(std::string_view body);
[[nodiscard]] std::string format_battle_state_body(std::string_view room_id, std::string_view battle_id);
[[nodiscard]] std::string format_battle_end_accepted_body(v2::battle::BattleFinishReason reason);
[[nodiscard]] std::string format_battle_frame_body(const v2::battle::BattleFrameAdvancedMsg& frame);
[[nodiscard]] std::string format_battle_finished_body(const v2::battle::BattleFinishedMsg& finished);

}  // namespace v2::gateway
