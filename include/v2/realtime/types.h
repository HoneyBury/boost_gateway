#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <chrono>

namespace v2::realtime {

// ─── Instance lifecycle state ───────────────────────────────────────

enum class InstanceState : std::uint8_t {
    kCreating = 0,
    kWaitingPlayers = 1,
    kRunning = 2,
    kFinishing = 3,
    kFinished = 4,
    kClosed = 5,
};

inline const char* to_string(InstanceState s) {
    switch (s) {
        case InstanceState::kCreating: return "creating";
        case InstanceState::kWaitingPlayers: return "waiting_players";
        case InstanceState::kRunning: return "running";
        case InstanceState::kFinishing: return "finishing";
        case InstanceState::kFinished: return "finished";
        case InstanceState::kClosed: return "closed";
    }
    return "creating";
}

// ─── Finish reasons ─────────────────────────────────────────────────

enum class FinishReason : std::uint8_t {
    kNormal = 0,
    kAllPlayersDone = 1,
    kTimeout = 2,
    kFrameLimit = 3,
    kPlayerDisconnected = 4,
    kUserRequested = 5,
    kError = 6,
};

inline const char* to_string(FinishReason r) {
    switch (r) {
        case FinishReason::kNormal: return "normal";
        case FinishReason::kAllPlayersDone: return "all_players_done";
        case FinishReason::kTimeout: return "timeout";
        case FinishReason::kFrameLimit: return "frame_limit";
        case FinishReason::kPlayerDisconnected: return "player_disconnected";
        case FinishReason::kUserRequested: return "user_requested";
        case FinishReason::kError: return "error";
    }
    return "normal";
}

// ─── Input envelope ─────────────────────────────────────────────────

struct InputEnvelope {
    std::string instance_id;
    std::string user_id;
    std::uint64_t seq = 0;
    std::string payload_type;   // e.g. "tank.input"
    std::string payload;        // opaque business payload
    std::int64_t client_time_ms = 0;
    std::int64_t score = 0;
    std::uint32_t submitted_frame = 0;
};

struct InputResult {
    bool accepted = false;
    std::string reject_reason;
    std::uint64_t ack_seq = 0;
};

// ─── Player context ─────────────────────────────────────────────────

struct PlayerContext {
    std::string user_id;
    std::string display_name;
    std::int64_t joined_at_ms = 0;
};

// ─── Instance context ───────────────────────────────────────────────

struct InstanceContext {
    std::string instance_id;
    std::string room_id;
    std::string instance_type;   // e.g. "tank_battle", "echo"
    std::vector<PlayerContext> players;
    std::uint32_t tick_interval_ms = 33;  // ~30 Hz default
    std::uint32_t max_frames = 0;         // 0 = unlimited
    std::uint32_t input_queue_limit = 64;
    std::uint32_t resume_window_ms = 30000; // 30 seconds
    std::int64_t created_at_ms = 0;
    void* plugin_state = nullptr;   // opaque business state

    // Find a player by user_id
    PlayerContext* find_player(const std::string& user_id) {
        for (auto& p : players) {
            if (p.user_id == user_id) return &p;
        }
        return nullptr;
    }
};

// ─── Frame context ──────────────────────────────────────────────────

struct FrameContext {
    std::uint32_t frame_number = 0;
    std::int64_t tick_start_ms = 0;
    std::vector<InputEnvelope> inputs_this_tick;
};

// ─── Snapshot ───────────────────────────────────────────────────────

struct Snapshot {
    std::uint32_t frame_number = 0;
    std::string payload_type;    // e.g. "tank.snapshot"
    std::string payload;         // opaque business payload
    bool is_full = true;         // false for delta snapshots
    bool is_resume = false;      // true for resume-after-reconnect
};

// ─── Settlement ─────────────────────────────────────────────────────

struct SettlementContext {
    std::string instance_id;
    std::string room_id;
    FinishReason reason = FinishReason::kNormal;
    std::uint32_t total_frames = 0;
    std::string result_payload;  // opaque business settlement
};

// ─── Instance state snapshot (for diagnostics) ──────────────────────

struct InstanceSnapshot {
    std::string instance_id;
    std::string instance_type;
    InstanceState state = InstanceState::kCreating;
    std::uint32_t frame_number = 0;
    std::uint32_t player_count = 0;
    std::uint32_t input_queue_size = 0;
    std::int64_t created_at_ms = 0;
    std::int64_t running_since_ms = 0;
};

// ─── Tick statistics ────────────────────────────────────────────────

struct TickStats {
    std::uint32_t frame_number = 0;
    std::uint32_t inputs_processed = 0;
    std::uint32_t pushes_sent = 0;
    double tick_duration_ms = 0.0;
    bool should_finish = false;
    FinishReason finish_reason = FinishReason::kNormal;
};

}  // namespace v2::realtime
