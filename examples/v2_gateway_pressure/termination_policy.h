#pragma once

namespace v2::gateway_pressure {

constexpr bool is_expected_shutdown_error(bool battle_scenario,
                                          bool global_completion) noexcept {
    return battle_scenario && global_completion;
}

}  // namespace v2::gateway_pressure
