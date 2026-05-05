#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace game::battle {

class BattleManager {
public:
    struct InputEvent {
        std::uint64_t sequence = 0;
        std::uint32_t frame_number = 0;
        std::string user_id;
        std::string payload;
    };

    struct FrameSnapshot {
        std::uint32_t frame_number = 0;
        std::string room_id;
        std::vector<InputEvent> inputs;
    };

    struct BattleResult {
        std::string room_id;
        std::string winner_id;
        std::uint32_t total_frames = 0;
        std::uint64_t total_inputs = 0;
        std::vector<std::pair<std::string, std::uint64_t>> player_scores;
    };

    struct BattleSnapshot {
        std::string room_id;
        std::vector<std::string> player_ids;
        std::uint64_t next_sequence = 1;
        std::uint32_t current_frame = 0;
        std::vector<InputEvent> inputs;
    };

    enum class StartBattleResult {
        kOk,
        kNotInRoom,
        kAlreadyStarted,
        kNotEnoughPlayers,
    };

    enum class SubmitInputResult {
        kOk,
        kBattleNotStarted,
        kPlayerNotInBattle,
    };

    struct StartBattleOutcome {
        StartBattleResult result = StartBattleResult::kNotInRoom;
        std::string room_id;
        std::size_t player_count = 0;
    };

    struct SubmitInputOutcome {
        SubmitInputResult result = SubmitInputResult::kBattleNotStarted;
        std::string room_id;
        InputEvent input;
    };

    StartBattleOutcome start_battle(const std::string& room_id, std::vector<std::string> player_ids);
    SubmitInputOutcome submit_input(const std::string& room_id,
                                    const std::string& user_id,
                                    std::string payload);
    [[nodiscard]] std::optional<FrameSnapshot> advance_frame(const std::string& room_id);
    [[nodiscard]] std::optional<BattleResult> end_battle(const std::string& room_id);
    void remove_room(const std::string& room_id);
    [[nodiscard]] bool battle_started(const std::string& room_id) const;
    [[nodiscard]] std::size_t active_battle_count() const;
    [[nodiscard]] std::optional<BattleSnapshot> snapshot(const std::string& room_id) const;

private:
    struct BattleContext {
        std::vector<std::string> player_ids;
        std::uint64_t next_sequence = 1;
        std::uint32_t current_frame = 0;
        std::vector<InputEvent> inputs;
    };

    mutable std::mutex mutex_;
    std::unordered_map<std::string, BattleContext> active_battles_;
};

}  // namespace game::battle
