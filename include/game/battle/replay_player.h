#pragma once

#include "game/battle/battle_manager.h"
#include "game/persistence/player_store.h"

#include <nlohmann/json.hpp>

#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <vector>

namespace game::battle {

class ReplayPlayer {
public:
    using FrameCallback = std::function<void(const FrameSnapshot&)>;
    using EndCallback = std::function<void()>;

    explicit ReplayPlayer(persistence::IBattleReplayStore& store) : store_(store) {}

    bool load(const std::string& battle_id) {
        auto data = store_.load_replay(battle_id);
        if (!data) return false;

        auto doc = nlohmann::json::parse(*data);
        battle_id_ = doc.value("battle_id", "");
        room_id_ = doc.value("room_id", "");
        total_frames_ = doc.value("total_frames", 0);

        frames_.clear();
        for (const auto& f : doc["frames"]) {
            FrameSnapshot snap;
            snap.frame_number = f.value("frame", 0u);
            snap.room_id = room_id_;
            for (const auto& input : f["inputs"]) {
                InputEvent ev;
                ev.sequence = input.value("seq", 0ull);
                ev.frame_number = snap.frame_number;
                ev.user_id = input.value("user_id", "");
                ev.payload = input.value("payload", "");
                snap.inputs.push_back(std::move(ev));
            }
            frames_.push_back(std::move(snap));
        }
        current_frame_ = 0;
        return true;
    }

    void play(FrameCallback on_frame, EndCallback on_end) {
        if (current_frame_ >= frames_.size()) {
            if (on_end) on_end();
            return;
        }
        if (on_frame) on_frame(frames_[current_frame_]);
        ++current_frame_;
        if (current_frame_ >= frames_.size() && on_end) on_end();
    }

    [[nodiscard]] bool finished() const { return current_frame_ >= frames_.size(); }
    [[nodiscard]] std::string battle_id() const { return battle_id_; }
    [[nodiscard]] std::uint32_t total_frames() const { return total_frames_; }

private:
    persistence::IBattleReplayStore& store_;
    std::string battle_id_;
    std::string room_id_;
    std::uint32_t total_frames_ = 0;
    std::vector<FrameSnapshot> frames_;
    std::size_t current_frame_ = 0;
};

}  // namespace game::battle
