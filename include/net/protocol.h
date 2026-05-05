#pragma once

#include <cstdint>

namespace net::protocol {

constexpr std::uint16_t kHeartbeatRequest = 1;
constexpr std::uint16_t kHeartbeatResponse = 2;
constexpr std::uint16_t kEchoRequest = 1001;
constexpr std::uint16_t kEchoResponse = 1002;

}  // namespace net::protocol
