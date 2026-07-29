#pragma once

#include "v2/realtime/types.h"

#include <memory>
#include <string>
#include <vector>

namespace v2::realtime {

// ─── Business Plugin SPI ────────────────────────────────────────────
//
// Implement this interface to attach business logic to the realtime
// instance runtime. The runtime controls lifecycle and scheduling;
// the plugin only implements domain-specific rules.
//
// ## Error isolation
//
// The runtime wraps every plugin call in a try-catch block (except
// noexcept methods which must not throw by contract). If a plugin
// method that is NOT marked noexcept throws, the runtime catches the
// exception, logs it via AUDIT_LOG, and continues with a safe default
// return value. The instance is NOT destroyed — the runtime keeps the
// instance alive so the error is observable via metrics and
// diagnostics.
//
// ## Thread safety
//
// All plugin methods are called from the runtime's internal thread
// (under the instance lock). The plugin must not block or call back
// into the runtime recursively. If the plugin needs to offload work,
// it must use its own thread pool.
//
// ## noexcept contract
//
// Methods marked noexcept are called in hot paths (every tick) and
// MUST never throw. If a noexcept method does throw, std::terminate
// is called. Plugin authors are responsible for ensuring these
// methods handle all error conditions internally.

class InstancePlugin {
public:
    virtual ~InstancePlugin() = default;

    // ── Lifecycle hooks ────────────────────────────────────────────
    //
    // These are called at most once per instance lifetime. They may
    // throw; the runtime catches the exception and logs it.

    // Called when a new instance is created, immediately after the
    // InternalInstance is allocated and before the instance is
    // inserted into the runtime's instance map.
    //
    // Precondition:
    //   - instance_ctx is fully populated (instance_id, room_id,
    //     instance_type, players, tick_interval_ms, max_frames,
    //     resume_window_ms, created_at_ms).
    //   - instance_ctx.plugin_state is nullptr.
    //
    // Postcondition:
    //   - The plugin should allocate its world state and store it
    //     via instance_ctx.plugin_state.
    //
    // Error behavior:
    //   - If this throws, the instance creation fails and the
    //     instance is NOT inserted into the runtime map. The caller
    //     receives an empty instance_id.
    //   - AUDIT_LOG records the failure.
    virtual void on_instance_created(InstanceContext& instance_ctx) = 0;

    // Called immediately before the runtime releases the instance. Plugins
    // that allocate instance_ctx.plugin_state must destroy that state here
    // and set the pointer to nullptr. This hook is also called when
    // on_instance_created() throws after allocating state.
    virtual void on_instance_destroyed(InstanceContext& instance_ctx) noexcept {
        instance_ctx.plugin_state = nullptr;
    }

    // Called when a player joins an existing instance (e.g. after
    // matchmaking places the player into the instance, or during
    // late-join).
    //
    // Precondition:
    //   - instance_ctx is valid and the instance is in
    //     kWaitingPlayers or kRunning state.
    //   - player.user_id is not already in instance_ctx.players.
    //
    // Postcondition:
    //   - The plugin should update its internal player list / state.
    //   - The runtime appends the player to instance_ctx.players
    //     after this call returns (the plugin should NOT mutate
    //     instance_ctx.players directly).
    //
    // Error behavior:
    //   - Exception is caught by the runtime and logged. The player
    //     join is still recorded by the runtime, but the plugin's
    //     internal state may be inconsistent.
    virtual void on_player_join(InstanceContext& instance_ctx,
                                const PlayerContext& player) = 0;

    // Called when a player leaves or disconnects.
    //
    // Precondition:
    //   - instance_ctx is valid and player.user_id exists in the
    //     instance's player list.
    //
    // Postcondition:
    //   - The plugin should remove the player from its internal
    //     state and may choose to trigger finish if all players
    //     have left.
    //
    // Error behavior:
    //   - Exception is caught and logged. The player leave is still
    //     processed by the runtime.
    virtual void on_player_leave(InstanceContext& instance_ctx,
                                 const PlayerContext& player) = 0;

    // ── Input processing ───────────────────────────────────────────
    //
    // Called for each dequeued input during tick_instance(). May
    // throw; the runtime catches the exception and rejects the
    // input.

    // Process a single input. The plugin should update its world
    // state based on the input.
    //
    // Precondition:
    //   - instance_ctx is valid and instance is in kRunning or
    //     kWaitingPlayers state.
    //   - input.instance_id == instance_ctx.instance_id.
    //
    // Postcondition:
    //   - Returns InputResult with accepted=true/false.
    //   - If accepted, the input is added to
    //     frame_ctx.inputs_this_tick.
    //   - If rejected, the runtime drops the input and continues.
    //
    // Error behavior:
    //   - Exception is caught by the runtime. The input is treated
    //     as rejected (not added to frame_ctx.inputs_this_tick).
    //   - AUDIT_LOG records the failure.
    virtual InputResult on_input(InstanceContext& instance_ctx,
                                 const InputEnvelope& input) = 0;

    // ── Tick / simulation ──────────────────────────────────────────
    //
    // Called every tick frame. These are hot-path methods and MUST
    // be noexcept. Plugin authors must handle all errors internally.

    // Advance the simulation by one tick. The plugin should update
    // its world state based on accumulated inputs (available via
    // frame_ctx.inputs_this_tick) and return:
    //   - pushes_sent: number of pushes generated this tick
    //     (informational, used for metrics).
    //   - should_finish: set to true if the instance should end
    //     (e.g. win condition met, all players done).
    //   - finish_reason: meaningful only when should_finish=true.
    //
    // Precondition:
    //   - instance_ctx is valid, instance is in kRunning state.
    //   - frame_ctx.frame_number > 0 and is monotonically
    //     increasing.
    //   - frame_ctx.inputs_this_tick contains all inputs that were
    //     dequeued since the last tick.
    //
    // Postcondition:
    //   - Plugin internal state is advanced by one frame.
    //   - Returned TickStats accurately reflects the tick outcome.
    //
    // noexcept:
    //   This is called in the hot tick loop. The contract requires
    //   this method to never throw.
    //
    // Error behavior:
    //   - If this method throws despite the noexcept contract,
    //     std::terminate is called. Plugin authors must ensure all
    //     error paths are handled internally.
    virtual TickStats on_tick(InstanceContext& instance_ctx,
                              const FrameContext& frame_ctx) noexcept = 0;

    // ── Snapshot / settlement ──────────────────────────────────────
    //
    // These build serialisable representations of the instance state.
    // They are called in the hot path (every tick for snapshots, once
    // at end for settlement) and MUST be noexcept.

    // Build a current snapshot of the instance state. The snapshot
    // is emitted as an InstanceEvent of type kSnapshotAvailable.
    //
    // Precondition:
    //   - instance_ctx is valid and the plugin has been initialised
    //     via on_instance_created.
    //
    // Postcondition:
    //   - Returns a Snapshot with the current game state encoded in
    //     payload (opaque byte string). The runtime does not
    //     interpret the payload.
    //   - The runtime sets snapshot.frame_number after this returns.
    //   - If is_resume is true, the snapshot should contain enough
    //     state for a reconnecting player to reconstruct the world.
    //
    // noexcept:
    //   This is called once per tick in the hot path. The contract
    //   requires this method to never throw.
    //
    // Error behavior:
    //   - std::terminate if thrown. Plugin must handle errors
    //     internally.
    virtual Snapshot build_snapshot(InstanceContext& instance_ctx,
                                    bool is_resume = false) noexcept = 0;

    // Build settlement payload when the instance finishes. The
    // returned string is stored in SettlementContext.result_payload
    // and emitted in the kInstanceFinished event.
    //
    // Precondition:
    //   - instance_ctx is valid and in kFinishing/kFinished state.
    //   - settlement_ctx contains the finish reason and total frame
    //     count.
    //
    // Postcondition:
    //   - Returns an opaque string representing the final results
    //     (e.g. JSON with per-player scores).
    //
    // noexcept:
    //   Called at instance end, but still noexcept for consistency.
    //
    // Error behavior:
    //   - std::terminate if thrown.
    virtual std::string build_settlement(InstanceContext& instance_ctx,
                                         const SettlementContext& settlement_ctx) noexcept = 0;

    // Build a resume snapshot for a reconnecting player. This should
    // contain the minimal state needed for the player to reconstruct
    // their view of the world.
    //
    // Precondition:
    //   - instance_ctx is valid.
    //   - player.user_id exists in instance_ctx.players.
    //
    // Postcondition:
    //   - Returns a Snapshot with is_resume=true and enough payload
    //     for the reconnecting player.
    //
    // noexcept:
    //   Called during reconnect handling; noexcept for consistency.
    //
    // Error behavior:
    //   - std::terminate if thrown.
    virtual Snapshot build_resume_snapshot(InstanceContext& instance_ctx,
                                           const PlayerContext& player) noexcept = 0;
};

// ─── Factory type ───────────────────────────────────────────────────

using InstancePluginFactory = std::unique_ptr<InstancePlugin>(*)();

}  // namespace v2::realtime
