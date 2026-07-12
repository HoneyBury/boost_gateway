#pragma once

// Experimental generated gRPC SDK transport. This does not replace the
// default TCP SdkClient transport.

#include "boost_gateway/sdk/types.h"

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace boost_gateway::sdk {

class GrpcClient {
 public:
  using BattleStateUpdateCallback = std::function<bool(const BattleStateResult&)>;

  GrpcClient();
  ~GrpcClient();

  GrpcClient(const GrpcClient&) = delete;
  GrpcClient& operator=(const GrpcClient&) = delete;

  bool connect(const std::string& host,
               std::uint16_t port,
               std::chrono::milliseconds timeout = std::chrono::seconds(5));
  void disconnect();
  [[nodiscard]] bool is_connected() const;

  LoginResult login(const std::string& user_id,
                    const std::string& token,
                    const std::string& display_name = "",
                    std::chrono::milliseconds timeout = std::chrono::seconds(5));

  RoomResult create_room(const std::string& user_id,
                         const std::string& room_id,
                         std::chrono::milliseconds timeout = std::chrono::seconds(5));
  RoomResult join_room(const std::string& user_id,
                       const std::string& room_id,
                       std::chrono::milliseconds timeout = std::chrono::seconds(5));
  RoomResult set_ready(const std::string& user_id,
                       const std::string& room_id,
                       bool ready,
                       std::chrono::milliseconds timeout = std::chrono::seconds(5));

  BattleStartResult create_battle(const std::string& battle_id,
                                  const std::string& room_id,
                                  const std::vector<std::string>& player_ids,
                                  std::uint32_t max_frames,
                                  std::chrono::milliseconds timeout = std::chrono::seconds(5));
  BattleInputResult send_battle_input(const std::string& user_id,
                                      const std::string& battle_id,
                                      const std::string& input_data,
                                      std::uint32_t submitted_frame,
                                      std::chrono::milliseconds timeout = std::chrono::seconds(5));
  BattleStateResult battle_state(const std::string& battle_id,
                                 std::chrono::milliseconds timeout = std::chrono::seconds(5));
  BattleStateStreamResult stream_battle_state(
      const std::string& battle_id,
      std::size_t update_count,
      std::chrono::milliseconds timeout = std::chrono::seconds(5));
  /// Subscribe until the callback returns false or the RPC deadline expires.
  /// Returning false is treated as a successful local cancellation when at
  /// least one update was received.
  BattleStateStreamResult subscribe_battle_state(
      const std::string& battle_id,
      BattleStateUpdateCallback on_update,
      std::chrono::milliseconds update_interval = std::chrono::milliseconds(250),
      std::chrono::milliseconds timeout = std::chrono::seconds(5));
  BattleFinishResult finish_battle(const std::string& user_id,
                                   const std::string& battle_id,
                                   const std::string& reason,
                                   std::chrono::milliseconds timeout = std::chrono::seconds(5));

  LeaderboardSubmitResult leaderboard_submit(
      const std::string& user_id,
      const std::string& display_name,
      std::int64_t score,
      std::chrono::milliseconds timeout = std::chrono::seconds(5));
  LeaderboardQueryResult leaderboard_rank(
      const std::string& user_id,
      std::chrono::milliseconds timeout = std::chrono::seconds(5));

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace boost_gateway::sdk
