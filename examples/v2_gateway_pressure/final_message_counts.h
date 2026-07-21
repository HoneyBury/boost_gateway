#pragma once

#include <cstdint>

namespace v2::gateway_pressure {

struct FinalMessageCounts {
    std::uint64_t response_messages = 0;
    std::uint64_t push_messages = 0;
    std::uint64_t total_messages = 0;
};

constexpr FinalMessageCounts final_message_counts(std::uint64_t response_messages,
                                                  std::uint64_t push_messages) noexcept {
    return {
        .response_messages = response_messages,
        .push_messages = push_messages,
        .total_messages = response_messages + push_messages,
    };
}

} // namespace v2::gateway_pressure
