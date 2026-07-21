#pragma once

namespace v2::gateway_pressure {

enum class StallWatchdogAction {
    kRearm,
    kStopForStall,
    kStopMonitoring,
};

struct StallWatchdogState {
    bool measurement_started = false;
    bool lifecycle_finished = false;
    bool duration_timer_expected = false;
    bool duration_deadline_armed = false;
    bool duration_deadline_elapsed = false;
    bool progress_changed = false;
};

constexpr StallWatchdogAction stall_watchdog_action(
    const StallWatchdogState& state) noexcept {
    if (state.lifecycle_finished) {
        return StallWatchdogAction::kStopMonitoring;
    }
    if (!state.measurement_started) {
        return StallWatchdogAction::kRearm;
    }
    if (state.duration_timer_expected) {
        if (!state.duration_deadline_armed) {
            return StallWatchdogAction::kRearm;
        }
        if (state.duration_deadline_elapsed) {
            return StallWatchdogAction::kStopMonitoring;
        }
    }
    return state.progress_changed ? StallWatchdogAction::kRearm
                                  : StallWatchdogAction::kStopForStall;
}

}  // namespace v2::gateway_pressure
