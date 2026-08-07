#pragma once

#include <cstddef>

namespace v2::gateway_pressure {

constexpr bool should_mark_global_completion(bool battle_scenario,
                                             std::size_t completed_clients,
                                             std::size_t target_clients) noexcept {
    return battle_scenario && target_clients > 0 &&
           completed_clients == target_clients;
}

}  // namespace v2::gateway_pressure
