#include "boost_gateway/sdk/grpc_client.h"

#include <algorithm>
#include <gateway.grpc.pb.h>
#include <grpcpp/grpcpp.h>
#include <nlohmann/json.hpp>

#include <chrono>
#include <memory>
#include <utility>

namespace boost_gateway::sdk {
namespace {

template <typename Result, typename Response>
Result result_from_response(const ::grpc::Status& status, const Response& response) {
  Result result;
  result.error_code = response.error_code() != 0
                          ? response.error_code()
                          : static_cast<std::int32_t>(status.error_code());
  result.error_message = !response.error_message().empty()
                             ? response.error_message()
                             : status.error_message();
  result.ok = status.ok() && result.error_code == 0;
  return result;
}

template <typename Result>
Result disconnected_result() {
  Result result;
  result.error_code = static_cast<std::int32_t>(::grpc::StatusCode::UNAVAILABLE);
  result.error_message = "grpc_not_connected";
  return result;
}

void set_deadline(::grpc::ClientContext& context, std::chrono::milliseconds timeout) {
  context.set_deadline(std::chrono::system_clock::now() + timeout);
}

std::shared_ptr<::grpc::ChannelCredentials> make_channel_credentials(
    const GrpcClientTlsOptions& tls_options) {
  if (!tls_options.enabled()) {
    return ::grpc::InsecureChannelCredentials();
  }
  ::grpc::SslCredentialsOptions options;
  options.pem_root_certs = tls_options.root_ca_pem;
  options.pem_cert_chain = tls_options.client_certificate_chain_pem;
  options.pem_private_key = tls_options.client_private_key_pem;
  return ::grpc::SslCredentials(options);
}

}  // namespace

class GrpcClient::Impl {
 public:
  std::shared_ptr<::grpc::Channel> channel;
  std::unique_ptr<boost::gateway::v3::Gateway::Stub> stub;
};

GrpcClient::GrpcClient() : impl_(std::make_unique<Impl>()) {}
GrpcClient::~GrpcClient() = default;

bool GrpcClient::connect(const std::string& host,
                         std::uint16_t port,
                         std::chrono::milliseconds timeout) {
  return connect_secure(host, port, {}, timeout);
}

bool GrpcClient::connect_secure(const std::string& host,
                                std::uint16_t port,
                                const GrpcClientTlsOptions& tls_options,
                                std::chrono::milliseconds timeout) {
  if (!tls_options.valid()) {
    disconnect();
    return false;
  }
  disconnect();
  const auto endpoint = host + ":" + std::to_string(port);
  auto channel = ::grpc::CreateChannel(endpoint, make_channel_credentials(tls_options));
  if (!channel->WaitForConnected(std::chrono::system_clock::now() + timeout)) {
    return false;
  }
  impl_->stub = boost::gateway::v3::Gateway::NewStub(channel);
  impl_->channel = std::move(channel);
  return true;
}

void GrpcClient::disconnect() {
  impl_->stub.reset();
  impl_->channel.reset();
}

bool GrpcClient::is_connected() const {
  return impl_->stub != nullptr;
}

LoginResult GrpcClient::login(const std::string& user_id,
                              const std::string& token,
                              const std::string& display_name,
                              std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<LoginResult>();
  boost::gateway::v3::LoginRequest request;
  request.set_user_id(user_id);
  request.set_token(token);
  request.set_display_name(display_name.empty() ? user_id : display_name);
  boost::gateway::v3::LoginResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<LoginResult>(
      impl_->stub->RequestLogin(&context, request, &response), response);
  result.user_id = response.user_id();
  result.display_name = response.display_name();
  return result;
}

RoomResult GrpcClient::create_room(const std::string& user_id,
                                   const std::string& room_id,
                                   std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<RoomResult>();
  boost::gateway::v3::RoomCreateRequest request;
  request.set_user_id(user_id);
  request.set_room_id(room_id);
  boost::gateway::v3::RoomCreateResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<RoomResult>(
      impl_->stub->RequestRoomCreate(&context, request, &response), response);
  result.room_id = response.room_id();
  result.member_count = response.member_count();
  return result;
}

RoomResult GrpcClient::join_room(const std::string& user_id,
                                 const std::string& room_id,
                                 std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<RoomResult>();
  boost::gateway::v3::RoomJoinRequest request;
  request.set_user_id(user_id);
  request.set_room_id(room_id);
  boost::gateway::v3::RoomJoinResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<RoomResult>(
      impl_->stub->RequestRoomJoin(&context, request, &response), response);
  result.room_id = response.room_id();
  result.member_count = response.member_count();
  return result;
}

RoomResult GrpcClient::set_ready(const std::string& user_id,
                                 const std::string& room_id,
                                 bool ready,
                                 std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<RoomResult>();
  boost::gateway::v3::RoomReadyRequest request;
  request.set_user_id(user_id);
  request.set_room_id(room_id);
  request.set_ready(ready);
  boost::gateway::v3::RoomReadyResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<RoomResult>(
      impl_->stub->RequestRoomReady(&context, request, &response), response);
  result.room_id = response.room_id();
  return result;
}

BattleStartResult GrpcClient::create_battle(const std::string& battle_id,
                                            const std::string& room_id,
                                            const std::vector<std::string>& player_ids,
                                            std::uint32_t max_frames,
                                            std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<BattleStartResult>();
  boost::gateway::v3::BattleCreateRequest request;
  request.set_battle_id(battle_id);
  request.set_room_id(room_id);
  request.set_max_frames(max_frames);
  for (const auto& player_id : player_ids) request.add_player_ids(player_id);
  boost::gateway::v3::BattleCreateResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<BattleStartResult>(
      impl_->stub->RequestBattleCreate(&context, request, &response), response);
  result.battle_id = response.battle_id();
  return result;
}

BattleInputResult GrpcClient::send_battle_input(const std::string& user_id,
                                                const std::string& battle_id,
                                                const std::string& input_data,
                                                std::uint32_t submitted_frame,
                                                std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<BattleInputResult>();
  boost::gateway::v3::BattleInputRequest request;
  request.set_user_id(user_id);
  request.set_battle_id(battle_id);
  request.set_input_data(input_data);
  request.set_submitted_frame(submitted_frame);
  boost::gateway::v3::BattleInputResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<BattleInputResult>(
      impl_->stub->RequestBattleInput(&context, request, &response), response);
  result.input_seq = response.input_seq();
  return result;
}

BattleStateResult GrpcClient::battle_state(const std::string& battle_id,
                                           std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<BattleStateResult>();
  boost::gateway::v3::BattleStateRequest request;
  request.set_battle_id(battle_id);
  boost::gateway::v3::BattleStateResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<BattleStateResult>(
      impl_->stub->RequestBattleState(&context, request, &response), response);
  result.response_body = nlohmann::json{
      {"battle_id", response.battle_id()}, {"frame_number", response.frame_number()}}.dump();
  return result;
}

BattleStateStreamResult GrpcClient::stream_battle_state(
    const std::string& battle_id,
    std::size_t update_count,
    std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<BattleStateStreamResult>();
  if (update_count == 0) {
    BattleStateStreamResult result;
    result.error_code = static_cast<std::int32_t>(::grpc::StatusCode::INVALID_ARGUMENT);
    result.error_message = "grpc_stream_update_count_must_be_positive";
    return result;
  }

  boost::gateway::v3::BattleStateRequest request;
  request.set_battle_id(battle_id);
  request.set_max_updates(static_cast<std::uint32_t>(update_count));
  request.set_update_interval_ms(100);
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto reader = impl_->stub->StreamBattleState(&context, request);

  BattleStateStreamResult result;
  boost::gateway::v3::BattleStateResponse response;
  while (result.updates.size() < update_count && reader->Read(&response)) {
    auto update = result_from_response<BattleStateResult>(::grpc::Status::OK, response);
    update.response_body = nlohmann::json{
        {"battle_id", response.battle_id()}, {"frame_number", response.frame_number()}}.dump();
    if (!update.ok) {
      result.error_code = update.error_code;
      result.error_message = update.error_message;
      break;
    }
    result.updates.push_back(std::move(update));
    response.Clear();
  }

  const auto status = reader->Finish();
  result.ok = status.ok() && result.updates.size() == update_count;
  if (!result.ok && result.error_code == 0) {
    result.error_code = static_cast<std::int32_t>(status.error_code());
    result.error_message = status.error_message();
  }
  return result;
}

BattleStateStreamResult GrpcClient::subscribe_battle_state(
    const std::string& battle_id,
    BattleStateUpdateCallback on_update,
    std::chrono::milliseconds update_interval,
    std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<BattleStateStreamResult>();
  if (!on_update) {
    BattleStateStreamResult result;
    result.error_code = static_cast<std::int32_t>(::grpc::StatusCode::INVALID_ARGUMENT);
    result.error_message = "grpc_stream_update_callback_required";
    return result;
  }

  boost::gateway::v3::BattleStateRequest request;
  request.set_battle_id(battle_id);
  request.set_max_updates(0);
  request.set_update_interval_ms(static_cast<std::uint32_t>(
      std::clamp<std::int64_t>(update_interval.count(), 1, 5000)));
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto reader = impl_->stub->StreamBattleState(&context, request);

  BattleStateStreamResult result;
  bool locally_cancelled = false;
  boost::gateway::v3::BattleStateResponse response;
  while (reader->Read(&response)) {
    auto update = result_from_response<BattleStateResult>(::grpc::Status::OK, response);
    update.response_body = nlohmann::json{
        {"battle_id", response.battle_id()}, {"frame_number", response.frame_number()}}.dump();
    if (!update.ok) {
      result.error_code = update.error_code;
      result.error_message = update.error_message;
      break;
    }
    result.updates.push_back(std::move(update));
    if (!on_update(result.updates.back())) {
      locally_cancelled = true;
      context.TryCancel();
      break;
    }
    response.Clear();
  }

  const auto status = reader->Finish();
  result.ok = locally_cancelled ? !result.updates.empty() : status.ok();
  if (!result.ok && result.error_code == 0) {
    result.error_code = static_cast<std::int32_t>(status.error_code());
    result.error_message = status.error_message();
  }
  return result;
}

BattleFinishResult GrpcClient::finish_battle(const std::string& user_id,
                                             const std::string& battle_id,
                                             const std::string& reason,
                                             std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<BattleFinishResult>();
  boost::gateway::v3::BattleFinishRequest request;
  request.set_user_id(user_id);
  request.set_battle_id(battle_id);
  request.set_reason(reason);
  boost::gateway::v3::BattleFinishResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<BattleFinishResult>(
      impl_->stub->RequestBattleFinish(&context, request, &response), response);
  result.battle_id = response.battle_id();
  result.total_frames = response.total_frames();
  return result;
}

LeaderboardSubmitResult GrpcClient::leaderboard_submit(
    const std::string& user_id,
    const std::string& display_name,
    std::int64_t score,
    std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<LeaderboardSubmitResult>();
  boost::gateway::v3::LeaderboardSubmitRequest request;
  request.set_user_id(user_id);
  request.set_display_name(display_name);
  request.set_score(score);
  boost::gateway::v3::LeaderboardSubmitResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<LeaderboardSubmitResult>(
      impl_->stub->RequestLeaderboardSubmit(&context, request, &response), response);
  result.response_body = nlohmann::json{
      {"user_id", response.user_id()}, {"rank", response.rank()}}.dump();
  return result;
}

LeaderboardQueryResult GrpcClient::leaderboard_rank(
    const std::string& user_id,
    std::chrono::milliseconds timeout) {
  if (!impl_->stub) return disconnected_result<LeaderboardQueryResult>();
  boost::gateway::v3::LeaderboardRankRequest request;
  request.set_user_id(user_id);
  boost::gateway::v3::LeaderboardRankResponse response;
  ::grpc::ClientContext context;
  set_deadline(context, timeout);
  auto result = result_from_response<LeaderboardQueryResult>(
      impl_->stub->RequestLeaderboardRank(&context, request, &response), response);
  result.response_body = nlohmann::json{
      {"user_id", response.user_id()}, {"rank", response.rank()}, {"score", response.score()}}.dump();
  return result;
}

}  // namespace boost_gateway::sdk
