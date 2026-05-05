#pragma once

#include <cstdint>

namespace net::protocol {

constexpr std::uint16_t kHeartbeatRequest = 1;
constexpr std::uint16_t kHeartbeatResponse = 2;

constexpr std::uint16_t kEchoRequest = 1001;
constexpr std::uint16_t kEchoResponse = 1002;

constexpr std::uint16_t kLoginRequest = 2001;
constexpr std::uint16_t kLoginResponse = 2002;

constexpr std::uint16_t kRoomJoinRequest = 3001;
constexpr std::uint16_t kRoomJoinResponse = 3002;

constexpr std::uint16_t kBattleStartRequest = 4001;
constexpr std::uint16_t kBattleStartResponse = 4002;

}  // namespace net::protocol
