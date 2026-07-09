#pragma once

#include "v2/perf/hot_path.h"
#include "v2/realtime/types.h"
#include "v2/realtime/instance_plugin.h"

#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

namespace v2::realtime {

// ─── Callbacks from runtime to owner ────────────────────────────────
//
// The runtime owner (e.g. a backend service) receives these events
// and is responsible for pushing snapshots to connected sessions.

struct InstanceEvent {
    enum class Type {
        kInstanceCreated,
        kInstanceFinished,
        kPlayerJoined,
        kPlayerLeft,
        kSnapshotAvailable,
        kInputRejected,
        kError,
    };

    Type type = Type::kError;
    std::string instance_id;
    std::string user_id;

    // For kSnapshotAvailable
    Snapshot snapshot{};

    // For kInstanceFinished
    SettlementContext settlement{};
};

using InstanceEventCallback = std::function<void(const InstanceEvent&)>;

// ─── Tick timer callback ────────────────────────────────────────────

using TickTimerCallback = std::function<void(const std::string& instance_id)>;

// ─── Runtime Configuration ─────────────────────────────────────────

struct RuntimeConfig {
    std::uint32_t max_instances = 1024;
    std::uint32_t global_input_queue_limit = 4096;
    bool enable_tick_timer = true;
};

// ─── Realtime Instance Runtime ──────────────────────────────────────
//
// Manages the lifecycle, tick scheduling, input queue, and snapshot
// generation for all realtime instances. Business logic is injected
// via InstancePlugin.

class InstanceRuntime {
public:
    explicit InstanceRuntime(RuntimeConfig config = {});
    ~InstanceRuntime();

    // Not copyable or movable
    InstanceRuntime(const InstanceRuntime&) = delete;
    InstanceRuntime& operator=(const InstanceRuntime&) = delete;

    // Register a plugin factory for an instance type.
    // The factory is called each time create_instance() is invoked.
    void register_plugin(const std::string& instance_type,
                         InstancePluginFactory factory);

    // Set the callback for instance events.
    void set_event_callback(InstanceEventCallback callback);

    // ─── Instance lifecycle ──────────────────────────────────────

    // Create a new instance. Returns the instance_id on success, empty on failure.
    // The plugin's on_instance_created() is called synchronously.
    std::string create_instance(
        const std::string& instance_id,
        const std::string& room_id,
        const std::string& instance_type,
        const std::vector<PlayerContext>& players,
        std::uint32_t tick_interval_ms = 33,
        std::uint32_t max_frames = 0,
        std::uint32_t resume_window_ms = 30000);

    // Destroy an instance and clean up resources.
    void destroy_instance(const std::string& instance_id);

    // Submit an input to an instance. Returns the input result.
    InputResult submit_input(const InputEnvelope& input);

    // Request the instance to finish.
    void finish_instance(const std::string& instance_id,
                         FinishReason reason = FinishReason::kUserRequested);

    // Get a resume snapshot for a reconnecting player.
    // Returns empty Snapshot if the instance or player is not found.
    [[nodiscard]] Snapshot get_resume_snapshot(
        const std::string& instance_id,
        const std::string& user_id);

    // ─── Tick / advance ──────────────────────────────────────────

    // Tick a single instance. Called by the tick timer or manually.
    BOOST_HOT_PATH TickStats tick_instance(const std::string& instance_id,
                                           std::uint32_t frame_number,
                                           std::int64_t tick_start_ms);

    // Tick all running instances. Returns stats for each.
    std::vector<TickStats> tick_all(std::int64_t tick_start_ms);

    // ─── Query ──────────────────────────────────────────────────

    [[nodiscard]] InstanceContext* find_instance(const std::string& instance_id);
    [[nodiscard]] InstanceState get_instance_state(const std::string& instance_id) const;
    [[nodiscard]] std::vector<InstanceSnapshot> list_instances() const;
    [[nodiscard]] std::size_t instance_count() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace v2::realtime
