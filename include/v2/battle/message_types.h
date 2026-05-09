#pragma once

#include <cstdint>
#include <string>
#include <variant>
#include <vector>

namespace v2::battle {

enum class BattleLifecycleState : std::uint8_t {
    kCreated = 0,
    kRunning = 1,
    kFinished = 2,
};

struct BattleParticipantState {
    std::string user_id;
    bool online = true;
};

struct BattleRuntimeState {
    std::string battle_id;
    std::string room_id;
    BattleLifecycleState lifecycle = BattleLifecycleState::kCreated;
    std::vector<BattleParticipantState> participants;
    std::uint32_t frame_number = 0;
};

struct CreateBattleMsg {
    std::string battle_id;
    std::string room_id;
    std::vector<std::string> player_ids;
};

struct SubmitBattleInputMsg {
    std::string user_id;
    std::uint32_t request_id = 0;
    std::string input_data;
};

struct TickBattleMsg {
    std::string trigger;
};

struct EndBattleMsg {
    std::string reason;
    std::string triggering_user_id;
};

struct PlayerDisconnectedMsg {
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
    std::string reason;
    std::string triggering_user_id;
};

using BattleEvent = std::variant<BattleCreatedMsg,
                                 BattleInputAcceptedMsg,
                                 BattleFrameAdvancedMsg,
                                 BattleFinishedMsg>;

}  // namespace v2::battle
