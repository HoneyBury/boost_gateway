#include "game/battle/battle_manager.h"

#include <algorithm>
#include <utility>

namespace game::battle {

BattleManager::StartBattleOutcome BattleManager::start_battle(const std::string& room_id,
                                                              std::vector<std::string> player_ids) {
    std::scoped_lock lock(mutex_);

    if (room_id.empty()) {
        return {StartBattleResult::kNotInRoom, "", 0};
    }

    if (player_ids.size() < 2) {
        return {StartBattleResult::kNotEnoughPlayers, room_id, player_ids.size()};
    }

    if (active_battles_.contains(room_id)) {
        return {StartBattleResult::kAlreadyStarted, room_id, player_ids.size()};
    }

    active_battles_.emplace(room_id,
                            BattleContext{
                                .player_ids = std::move(player_ids),
                                .next_sequence = 1,
                                .inputs = {},
                            });
    return {StartBattleResult::kOk, room_id, active_battles_.at(room_id).player_ids.size()};
}

BattleManager::SubmitInputOutcome BattleManager::submit_input(const std::string& room_id,
                                                              const std::string& user_id,
                                                              std::string payload) {
    std::scoped_lock lock(mutex_);

    auto battle_it = active_battles_.find(room_id);
    if (battle_it == active_battles_.end()) {
        return {};
    }

    if (std::find(battle_it->second.player_ids.begin(), battle_it->second.player_ids.end(), user_id) ==
        battle_it->second.player_ids.end()) {
        return {SubmitInputResult::kPlayerNotInBattle, room_id, {}};
    }

    InputEvent event{
        .sequence = battle_it->second.next_sequence++,
        .frame_number = battle_it->second.current_frame,
        .user_id = user_id,
        .payload = std::move(payload),
    };
    battle_it->second.inputs.push_back(event);
    return {SubmitInputResult::kOk, room_id, std::move(event)};
}

std::optional<BattleManager::FrameSnapshot> BattleManager::advance_frame(const std::string& room_id) {
    std::scoped_lock lock(mutex_);
    auto battle_it = active_battles_.find(room_id);
    if (battle_it == active_battles_.end()) {
        return std::nullopt;
    }

    auto& ctx = battle_it->second;
    const auto frame = ctx.current_frame++;

    // Collect all inputs for this frame
    std::vector<InputEvent> frame_inputs;
    for (const auto& input : ctx.inputs) {
        if (input.frame_number == frame) {
            frame_inputs.push_back(input);
        }
    }

    return FrameSnapshot{
        .frame_number = frame,
        .room_id = room_id,
        .inputs = std::move(frame_inputs),
    };
}

std::optional<BattleManager::BattleResult> BattleManager::end_battle(const std::string& room_id) {
    std::scoped_lock lock(mutex_);
    auto battle_it = active_battles_.find(room_id);
    if (battle_it == active_battles_.end()) {
        return std::nullopt;
    }

    auto& ctx = battle_it->second;

    // Compute per-player input counts as scores
    std::unordered_map<std::string, std::uint64_t> scores;
    for (const auto& input : ctx.inputs) {
        scores[input.user_id]++;
    }

    std::vector<std::pair<std::string, std::uint64_t>> player_scores;
    std::string winner_id;
    std::uint64_t max_score = 0;

    for (const auto& pid : ctx.player_ids) {
        const auto score = scores[pid];
        player_scores.emplace_back(pid, score);
        if (score > max_score) {
            max_score = score;
            winner_id = pid;
        }
    }

    BattleResult result{
        .room_id = room_id,
        .winner_id = std::move(winner_id),
        .total_frames = ctx.current_frame,
        .total_inputs = ctx.inputs.size(),
        .player_scores = std::move(player_scores),
    };

    active_battles_.erase(battle_it);
    return result;
}

void BattleManager::remove_room(const std::string& room_id) {
    std::scoped_lock lock(mutex_);
    active_battles_.erase(room_id);
}

bool BattleManager::battle_started(const std::string& room_id) const {
    std::scoped_lock lock(mutex_);
    return active_battles_.contains(room_id);
}

std::size_t BattleManager::active_battle_count() const {
    std::scoped_lock lock(mutex_);
    return active_battles_.size();
}

std::optional<BattleManager::BattleSnapshot> BattleManager::snapshot(const std::string& room_id) const {
    std::scoped_lock lock(mutex_);

    const auto battle_it = active_battles_.find(room_id);
    if (battle_it == active_battles_.end()) {
        return std::nullopt;
    }

    return BattleSnapshot{
        .room_id = room_id,
        .player_ids = battle_it->second.player_ids,
        .next_sequence = battle_it->second.next_sequence,
        .current_frame = battle_it->second.current_frame,
        .inputs = battle_it->second.inputs,
    };
}

}  // namespace game::battle
