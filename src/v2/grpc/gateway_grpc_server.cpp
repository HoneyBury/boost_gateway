// gateway_grpc_server.cpp — Gateway gRPC service implementation.
//
// Uses the async gRPC API with a single completion queue. Each incoming RPC
// spawns a CallData object that manages the per-call state machine:
//
//   CREATE -> PROCESS -> FINISH -> (reclaim or re-register)

#ifdef BOOST_BUILD_GRPC

#include "v2/grpc/gateway_grpc_server.h"

#include <algorithm>
#include <grpcpp/alarm.h>
#include <spdlog/spdlog.h>

#include "net/protocol.h"
#include "v2/gateway/gateway_command_parser.h"

namespace v2::grpc {

namespace {

template <typename Response>
::grpc::Status set_backend_failure(Response& response,
                                 const std::string& encoded_failure,
                                 const std::string& default_message) {
  const auto failure = decode_backend_failure(
      encoded_failure.empty() ? default_message : encoded_failure,
      v2::service::ServiceErrorCode::kUnavailable);
  response.set_error_code(v2::service::to_client_error(failure.code));
  response.set_error_message(
      failure.message.empty() ? default_message : failure.message);
  return ErrorCodeMapper::from_service_error(
      failure.code, response.error_message());
}

}  // namespace

// ===================================================================
// LoginCallData — handles RequestLogin RPCs
// ===================================================================
class LoginCallData final : public GatewayGrpcServer::CallData {
 public:
  LoginCallData(GatewayGrpcServer* server,
                boost::gateway::v3::Gateway::AsyncService* service,
                ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestRequestLogin(&ctx_, &request_, &responder_, cq_, cq_,
                                  this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      // Build response from request fields
      response_.set_user_id(request_.user_id());
      response_.set_display_name(request_.display_name());

      std::string error_msg;
      std::int32_t error_code = 0;

      // Delegate to the login auth callback if set
      if (server_->login_auth_) {
        const bool allowed = server_->login_auth_(
            request_.user_id(), request_.token(), error_msg);
        if (!allowed) {
          error_code = static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired);
          response_.set_error_code(error_code);
          response_.set_error_message(error_msg);
          goto finish;
        }
      }

      // Use the existing command parser for validation
      {
        const std::string raw_body = request_.user_id() + "|" +
                                     request_.token() + "|" +
                                     request_.display_name();
        const auto parsed = v2::gateway::parse_login_command_body(raw_body);
        if (!parsed.has_value() ||
            !v2::gateway::validate_login_command_body(*parsed)) {
          error_code = static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId);
          response_.set_error_code(error_code);
          response_.set_error_message(
              net::protocol::to_string(net::protocol::ErrorCode::kInvalidUserId));
          goto finish;
        }
      }

      // Success
      response_.set_error_code(0);
      response_.set_role("player");
      server_->active_sessions_.fetch_add(1, std::memory_order_relaxed);

      SPDLOG_INFO("GatewayGrpc: login ok user={}", request_.user_id());

    finish:
      responder_.Finish(response_, ErrorCodeMapper::from_error_code(error_code, error_msg),
                        this);
      status_ = FINISH;

    } else {
      // FINISH state: this CallData is done; spawn replacement if server lives
      if (server_->running()) {
        auto* replacement = new LoginCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };

  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;

  ::grpc::ServerContext ctx_;
  boost::gateway::v3::LoginRequest request_;
  boost::gateway::v3::LoginResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::LoginResponse> responder_;

  CallStatus status_;
};

// ===================================================================
// LogoutCallData — handles RequestLogout RPCs
// ===================================================================
class LogoutCallData final : public GatewayGrpcServer::CallData {
 public:
  LogoutCallData(GatewayGrpcServer* server,
                 boost::gateway::v3::Gateway::AsyncService* service,
                 ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestRequestLogout(&ctx_, &request_, &responder_, cq_, cq_,
                                   this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      response_.set_success(true);
      response_.set_error_code(0);

      if (server_->logout_cb_) {
        server_->logout_cb_(request_.user_id(), request_.session_id());
      }

      server_->active_sessions_.fetch_sub(1, std::memory_order_relaxed);

      SPDLOG_INFO("GatewayGrpc: logout user={}", request_.user_id());

      responder_.Finish(response_, ::grpc::Status::OK,
                        this);
      status_ = FINISH;

    } else {
      if (server_->running()) {
        auto* replacement = new LogoutCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };

  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;

  ::grpc::ServerContext ctx_;
  boost::gateway::v3::LogoutRequest request_;
  boost::gateway::v3::LogoutResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::LogoutResponse> responder_;

  CallStatus status_;
};

// ===================================================================
// HealthCallData — handles Health RPCs
// ===================================================================
class HealthCallData final : public GatewayGrpcServer::CallData {
 public:
  HealthCallData(GatewayGrpcServer* server,
                 boost::gateway::v3::Gateway::AsyncService* service,
                 ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestHealth(&ctx_, &request_, &responder_, cq_, cq_,
                            this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      response_.set_status("SERVING");
      response_.set_uptime_seconds(server_->uptime_seconds());
      response_.set_active_sessions(server_->active_sessions());

      responder_.Finish(response_, ::grpc::Status::OK,
                        this);
      status_ = FINISH;

    } else {
      if (server_->running()) {
        auto* replacement = new HealthCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };

  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;

  ::grpc::ServerContext ctx_;
  boost::gateway::v3::HealthRequest request_;
  boost::gateway::v3::HealthResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::HealthResponse> responder_;

  CallStatus status_;
};

class RoomCreateCallData final : public GatewayGrpcServer::CallData {
 public:
  RoomCreateCallData(GatewayGrpcServer* server,
                     boost::gateway::v3::Gateway::AsyncService* service,
                     ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestRequestRoomCreate(&ctx_, &request_, &responder_, cq_, cq_,
                                       this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::int32_t member_count = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->room_create_cb_ ||
          !server_->room_create_cb_(request_.user_id(), request_.room_id(), member_count, error)) {
        response_.set_room_id(request_.room_id());
        rpc_status = set_backend_failure(response_, error, "room_create_failed");
      } else {
        response_.set_room_id(request_.room_id());
        response_.set_member_count(member_count);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new RoomCreateCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::RoomCreateRequest request_;
  boost::gateway::v3::RoomCreateResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::RoomCreateResponse> responder_;
  CallStatus status_;
};

class RoomJoinCallData final : public GatewayGrpcServer::CallData {
 public:
  RoomJoinCallData(GatewayGrpcServer* server,
                   boost::gateway::v3::Gateway::AsyncService* service,
                   ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestRequestRoomJoin(&ctx_, &request_, &responder_, cq_, cq_,
                                     this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::int32_t member_count = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->room_join_cb_ ||
          !server_->room_join_cb_(request_.user_id(), request_.room_id(), member_count, error)) {
        response_.set_room_id(request_.room_id());
        rpc_status = set_backend_failure(response_, error, "room_join_failed");
      } else {
        response_.set_room_id(request_.room_id());
        response_.set_member_count(member_count);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new RoomJoinCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::RoomJoinRequest request_;
  boost::gateway::v3::RoomJoinResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::RoomJoinResponse> responder_;
  CallStatus status_;
};

class RoomLeaveCallData final : public GatewayGrpcServer::CallData {
 public:
  RoomLeaveCallData(GatewayGrpcServer* server,
                    boost::gateway::v3::Gateway::AsyncService* service,
                    ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestRequestRoomLeave(&ctx_, &request_, &responder_, cq_, cq_,
                                      this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      bool was_owner = false;
      std::string new_owner_id;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->room_leave_cb_ ||
          !server_->room_leave_cb_(request_.user_id(), request_.room_id(), was_owner, new_owner_id, error)) {
        response_.set_room_id(request_.room_id());
        rpc_status = set_backend_failure(response_, error, "room_leave_failed");
      } else {
        response_.set_room_id(request_.room_id());
        response_.set_was_owner(was_owner);
        response_.set_new_owner_id(new_owner_id);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new RoomLeaveCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::RoomLeaveRequest request_;
  boost::gateway::v3::RoomLeaveResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::RoomLeaveResponse> responder_;
  CallStatus status_;
};

class RoomReadyCallData final : public GatewayGrpcServer::CallData {
 public:
  RoomReadyCallData(GatewayGrpcServer* server,
                    boost::gateway::v3::Gateway::AsyncService* service,
                    ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_),
        status_(CREATE) {
    service_->RequestRequestRoomReady(&ctx_, &request_, &responder_, cq_, cq_,
                                      this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      bool all_ready = false;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->room_ready_cb_ ||
          !server_->room_ready_cb_(request_.user_id(), request_.room_id(), request_.ready(), all_ready, error)) {
        response_.set_room_id(request_.room_id());
        rpc_status = set_backend_failure(response_, error, "room_ready_failed");
      } else {
        response_.set_room_id(request_.room_id());
        response_.set_all_ready(all_ready);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new RoomReadyCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::RoomReadyRequest request_;
  boost::gateway::v3::RoomReadyResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::RoomReadyResponse> responder_;
  CallStatus status_;
};

class MatchJoinCallData final : public GatewayGrpcServer::CallData {
 public:
  MatchJoinCallData(GatewayGrpcServer* server,
                    boost::gateway::v3::Gateway::AsyncService* service,
                    ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestMatchJoin(&ctx_, &request_, &responder_, cq_, cq_,
                                      this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      bool queued = false;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->match_join_cb_ ||
          !server_->match_join_cb_(request_.user_id(), request_.mmr(), request_.mode(), queued, error)) {
        rpc_status = set_backend_failure(response_, error, "match_join_failed");
      } else {
        response_.set_queued(queued);
        response_.set_mode(request_.mode());
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new MatchJoinCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::MatchJoinRequest request_;
  boost::gateway::v3::MatchJoinResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::MatchJoinResponse> responder_;
  CallStatus status_;
};

class MatchLeaveCallData final : public GatewayGrpcServer::CallData {
 public:
  MatchLeaveCallData(GatewayGrpcServer* server,
                     boost::gateway::v3::Gateway::AsyncService* service,
                     ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestMatchLeave(&ctx_, &request_, &responder_, cq_, cq_,
                                       this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      bool left = false;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->match_leave_cb_ ||
          !server_->match_leave_cb_(request_.user_id(), request_.mode(), left, error)) {
        rpc_status = set_backend_failure(response_, error, "match_leave_failed");
      } else {
        response_.set_left(left);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new MatchLeaveCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::MatchLeaveRequest request_;
  boost::gateway::v3::MatchLeaveResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::MatchLeaveResponse> responder_;
  CallStatus status_;
};

class MatchStatusCallData final : public GatewayGrpcServer::CallData {
 public:
  MatchStatusCallData(GatewayGrpcServer* server,
                      boost::gateway::v3::Gateway::AsyncService* service,
                      ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestMatchStatus(&ctx_, &request_, &responder_, cq_, cq_,
                                        this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      bool matched = false;
      std::string match_id;
      std::int64_t avg_mmr = 0;
      std::int32_t queue_size = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->match_status_cb_ ||
          !server_->match_status_cb_(request_.user_id(), request_.mode(), matched, match_id, avg_mmr, queue_size, error)) {
        rpc_status = set_backend_failure(response_, error, "match_status_failed");
      } else {
        response_.set_matched(matched);
        response_.set_match_id(match_id);
        response_.set_mode(request_.mode());
        response_.set_avg_mmr(avg_mmr);
        response_.set_queue_size(queue_size);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new MatchStatusCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::MatchStatusRequest request_;
  boost::gateway::v3::MatchStatusResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::MatchStatusResponse> responder_;
  CallStatus status_;
};

class LeaderboardSubmitCallData final : public GatewayGrpcServer::CallData {
 public:
  LeaderboardSubmitCallData(GatewayGrpcServer* server,
                            boost::gateway::v3::Gateway::AsyncService* service,
                            ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestLeaderboardSubmit(&ctx_, &request_, &responder_, cq_, cq_,
                                              this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::int64_t rank = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->leaderboard_submit_cb_ ||
          !server_->leaderboard_submit_cb_(request_.user_id(), request_.display_name(), request_.score(), rank, error)) {
        rpc_status = set_backend_failure(response_, error, "leaderboard_submit_failed");
      } else {
        response_.set_user_id(request_.user_id());
        response_.set_rank(rank);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new LeaderboardSubmitCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::LeaderboardSubmitRequest request_;
  boost::gateway::v3::LeaderboardSubmitResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::LeaderboardSubmitResponse> responder_;
  CallStatus status_;
};

class LeaderboardTopCallData final : public GatewayGrpcServer::CallData {
 public:
  LeaderboardTopCallData(GatewayGrpcServer* server,
                         boost::gateway::v3::Gateway::AsyncService* service,
                         ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestLeaderboardTop(&ctx_, &request_, &responder_, cq_, cq_,
                                           this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::vector<boost::gateway::v3::LeaderboardEntry> entries;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->leaderboard_top_cb_ ||
          !server_->leaderboard_top_cb_(request_.k(), entries, error)) {
        rpc_status = set_backend_failure(response_, error, "leaderboard_top_failed");
      } else {
        for (const auto& entry : entries) {
          *response_.add_entries() = entry;
        }
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new LeaderboardTopCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::LeaderboardTopRequest request_;
  boost::gateway::v3::LeaderboardTopResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::LeaderboardTopResponse> responder_;
  CallStatus status_;
};

class LeaderboardRankCallData final : public GatewayGrpcServer::CallData {
 public:
  LeaderboardRankCallData(GatewayGrpcServer* server,
                          boost::gateway::v3::Gateway::AsyncService* service,
                          ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestLeaderboardRank(&ctx_, &request_, &responder_, cq_, cq_,
                                            this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::int64_t rank = 0;
      std::int64_t score = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->leaderboard_rank_cb_ ||
          !server_->leaderboard_rank_cb_(request_.user_id(), rank, score, error)) {
        rpc_status = set_backend_failure(response_, error, "leaderboard_rank_failed");
      } else {
        response_.set_user_id(request_.user_id());
        response_.set_rank(rank);
        response_.set_score(score);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new LeaderboardRankCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::LeaderboardRankRequest request_;
  boost::gateway::v3::LeaderboardRankResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::LeaderboardRankResponse> responder_;
  CallStatus status_;
};

class BattleCreateCallData final : public GatewayGrpcServer::CallData {
 public:
  BattleCreateCallData(GatewayGrpcServer* server,
                       boost::gateway::v3::Gateway::AsyncService* service,
                       ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestBattleCreate(&ctx_, &request_, &responder_, cq_, cq_,
                                         this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::vector<std::string> player_ids;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      player_ids.reserve(static_cast<size_t>(request_.player_ids_size()));
      for (const auto& player_id : request_.player_ids()) {
        player_ids.push_back(player_id);
      }
      if (!server_->battle_create_cb_ ||
          !server_->battle_create_cb_(request_.battle_id(), request_.room_id(), player_ids, request_.max_frames(), error)) {
        rpc_status = set_backend_failure(response_, error, "battle_create_failed");
      } else {
        response_.set_battle_id(request_.battle_id());
        response_.set_room_id(request_.room_id());
        for (const auto& player_id : request_.player_ids()) {
          response_.add_player_ids(player_id);
        }
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new BattleCreateCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::BattleCreateRequest request_;
  boost::gateway::v3::BattleCreateResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::BattleCreateResponse> responder_;
  CallStatus status_;
};

class BattleInputCallData final : public GatewayGrpcServer::CallData {
 public:
  BattleInputCallData(GatewayGrpcServer* server,
                      boost::gateway::v3::Gateway::AsyncService* service,
                      ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestBattleInput(&ctx_, &request_, &responder_, cq_, cq_,
                                        this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::uint64_t input_seq = 0;
      std::uint32_t frame_number = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->battle_input_cb_ ||
          !server_->battle_input_cb_(request_.user_id(), request_.battle_id(), request_.input_data(), request_.submitted_frame(), input_seq, frame_number, error)) {
        rpc_status = set_backend_failure(response_, error, "battle_input_failed");
      } else {
        response_.set_battle_id(request_.battle_id());
        response_.set_input_seq(input_seq);
        response_.set_frame_number(frame_number);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new BattleInputCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::BattleInputRequest request_;
  boost::gateway::v3::BattleInputResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::BattleInputResponse> responder_;
  CallStatus status_;
};

class BattleStateCallData final : public GatewayGrpcServer::CallData {
 public:
  BattleStateCallData(GatewayGrpcServer* server,
                      boost::gateway::v3::Gateway::AsyncService* service,
                      ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestBattleState(&ctx_, &request_, &responder_, cq_, cq_,
                                        this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::uint32_t frame_number = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->battle_state_cb_ ||
          !server_->battle_state_cb_(request_.battle_id(), frame_number, error)) {
        rpc_status = set_backend_failure(response_, error, "battle_state_failed");
      } else {
        response_.set_battle_id(request_.battle_id());
        response_.set_frame_number(frame_number);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new BattleStateCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::BattleStateRequest request_;
  boost::gateway::v3::BattleStateResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::BattleStateResponse> responder_;
  CallStatus status_;
};

class BattleStateStreamCallData final : public GatewayGrpcServer::CallData {
 private:
  enum class Event { kDone, kWrite, kTimer, kFinish };

  class OperationTag final : public GatewayGrpcServer::CompletionTag {
   public:
    OperationTag(BattleStateStreamCallData* owner, Event event)
        : owner_(owner), event_(event) {}

    void proceed(bool ok) override {
      owner_->on_event(event_, ok);
      owner_->complete_operation();
      delete this;
    }

   private:
    BattleStateStreamCallData* owner_;
    Event event_;
  };

 public:
  BattleStateStreamCallData(GatewayGrpcServer* server,
                            boost::gateway::v3::Gateway::AsyncService* service,
                            ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), writer_(&ctx_) {
    // gRPC requires this notification to be registered before the RPC starts.
    // If RequestStreamBattleState later completes with ok=false, this tag is
    // never delivered and is reclaimed by the request-failure branch below.
    begin_operation(Event::kDone);
    ctx_.AsyncNotifyWhenDone(last_tag_);
    service_->RequestStreamBattleState(&ctx_, &request_, &writer_, cq_, cq_, this);
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete last_tag_;
      --pending_operations_;
      delete this;
      return;
    }
    // Keep accepting new streaming clients while this call remains active.
    (void)new BattleStateStreamCallData(server_, service_, cq_);
    update_limit_ = std::min(request_.max_updates(), kMaximumUpdates);
    update_interval_ms_ = std::clamp(
        request_.update_interval_ms() == 0 ? kDefaultIntervalMs
                                           : request_.update_interval_ms(),
        kMinimumIntervalMs, kMaximumIntervalMs);
    stream_active_ = true;
    server_->on_battle_state_stream_started();
    write_update();
  }

 private:
  static constexpr std::uint32_t kMaximumUpdates = 100;
  static constexpr std::uint32_t kDefaultIntervalMs = 250;
  static constexpr std::uint32_t kMinimumIntervalMs = 100;
  static constexpr std::uint32_t kMaximumIntervalMs = 5000;

  void begin_operation(Event event) {
    ++pending_operations_;
    last_tag_ = new OperationTag(this, event);
  }

  void complete_operation() {
    --pending_operations_;
    if (pending_operations_ == 0 && finished_) {
      if (stream_active_) {
        server_->on_battle_state_stream_finished(stream_cancelled_);
        stream_active_ = false;
      }
      delete this;
    }
  }

  void on_event(Event event, bool ok) {
    switch (event) {
      case Event::kDone:
        if (ctx_.IsCancelled()) {
          client_done_ = true;
          if (timer_pending_) {
            alarm_.Cancel();
          }
          // A cancelled client has already terminated the RPC. Do not issue a
          // new Finish operation against a possibly shutting-down server; the
          // outstanding write/timer tags keep this object alive until the CQ
          // has drained them.
          abandon();
        }
        return;
      case Event::kWrite:
        write_pending_ = false;
        if (!ok || client_done_ || !server_->running()) {
          abandon();
          return;
        }
        ++updates_sent_;
        if (update_limit_ != 0 && updates_sent_ >= update_limit_) {
          finish(::grpc::Status::OK);
          return;
        }
        schedule_next_update();
        return;
      case Event::kTimer:
        timer_pending_ = false;
        if (!ok || client_done_ || !server_->running()) {
          abandon();
          return;
        }
        write_update();
        return;
      case Event::kFinish:
        finish_pending_ = false;
        finished_ = true;
        return;
    }
  }

  void abandon() {
    stream_cancelled_ = true;
    finished_ = true;
  }

  void schedule_next_update() {
    if (client_done_ || timer_pending_ || finish_pending_) {
      return;
    }
    timer_pending_ = true;
    begin_operation(Event::kTimer);
    alarm_.Set(cq_, std::chrono::system_clock::now() +
                        std::chrono::milliseconds(update_interval_ms_),
               last_tag_);
  }

  void finish(const ::grpc::Status& status) {
    if (finished_ || finish_pending_) {
      return;
    }
    finish_pending_ = true;
    begin_operation(Event::kFinish);
    writer_.Finish(status, last_tag_);
  }

  void write_update() {
    if (client_done_ || write_pending_ || finish_pending_ || !server_->running()) {
      if (!server_->running()) {
        abandon();
      }
      return;
    }
    std::string error;
    std::uint32_t frame_number = 0;
    if (!server_->battle_state_cb_ ||
        !server_->battle_state_cb_(request_.battle_id(), frame_number, error)) {
      finish(set_backend_failure(response_, error, "battle_state_stream_failed"));
      return;
    }

    response_.Clear();
    response_.set_battle_id(request_.battle_id());
    response_.set_frame_number(frame_number);
    response_.set_error_code(0);
    write_pending_ = true;
    begin_operation(Event::kWrite);
    writer_.Write(response_, last_tag_);
  }

  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::BattleStateRequest request_;
  boost::gateway::v3::BattleStateResponse response_;
  ::grpc::ServerAsyncWriter<boost::gateway::v3::BattleStateResponse> writer_;
  ::grpc::Alarm alarm_;
  OperationTag* last_tag_ = nullptr;
  std::uint32_t update_limit_ = 0;
  std::uint32_t updates_sent_ = 0;
  std::uint32_t update_interval_ms_ = kDefaultIntervalMs;
  std::uint32_t pending_operations_ = 0;
  bool client_done_ = false;
  bool write_pending_ = false;
  bool timer_pending_ = false;
  bool finish_pending_ = false;
  bool finished_ = false;
  bool stream_active_ = false;
  bool stream_cancelled_ = false;
};

class BattleFinishCallData final : public GatewayGrpcServer::CallData {
 public:
  BattleFinishCallData(GatewayGrpcServer* server,
                       boost::gateway::v3::Gateway::AsyncService* service,
                       ::grpc::ServerCompletionQueue* cq)
      : server_(server), service_(service), cq_(cq), responder_(&ctx_), status_(CREATE) {
    service_->RequestRequestBattleFinish(&ctx_, &request_, &responder_, cq_, cq_,
                                         this);
    status_ = PROCESS;
  }

  void proceed(bool ok) override {
    if (!ok) {
      delete this;
      return;
    }
    if (status_ == PROCESS) {
      std::string error;
      std::uint32_t total_frames = 0;
      ::grpc::Status rpc_status = ::grpc::Status::OK;
      if (!server_->battle_finish_cb_ ||
          !server_->battle_finish_cb_(request_.user_id(), request_.battle_id(), request_.reason(), total_frames, error)) {
        rpc_status = set_backend_failure(response_, error, "battle_finish_failed");
      } else {
        response_.set_battle_id(request_.battle_id());
        response_.set_reason(request_.reason());
        response_.set_total_frames(total_frames);
        response_.set_error_code(0);
      }
      responder_.Finish(response_, rpc_status,
                        this);
      status_ = FINISH;
    } else {
      if (server_->running()) {
        auto* replacement = new BattleFinishCallData(server_, service_, cq_);
        (void)replacement;
      }
      delete this;
    }
  }

 private:
  enum CallStatus { CREATE, PROCESS, FINISH };
  GatewayGrpcServer* server_;
  boost::gateway::v3::Gateway::AsyncService* service_;
  ::grpc::ServerCompletionQueue* cq_;
  ::grpc::ServerContext ctx_;
  boost::gateway::v3::BattleFinishRequest request_;
  boost::gateway::v3::BattleFinishResponse response_;
  ::grpc::ServerAsyncResponseWriter<boost::gateway::v3::BattleFinishResponse> responder_;
  CallStatus status_;
};

// ===================================================================
// GatewayGrpcServer implementation
// ===================================================================

GatewayGrpcServer::GatewayGrpcServer(
    std::uint16_t port,
    LoginAuthCallback login_auth,
    LogoutCallback logout_cb,
    RoomCreateCallback room_create_cb,
    RoomJoinCallback room_join_cb,
    RoomLeaveCallback room_leave_cb,
    RoomReadyCallback room_ready_cb,
    MatchJoinCallback match_join_cb,
    MatchLeaveCallback match_leave_cb,
    MatchStatusCallback match_status_cb,
    LeaderboardSubmitCallback leaderboard_submit_cb,
    LeaderboardTopCallback leaderboard_top_cb,
    LeaderboardRankCallback leaderboard_rank_cb,
    BattleCreateCallback battle_create_cb,
    BattleInputCallback battle_input_cb,
    BattleStateCallback battle_state_cb,
    BattleFinishCallback battle_finish_cb)
    : GrpcServer("GatewayGrpc", port),
      login_auth_(std::move(login_auth)),
      logout_cb_(std::move(logout_cb)),
      room_create_cb_(std::move(room_create_cb)),
      room_join_cb_(std::move(room_join_cb)),
      room_leave_cb_(std::move(room_leave_cb)),
      room_ready_cb_(std::move(room_ready_cb)),
      match_join_cb_(std::move(match_join_cb)),
      match_leave_cb_(std::move(match_leave_cb)),
      match_status_cb_(std::move(match_status_cb)),
      leaderboard_submit_cb_(std::move(leaderboard_submit_cb)),
      leaderboard_top_cb_(std::move(leaderboard_top_cb)),
      leaderboard_rank_cb_(std::move(leaderboard_rank_cb)),
      battle_create_cb_(std::move(battle_create_cb)),
      battle_input_cb_(std::move(battle_input_cb)),
      battle_state_cb_(std::move(battle_state_cb)),
      battle_finish_cb_(std::move(battle_finish_cb)) {}

GatewayGrpcServer::~GatewayGrpcServer() {
  shutdown();
}

void GatewayGrpcServer::register_services(::grpc::ServerBuilder& builder) {
  builder.RegisterService(&service_);
  cq_ = builder.AddCompletionQueue();
}

void GatewayGrpcServer::seed_completion_queue() {
  if (!cq_) {
    SPDLOG_WARN("GatewayGrpc: cannot seed CQ — not started");
    return;
  }

  // Create one CallData per RPC type so the CQ has initial handlers.
  auto* login = new LoginCallData(this, &service_, cq_.get());
  auto* logout = new LogoutCallData(this, &service_, cq_.get());
  auto* health = new HealthCallData(this, &service_, cq_.get());
  auto* room_create = new RoomCreateCallData(this, &service_, cq_.get());
  auto* room_join = new RoomJoinCallData(this, &service_, cq_.get());
  auto* room_leave = new RoomLeaveCallData(this, &service_, cq_.get());
  auto* room_ready = new RoomReadyCallData(this, &service_, cq_.get());
  auto* match_join = new MatchJoinCallData(this, &service_, cq_.get());
  auto* match_leave = new MatchLeaveCallData(this, &service_, cq_.get());
  auto* match_status = new MatchStatusCallData(this, &service_, cq_.get());
  auto* leaderboard_submit = new LeaderboardSubmitCallData(this, &service_, cq_.get());
  auto* leaderboard_top = new LeaderboardTopCallData(this, &service_, cq_.get());
  auto* leaderboard_rank = new LeaderboardRankCallData(this, &service_, cq_.get());
  auto* battle_create = new BattleCreateCallData(this, &service_, cq_.get());
  auto* battle_input = new BattleInputCallData(this, &service_, cq_.get());
  auto* battle_state = new BattleStateCallData(this, &service_, cq_.get());
  auto* battle_state_stream = new BattleStateStreamCallData(this, &service_, cq_.get());
  auto* battle_finish = new BattleFinishCallData(this, &service_, cq_.get());

  // Suppress unused-variable warnings — the CallData objects register
  // themselves with the AsyncService in their constructors.
  (void)login;
  (void)logout;
  (void)health;
  (void)room_create;
  (void)room_join;
  (void)room_leave;
  (void)room_ready;
  (void)match_join;
  (void)match_leave;
  (void)match_status;
  (void)leaderboard_submit;
  (void)leaderboard_top;
  (void)leaderboard_rank;
  (void)battle_create;
  (void)battle_input;
  (void)battle_state;
  (void)battle_state_stream;
  (void)battle_finish;

  SPDLOG_DEBUG("GatewayGrpc: CQ seeded with Login/Logout/Health/Room/Match/Leaderboard/Battle handlers");
}

}  // namespace v2::grpc

#endif  // BOOST_BUILD_GRPC
