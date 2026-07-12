#ifdef BOOST_BUILD_GRPC

#include <gtest/gtest.h>

#include <grpcpp/grpcpp.h>

#include "boost_gateway/sdk/grpc_client.h"
#include "gateway.grpc.pb.h"
#include "v2/battle/battle_backend_service.h"
#include "v2/grpc/grpc_adapter.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v2/login/login_backend_service.h"
#include "v2/match/matchmaking_service.h"
#include "v2/room/room_backend_service.h"

#include <chrono>
#include <cstdlib>
#include <memory>
#include <optional>
#include <string>
#include <thread>

namespace {

class DisableRedisAutoConnectForGrpcTest {
public:
    DisableRedisAutoConnectForGrpcTest() {
        if (const char* value = std::getenv("BOOST_DISABLE_REDIS_AUTO_CONNECT")) {
            previous_value_ = value;
        }
        setenv("BOOST_DISABLE_REDIS_AUTO_CONNECT", "1", 1);
    }

    ~DisableRedisAutoConnectForGrpcTest() {
        if (previous_value_.has_value()) {
            setenv("BOOST_DISABLE_REDIS_AUTO_CONNECT", previous_value_->c_str(), 1);
        } else {
            unsetenv("BOOST_DISABLE_REDIS_AUTO_CONNECT");
        }
    }

private:
    std::optional<std::string> previous_value_;
};

v2::gateway::GatewayServiceBridge::BackendConfig backend_config(std::uint16_t port) {
    return {
        .host = "127.0.0.1",
        .port = port,
        .timeout = std::chrono::milliseconds(2000),
        .connect_timeout = std::chrono::milliseconds(500),
    };
}

class GrpcGatewayAdapterE2ETest : public ::testing::Test {
protected:
    void SetUp() override {
        login_ = std::make_unique<v2::login::LoginBackendService>(0);
        room_ = std::make_unique<v2::room::RoomBackendService>(0);
        battle_ = std::make_unique<v2::battle::BattleBackendService>(0);
        match_ = std::make_unique<v2::match::MatchmakingService>(0);
        leaderboard_ = std::make_unique<v2::leaderboard::LeaderboardService>(0);

        v2::match::MatchmakingConfig match_config;
        match_config.match_check_interval_ms = 1000;
        match_->set_matchmaking_config(match_config);

        login_->start();
        room_->start();
        battle_->start();
        match_->start();
        leaderboard_->start();

        adapter_ = std::make_unique<v2::grpc::GrpcGatewayAdapter>(
            0,
            v2::grpc::GrpcGatewayAdapter::BackendOptions{
                .login_backend_config = backend_config(login_->local_port()),
                .room_backend_config = backend_config(room_->local_port()),
                .battle_backend_config = backend_config(battle_->local_port()),
                .matchmaking_backend_config = backend_config(match_->local_port()),
                .leaderboard_backend_config = backend_config(leaderboard_->local_port()),
            });
        ASSERT_TRUE(adapter_->start());
        ASSERT_GT(adapter_->port(), 0U);

        channel_ = grpc::CreateChannel(
            "127.0.0.1:" + std::to_string(adapter_->port()),
            grpc::InsecureChannelCredentials());
        stub_ = boost::gateway::v3::Gateway::NewStub(channel_);
    }

    void TearDown() override {
        stub_.reset();
        channel_.reset();
        if (adapter_) adapter_->stop();
        if (leaderboard_) leaderboard_->stop();
        if (match_) match_->stop();
        if (room_) room_->stop();
        if (battle_) battle_->stop();
        if (login_) login_->stop();
    }

    DisableRedisAutoConnectForGrpcTest disable_redis_auto_connect_;
    std::unique_ptr<v2::login::LoginBackendService> login_;
    std::unique_ptr<v2::room::RoomBackendService> room_;
    std::unique_ptr<v2::battle::BattleBackendService> battle_;
    std::unique_ptr<v2::match::MatchmakingService> match_;
    std::unique_ptr<v2::leaderboard::LeaderboardService> leaderboard_;
    std::unique_ptr<v2::grpc::GrpcGatewayAdapter> adapter_;
    std::shared_ptr<grpc::Channel> channel_;
    std::unique_ptr<boost::gateway::v3::Gateway::Stub> stub_;
};

TEST_F(GrpcGatewayAdapterE2ETest, RoutesRoomMatchAndLeaderboardToRealBackends) {
    {
        grpc::ClientContext context;
        boost::gateway::v3::LoginRequest request;
        request.set_user_id("alice");
        request.set_token("token:alice");
        request.set_display_name("Alice");
        boost::gateway::v3::LoginResponse response;
        const auto status = stub_->RequestLogin(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
    }

    {
        grpc::ClientContext context;
        boost::gateway::v3::RoomCreateRequest request;
        request.set_user_id("alice");
        request.set_room_id("grpc-room");
        boost::gateway::v3::RoomCreateResponse response;
        const auto status = stub_->RequestRoomCreate(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        EXPECT_EQ(response.member_count(), 1);
    }
    {
        grpc::ClientContext context;
        boost::gateway::v3::RoomJoinRequest request;
        request.set_user_id("bob");
        request.set_room_id("grpc-room");
        boost::gateway::v3::RoomJoinResponse response;
        const auto status = stub_->RequestRoomJoin(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        EXPECT_EQ(response.member_count(), 2);
    }
    {
        grpc::ClientContext context;
        boost::gateway::v3::RoomJoinRequest request;
        request.set_user_id("carol");
        request.set_room_id("missing-room");
        boost::gateway::v3::RoomJoinResponse response;
        const auto status = stub_->RequestRoomJoin(&context, request, &response);
        EXPECT_EQ(status.error_code(), grpc::StatusCode::NOT_FOUND);
        EXPECT_FALSE(status.error_message().empty());
    }

    {
        grpc::ClientContext context;
        boost::gateway::v3::MatchJoinRequest request;
        request.set_user_id("alice");
        request.set_mmr(1200);
        request.set_mode("1v1");
        boost::gateway::v3::MatchJoinResponse response;
        const auto status = stub_->RequestMatchJoin(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        EXPECT_TRUE(response.queued());
    }
    {
        grpc::ClientContext context;
        boost::gateway::v3::MatchLeaveRequest request;
        request.set_user_id("alice");
        request.set_mode("1v1");
        boost::gateway::v3::MatchLeaveResponse response;
        const auto status = stub_->RequestMatchLeave(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        EXPECT_TRUE(response.left());
    }

    {
        grpc::ClientContext context;
        boost::gateway::v3::LeaderboardSubmitRequest request;
        request.set_user_id("alice");
        request.set_display_name("Alice");
        request.set_score(1200);
        boost::gateway::v3::LeaderboardSubmitResponse response;
        const auto status = stub_->RequestLeaderboardSubmit(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        EXPECT_GT(response.rank(), 0);
    }
    {
        grpc::ClientContext context;
        boost::gateway::v3::LeaderboardRankRequest request;
        request.set_user_id("alice");
        boost::gateway::v3::LeaderboardRankResponse response;
        const auto status = stub_->RequestLeaderboardRank(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        EXPECT_EQ(response.user_id(), "alice");
        EXPECT_EQ(response.score(), 1200);
    }
    {
        grpc::ClientContext context;
        boost::gateway::v3::LeaderboardTopRequest request;
        request.set_k(1);
        boost::gateway::v3::LeaderboardTopResponse response;
        const auto status = stub_->RequestLeaderboardTop(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
        ASSERT_EQ(response.entries_size(), 1);
        EXPECT_EQ(response.entries(0).user_id(), "alice");
    }
}

TEST_F(GrpcGatewayAdapterE2ETest, SdkClientRoutesRoomBattleAndLeaderboard) {
    boost_gateway::sdk::GrpcClient client;
    ASSERT_TRUE(client.connect("127.0.0.1", adapter_->port()));

    ASSERT_TRUE(client.login("sdk-alice", "token:sdk-alice", "SDK Alice").ok);
    ASSERT_TRUE(client.create_room("sdk-alice", "sdk-grpc-room").ok);
    ASSERT_TRUE(client.join_room("sdk-bob", "sdk-grpc-room").ok);
    ASSERT_TRUE(client.set_ready("sdk-alice", "sdk-grpc-room", true).ok);
    ASSERT_TRUE(client.set_ready("sdk-bob", "sdk-grpc-room", true).ok);

    const auto battle = client.create_battle(
        "sdk-grpc-battle", "sdk-grpc-room", {"sdk-alice", "sdk-bob"}, 60);
    ASSERT_TRUE(battle.ok) << battle.error_message;
    EXPECT_EQ(battle.battle_id, "sdk-grpc-battle");

    const auto input = client.send_battle_input(
        "sdk-alice", "sdk-grpc-battle", "move:10,20", 1);
    ASSERT_TRUE(input.ok) << input.error_message;
    EXPECT_GT(input.input_seq, 0U);

    const auto state = client.battle_state("sdk-grpc-battle");
    ASSERT_TRUE(state.ok) << state.error_message;
    EXPECT_NE(state.response_body.find("sdk-grpc-battle"), std::string::npos);

    const auto stream_started = std::chrono::steady_clock::now();
    const auto stream = client.stream_battle_state("sdk-grpc-battle", 2);
    const auto stream_elapsed = std::chrono::steady_clock::now() - stream_started;
    ASSERT_TRUE(stream.ok) << stream.error_message;
    ASSERT_EQ(stream.updates.size(), 2U);
    EXPECT_NE(stream.updates.front().response_body.find("sdk-grpc-battle"), std::string::npos);
    EXPECT_GE(stream_elapsed, std::chrono::milliseconds(80));

    std::size_t subscription_updates = 0;
    const auto subscription_started = std::chrono::steady_clock::now();
    const auto subscription = client.subscribe_battle_state(
        "sdk-grpc-battle",
        [&subscription_updates](const boost_gateway::sdk::BattleStateResult&) {
            return ++subscription_updates < 2;
        },
        std::chrono::milliseconds(1));
    const auto subscription_elapsed = std::chrono::steady_clock::now() - subscription_started;
    ASSERT_TRUE(subscription.ok) << subscription.error_message;
    EXPECT_EQ(subscription.updates.size(), 2U);
    EXPECT_GE(subscription_elapsed, std::chrono::milliseconds(80));

    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    const auto stream_metrics = adapter_->battle_state_stream_metrics();
    EXPECT_EQ(stream_metrics.started, 2U);
    EXPECT_EQ(stream_metrics.active, 0U);
    EXPECT_EQ(stream_metrics.completed, 1U);
    EXPECT_EQ(stream_metrics.cancelled, 1U);

    const auto finish = client.finish_battle(
        "sdk-alice", "sdk-grpc-battle", "user_requested");
    ASSERT_TRUE(finish.ok) << finish.error_message;
    EXPECT_EQ(finish.battle_id, "sdk-grpc-battle");

    const auto rejected = client.send_battle_input(
        "sdk-alice", "sdk-grpc-battle", "move:20,30", 2);
    EXPECT_FALSE(rejected.ok);
    EXPECT_NE(rejected.error_code, 0);

    const auto submit = client.leaderboard_submit("sdk-alice", "SDK Alice", 1700);
    ASSERT_TRUE(submit.ok) << submit.error_message;
    const auto rank = client.leaderboard_rank("sdk-alice");
    ASSERT_TRUE(rank.ok) << rank.error_message;
    EXPECT_NE(rank.response_body.find("1700"), std::string::npos);

    const auto battle_metrics = adapter_->backend_metrics_snapshot(
        v2::service::ServiceId::kBattle);
    EXPECT_GE(battle_metrics.total_requests, 8U);
    EXPECT_GE(battle_metrics.total_successes, 8U);
    EXPECT_GE(battle_metrics.latency_sample_count, 8U);

    client.disconnect();
}

}  // namespace

#endif  // BOOST_BUILD_GRPC
