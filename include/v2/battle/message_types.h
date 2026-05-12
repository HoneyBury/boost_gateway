#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <variant>
#include <vector>

namespace v2::battle {

enum class BattleLifecycleState : std::uint8_t {
    kCreated = 0,
    kRunning = 1,
    kFinished = 2,
};

enum class BattleFinishReason : std::uint8_t {
    kFinished = 0,
    kSurrender = 1,
    kTimeout = 2,
    kFrameLimitReached = 3,
    kPlayerDisconnected = 4,
    kUserRequested = 5,
};

[[nodiscard]] constexpr const char* to_string(BattleFinishReason reason) {
    switch (reason) {
        case BattleFinishReason::kFinished:
            return "finished";
        case BattleFinishReason::kSurrender:
            return "surrender";
        case BattleFinishReason::kTimeout:
            return "timeout";
        case BattleFinishReason::kFrameLimitReached:
            return "frame_limit_reached";
        case BattleFinishReason::kPlayerDisconnected:
            return "player_disconnected";
        case BattleFinishReason::kUserRequested:
            return "user_requested";
    }

    return "finished";
}

struct BattleParticipantState {
    std::string user_id;
    bool online = true;
};

struct BattleReplayInputRecord {
    std::uint64_t input_seq = 0;
    std::uint32_t frame_number = 0;
    std::string user_id;
    std::string input_data;
    std::int64_t score = 0;
    std::string trigger;
};

struct BattleRuntimeState {
    std::string battle_id;
    std::string room_id;
    BattleLifecycleState lifecycle = BattleLifecycleState::kCreated;
    std::vector<BattleParticipantState> participants;
    std::uint32_t frame_number = 0;
    std::vector<BattleReplayInputRecord> replay_inputs;
};

struct CreateBattleMsg {
    std::string battle_id;
    std::string room_id;
    std::vector<std::string> player_ids;
    std::uint32_t max_frames = 0;
};

struct SubmitBattleInputMsg {
    std::string user_id;
    std::uint32_t request_id = 0;
    std::string input_data;
    std::int64_t score = 0;
    std::uint32_t submitted_frame = 0;
};

struct TickBattleMsg {
    std::string trigger;
};

struct EndBattleMsg {
    BattleFinishReason reason = BattleFinishReason::kFinished;
    std::string triggering_user_id;
};

struct PlayerDisconnectedMsg {
    std::string user_id;
};

struct PlayerReconnectedMsg {
    std::string user_id;
};

struct DisconnectGraceTimerExpiredMsg {
    std::string user_id;
};

struct BattleCreatedMsg {
    std::string battle_id;
    std::string room_id;
    std::vector<std::string> player_ids;
};

struct BattleInputAcceptedMsg {
    std::string battle_id;
    std::string room_id;
    std::string user_id;
    std::uint64_t input_seq = 0;
    std::uint32_t request_id = 0;
    std::string input_data;
};

struct BattleFrameAdvancedMsg {
    std::string battle_id;
    std::string room_id;
    std::uint32_t frame_number = 0;
    std::string trigger;
};

struct BattleFinishedMsg {
    std::string battle_id;
    std::string room_id;
    BattleFinishReason reason = BattleFinishReason::kFinished;
    std::string triggering_user_id;
};

struct BattleScore {
    std::string user_id;
    std::int64_t score = 0;
};

struct BattleResultSummary {
    std::string battle_id;
    std::string room_id;
    BattleFinishReason reason = BattleFinishReason::kFinished;
    std::optional<std::string> winner_user_id;
    std::vector<BattleScore> scores;
    std::uint32_t total_frames = 0;
};

struct BattleSettlementPreparedMsg {
    std::string battle_id;
    std::string room_id;
    BattleFinishReason reason = BattleFinishReason::kFinished;
    std::string triggering_user_id;
    std::uint32_t total_frames = 0;
    std::vector<std::string> participant_user_ids;
    std::vector<BattleReplayInputRecord> replay_inputs;
    BattleResultSummary result;
};

struct FrameAckMsg {
    std::string user_id;
    std::uint32_t frame_number = 0;
};

using BattleEvent = std::variant<BattleCreatedMsg,
                                 BattleInputAcceptedMsg,
                                 BattleFrameAdvancedMsg,
                                 BattleSettlementPreparedMsg,
                                 BattleFinishedMsg,
                                 FrameAckMsg>;

}  // namespace v2::battle
