#pragma once

#include <chrono>
#include <optional>
#include <string_view>

namespace v2::gateway_pressure {

enum class LoadModel {
    kClosedLoop,
    kOpenLoop,
};

constexpr std::string_view load_model_name(LoadModel model) noexcept {
    return model == LoadModel::kOpenLoop
        ? "open_loop_fixed_interval_per_client"
        : "closed_loop_one_in_flight_per_client";
}

constexpr std::optional<LoadModel> parse_load_model(std::string_view value) noexcept {
    if (value == "closed-loop") {
        return LoadModel::kClosedLoop;
    }
    if (value == "open-loop") {
        return LoadModel::kOpenLoop;
    }
    return std::nullopt;
}

template <typename Clock>
constexpr typename Clock::time_point next_open_loop_deadline(
    typename Clock::time_point previous,
    std::chrono::milliseconds interval) noexcept {
    return previous + interval;
}

}  // namespace v2::gateway_pressure
