#pragma once

// gateway_grpc_server.h — Gateway gRPC service implementation.
//
// Implements the Gateway::AsyncService with RequestLogin and RequestLogout
// RPCs. Delegates business logic to the existing gateway command parser and
// validation layer from v2/gateway/gateway_command_parser.h.
//
// Conditionally compiled only when BOOST_BUILD_GRPC is defined.

#ifdef BOOST_BUILD_GRPC

#include <atomic>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include <grpcpp/grpcpp.h>
#include <grpcpp/server_context.h>

#include "v2/grpc/grpc_server.h"

// Generated protobuf/gRPC headers — resolved via project_proto include path
#include <gateway.pb.h>
#include <gateway.grpc.pb.h>

namespace v2::grpc {

// -------------------------------------------------------------------
// GatewayGrpcServer: implements the Gateway gRPC service.
//
// Lifecycle:
//   1. Construct with a port and optional callback.
//   2. Call start() to begin serving.
//   3. Call shutdown() for graceful stop.
//
// The async gRPC API uses per-RPC CallData objects. Each CallData
// registers itself with the AsyncService to receive the next incoming
// request of its type. When a request arrives and is processed, the
// CallData spawns a replacement to handle the next request.
//
// External code drives the completion queue via:
//   auto* cq = server.completion_queue();
//   void* tag; bool ok;
//   while (cq->Next(&tag, &ok)) {
//       static_cast<GatewayGrpcServer::CallData*>(tag)->proceed(ok);
//   }
// -------------------------------------------------------------------
class GatewayGrpcServer final : public GrpcServer {
 public:
  /// Abstract base for per-RPC state machines.
  class CallData {
   public:
    virtual ~CallData() = default;
    /// Drive the state machine (called by the CQ polling loop).
    virtual void proceed(bool ok) = 0;
  };

  /// Callback invoked on successful login. Returns true if the login
  /// should proceed, false to reject. When set, the callback runs before
  /// the default validation logic.
  using LoginAuthCallback = std::function<bool(
      const std::string& user_id,
      const std::string& token,
      std::string& out_error)>;

  /// Callback invoked on logout.
  using LogoutCallback = std::function<void(
      const std::string& user_id,
      const std::string& session_id)>;

  using RoomCreateCallback = std::function<bool(
      const std::string& user_id,
      const std::string& room_id,
      std::int32_t& out_member_count,
      std::string& out_error)>;

  using RoomJoinCallback = std::function<bool(
      const std::string& user_id,
      const std::string& room_id,
      std::int32_t& out_member_count,
      std::string& out_error)>;

  using RoomLeaveCallback = std::function<bool(
      const std::string& user_id,
      const std::string& room_id,
      bool& out_was_owner,
      std::string& out_new_owner_id,
      std::string& out_error)>;

  using RoomReadyCallback = std::function<bool(
      const std::string& user_id,
      const std::string& room_id,
      bool ready,
      bool& out_all_ready,
      std::string& out_error)>;

  using MatchJoinCallback = std::function<bool(
      const std::string& user_id,
      std::int64_t mmr,
      const std::string& mode,
      bool& out_queued,
      std::string& out_error)>;

  using MatchLeaveCallback = std::function<bool(
      const std::string& user_id,
      const std::string& mode,
      bool& out_left,
      std::string& out_error)>;

  using MatchStatusCallback = std::function<bool(
      const std::string& user_id,
      const std::string& mode,
      bool& out_matched,
      std::string& out_match_id,
      std::int64_t& out_avg_mmr,
      std::int32_t& out_queue_size,
      std::string& out_error)>;

  using LeaderboardSubmitCallback = std::function<bool(
      const std::string& user_id,
      const std::string& display_name,
      std::int64_t score,
      std::int64_t& out_rank,
      std::string& out_error)>;

  using LeaderboardTopCallback = std::function<bool(
      std::int32_t k,
      std::vector<boost::gateway::v3::LeaderboardEntry>& out_entries,
      std::string& out_error)>;

  using LeaderboardRankCallback = std::function<bool(
      const std::string& user_id,
      std::int64_t& out_rank,
      std::int64_t& out_score,
      std::string& out_error)>;

  using BattleCreateCallback = std::function<bool(
      const std::string& battle_id,
      const std::string& room_id,
      const std::vector<std::string>& player_ids,
      std::uint32_t max_frames,
      std::string& out_error)>;

  using BattleInputCallback = std::function<bool(
      const std::string& user_id,
      const std::string& battle_id,
      const std::string& input_data,
      std::uint32_t submitted_frame,
      std::uint64_t& out_input_seq,
      std::uint32_t& out_frame_number,
      std::string& out_error)>;

  using BattleStateCallback = std::function<bool(
      const std::string& battle_id,
      std::uint32_t& out_frame_number,
      std::string& out_error)>;

  using BattleFinishCallback = std::function<bool(
      const std::string& user_id,
      const std::string& battle_id,
      const std::string& reason,
      std::uint32_t& out_total_frames,
      std::string& out_error)>;

  explicit GatewayGrpcServer(
      std::uint16_t port,
      LoginAuthCallback login_auth = nullptr,
      LogoutCallback logout_cb = nullptr,
      RoomCreateCallback room_create_cb = nullptr,
      RoomJoinCallback room_join_cb = nullptr,
      RoomLeaveCallback room_leave_cb = nullptr,
      RoomReadyCallback room_ready_cb = nullptr,
      MatchJoinCallback match_join_cb = nullptr,
      MatchLeaveCallback match_leave_cb = nullptr,
      MatchStatusCallback match_status_cb = nullptr,
      LeaderboardSubmitCallback leaderboard_submit_cb = nullptr,
      LeaderboardTopCallback leaderboard_top_cb = nullptr,
      LeaderboardRankCallback leaderboard_rank_cb = nullptr,
      BattleCreateCallback battle_create_cb = nullptr,
      BattleInputCallback battle_input_cb = nullptr,
      BattleStateCallback battle_state_cb = nullptr,
      BattleFinishCallback battle_finish_cb = nullptr);

  ~GatewayGrpcServer() override;

  // Non-copyable, non-movable.
  GatewayGrpcServer(const GatewayGrpcServer&) = delete;
  GatewayGrpcServer& operator=(const GatewayGrpcServer&) = delete;

  /// Number of currently active (authenticated) sessions tracked by this
  /// server. Used for the health check response.
  std::uint32_t active_sessions() const noexcept { return active_sessions_; }

  /// Access the completion queue for external polling.
  grpc::ServerCompletionQueue* completion_queue() noexcept { return cq_.get(); }

  /// Access the async service (for seeding initial CallData objects).
  boost::gateway::v3::Gateway::AsyncService& async_service() noexcept {
    return service_;
  }

  /// Seed the completion queue with initial CallData instances for each
  /// RPC type. Must be called after start() and before the CQ poll loop.
  void seed_completion_queue();

 private:
  // GrpcServer interface
  void register_services(grpc::ServerBuilder& builder) override;

  boost::gateway::v3::Gateway::AsyncService service_;
  std::unique_ptr<grpc::ServerCompletionQueue> cq_;

  LoginAuthCallback login_auth_;
  LogoutCallback logout_cb_;
  RoomCreateCallback room_create_cb_;
  RoomJoinCallback room_join_cb_;
  RoomLeaveCallback room_leave_cb_;
  RoomReadyCallback room_ready_cb_;
  MatchJoinCallback match_join_cb_;
  MatchLeaveCallback match_leave_cb_;
  MatchStatusCallback match_status_cb_;
  LeaderboardSubmitCallback leaderboard_submit_cb_;
  LeaderboardTopCallback leaderboard_top_cb_;
  LeaderboardRankCallback leaderboard_rank_cb_;
  BattleCreateCallback battle_create_cb_;
  BattleInputCallback battle_input_cb_;
  BattleStateCallback battle_state_cb_;
  BattleFinishCallback battle_finish_cb_;

  std::atomic<std::uint32_t> active_sessions_{0};
};

}  // namespace v2::grpc

#endif  // BOOST_BUILD_GRPC
