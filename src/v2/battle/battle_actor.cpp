#include "v2/battle/battle_actor.h"

#include <algorithm>
#include <cstdint>
#include <limits>
#include <utility>

namespace v2::battle {

void BattleActor::finish_battle(BattleFinishReason reason, std::string triggering_user_id) {
    if (state_.lifecycle == BattleLifecycleState::kFinished) {
        return;
    }

    state_.lifecycle = BattleLifecycleState::kFinished;
    std::vector<std::string> participant_user_ids;
    participant_user_ids.reserve(state_.participants.size());
    for (const auto& participant : state_.participants) {
        participant_user_ids.push_back(participant.user_id);
    }

    std::vector<BattleScore> scores;
    scores.reserve(state_.participants.size());
    std::optional<std::string> winner_user_id;
    std::int64_t high_score = 0;
    bool any_score_set = false;
    for (const auto& participant : state_.participants) {
        std::int64_t score = 0;
        for (const auto& input : state_.replay_inputs) {
            if (input.user_id == participant.user_id) {
                score += input.score;
            }
        }
        scores.push_back(BattleScore{.user_id = participant.user_id, .score = score});
        if (!any_score_set || score > high_score) {
            any_score_set = true;
            high_score = score;
            winner_user_id = participant.user_id;
        }
    }

    BattleResultSummary result{
        .battle_id = state_.battle_id,
        .room_id = state_.room_id,
        .reason = reason,
        .winner_user_id = winner_user_id,
        .scores = std::move(scores),
        .total_frames = state_.frame_number,
    };

    sink_.push(BattleSettlementPreparedMsg{
        .battle_id = state_.battle_id,
        .room_id = state_.room_id,
        .reason = reason,
        .triggering_user_id = triggering_user_id,
        .total_frames = state_.frame_number,
        .participant_user_ids = std::move(participant_user_ids),
        .replay_inputs = state_.replay_inputs,
        .result = std::move(result),
    });
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
        state_.replay_inputs.clear();
        state_.participants.clear();
        state_.participants.reserve(create->player_ids.size());
        for (const auto& user_id : create->player_ids) {
            state_.participants.push_back(BattleParticipantState{.user_id = user_id, .online = true});
        }
        max_frames_ = create->max_frames;
        last_submitted_frame_.clear();
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
        if (input->submitted_frame > 0) {
            auto it = last_submitted_frame_.find(input->user_id);
            if (it != last_submitted_frame_.end() && it->second >= input->submitted_frame) {
                return;
            }
            last_submitted_frame_[input->user_id] = input->submitted_frame;
        }
        const auto input_seq = next_input_seq_++;
        state_.replay_inputs.push_back(BattleReplayInputRecord{
            .input_seq = input_seq,
            .frame_number = state_.frame_number + 1,
            .user_id = input->user_id,
            .input_data = input->input_data,
            .score = input->score,
            .trigger = {},
        });
        sink_.push(BattleInputAcceptedMsg{
            .battle_id = state_.battle_id,
            .room_id = state_.room_id,
            .user_id = input->user_id,
            .input_seq = input_seq,
            .request_id = input->request_id,
            .input_data = input->input_data,
        });
        return;
    }

    const auto* tick = std::get_if<TickBattleMsg>(&message.payload);
    if (tick != nullptr && state_.lifecycle == BattleLifecycleState::kRunning) {
        ++state_.frame_number;
        for (auto& record : state_.replay_inputs) {
            if (record.frame_number == state_.frame_number) {
                record.trigger = tick->trigger;
            }
        }
        sink_.push(BattleFrameAdvancedMsg{
            .battle_id = state_.battle_id,
            .room_id = state_.room_id,
            .frame_number = state_.frame_number,
            .trigger = tick->trigger,
        });
        if (max_frames_ > 0 && state_.frame_number >= max_frames_) {
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
