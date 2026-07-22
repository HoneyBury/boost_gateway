#pragma once

#include <chrono>

namespace v2::gateway_pressure {

inline constexpr auto kBattleCleanupTimeout = std::chrono::seconds(10);
inline constexpr auto kBattleStopGrace = std::chrono::seconds(12);

constexpr bool is_expected_shutdown_error(bool battle_scenario,
                                          bool global_completion) noexcept {
    return battle_scenario && global_completion;
}

}  // namespace v2::gateway_pressure
