#include "v2/gateway/battle_protocol_codec.h"

#include <fmt/format.h>

namespace v2::gateway {

std::optional<v2::battle::BattleFinishReason> parse_battle_finish_request(std::string_view body) {
    constexpr std::string_view prefix = "finish:";
    if (!body.starts_with(prefix)) {
        return std::nullopt;
    }

    const auto reason = body.substr(prefix.size());
    if (reason.empty() || reason == "finished") {
        return v2::battle::BattleFinishReason::kFinished;
    }
    if (reason == "surrender") {
        return v2::battle::BattleFinishReason::kSurrender;
    }
    if (reason == "timeout") {
        return v2::battle::BattleFinishReason::kTimeout;
    }
    return v2::battle::BattleFinishReason::kFinished;
}

std::string format_battle_state_body(std::string_view room_id, std::string_view battle_id) {
    return fmt::format("battle_state:{}:{}", room_id, battle_id);
}

std::string format_battle_end_accepted_body(v2::battle::BattleFinishReason reason) {
    return fmt::format("battle_end_accepted:{}", v2::battle::to_string(reason));
}

std::string format_battle_frame_body(const v2::battle::BattleFrameAdvancedMsg& frame) {
    return fmt::format("battle_frame:{}:{}:{}:{}",
                       frame.room_id,
                       frame.battle_id,
                       frame.frame_number,
                       frame.trigger);
}

std::string format_battle_finished_body(const v2::battle::BattleFinishedMsg& finished) {
    return fmt::format("battle_finished:{}:{}:{}:{}",
                       finished.room_id,
                       finished.battle_id,
                       v2::battle::to_string(finished.reason),
                       finished.triggering_user_id);
}

}  // namespace v2::gateway
