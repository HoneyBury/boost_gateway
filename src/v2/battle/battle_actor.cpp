#include "v2/battle/battle_actor.h"

#include <algorithm>
#include <utility>

namespace v2::battle {

void BattleActor::finish_battle(BattleFinishReason reason, std::string triggering_user_id) {
    if (state_.lifecycle == BattleLifecycleState::kFinished) {
        return;
    }

    state_.lifecycle = BattleLifecycleState::kFinished;
    sink_.push(BattleFinishedMsg{
        .battle_id = state_.battle_id,
        .room_id = state_.room_id,
        .reason = reason,
        .triggering_user_id = std::move(triggering_user_id),
    });
}

void BattleActor::on_message(v2::actor::Message&& message) {
    if (const auto* create = std::get_if<CreateBattleMsg>(&message.payload)) {
        state_.battle_id = create->battle_id;
        state_.room_id = create->room_id;
        state_.frame_number = 0;
        state_.participants.clear();
        state_.participants.reserve(create->player_ids.size());
        for (const auto& user_id : create->player_ids) {
            state_.participants.push_back(BattleParticipantState{.user_id = user_id, .online = true});
        }
        state_.lifecycle = BattleLifecycleState::kRunning;

        sink_.push(BattleCreatedMsg{
            .battle_id = state_.battle_id,
            .room_id = state_.room_id,
            .player_ids = create->player_ids,
        });
        return;
    }

    const auto* input = std::get_if<SubmitBattleInputMsg>(&message.payload);
    if (input != nullptr && state_.lifecycle == BattleLifecycleState::kRunning) {
        sink_.push(BattleInputAcceptedMsg{
            .battle_id = state_.battle_id,
            .room_id = state_.room_id,
            .user_id = input->user_id,
            .input_seq = next_input_seq_++,
            .request_id = input->request_id,
            .input_data = input->input_data,
        });
        return;
    }

    const auto* tick = std::get_if<TickBattleMsg>(&message.payload);
    if (tick != nullptr && state_.lifecycle == BattleLifecycleState::kRunning) {
        ++state_.frame_number;
        sink_.push(BattleFrameAdvancedMsg{
            .battle_id = state_.battle_id,
            .room_id = state_.room_id,
            .frame_number = state_.frame_number,
            .trigger = tick->trigger,
        });
        if (state_.frame_number >= kFrameLimit) {
            finish_battle(BattleFinishReason::kFrameLimitReached, tick->trigger);
        }
        return;
    }

    const auto* end = std::get_if<EndBattleMsg>(&message.payload);
    if (end != nullptr && state_.lifecycle == BattleLifecycleState::kRunning) {
        finish_battle(end->reason, end->triggering_user_id);
        return;
    }

    const auto* disconnected = std::get_if<PlayerDisconnectedMsg>(&message.payload);
    if (disconnected == nullptr || state_.lifecycle != BattleLifecycleState::kRunning) {
        return;
    }

    auto it = std::find_if(state_.participants.begin(),
                           state_.participants.end(),
                           [&disconnected](const BattleParticipantState& participant) {
                               return participant.user_id == disconnected->user_id;
                           });
    if (it == state_.participants.end() || !it->online) {
        return;
    }

    it->online = false;
    finish_battle(BattleFinishReason::kPlayerDisconnected, disconnected->user_id);
}

}  // namespace v2::battle
