#ifdef BOOST_BUILD_GRPC

#include <gtest/gtest.h>

#include <boost/asio.hpp>
#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/http/message.hpp>
#include <boost/beast/http/read.hpp>
#include <boost/beast/http/string_body.hpp>
#include <boost/beast/http/write.hpp>
#include <grpcpp/grpcpp.h>

#include "boost_gateway/sdk/grpc_client.h"
#include "gateway.grpc.pb.h"
#include "v2/battle/battle_backend_service.h"
#include "v2/grpc/grpc_adapter.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v2/login/login_backend_service.h"
#include "v2/match/matchmaking_service.h"
#include "v2/room/room_backend_service.h"

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>

namespace {

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
using tcp = asio::ip::tcp;

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

std::string read_text_file(const std::filesystem::path& path) {
    std::ifstream input(path);
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

std::filesystem::path make_temp_dir(const std::string& prefix) {
    const auto unique = prefix + "-" +
        std::to_string(std::chrono::steady_clock::now().time_since_epoch().count());
    auto dir = std::filesystem::temp_directory_path() / unique;
    std::filesystem::create_directories(dir);
    return dir;
}

bool generate_dev_certs(const std::filesystem::path& output_dir,
                        bool include_client,
                        std::string& out_error) {
    const std::string script = std::string(PROJECT_SOURCE_DIR) + "/scripts/tools/gen_certs.py";
    std::string command = "python3 \"" + script + "\" --output-dir \"" + output_dir.string() + "\"";
    if (include_client) {
        command += " --include-client";
    }
    if (std::system(command.c_str()) != 0) {
        out_error = "failed to generate dev certificates";
        return false;
    }
    return true;
}

struct FakeOtlpCollector {
    asio::io_context io_context;
    tcp::acceptor acceptor{io_context};
    std::thread thread;
    std::mutex mutex;
    std::string last_target;
    std::string last_body;
    std::size_t request_count = 0;
    std::atomic<bool> running{false};

    bool start() {
        try {
            acceptor.open(tcp::v4());
            acceptor.set_option(asio::socket_base::reuse_address(true));
            acceptor.bind({tcp::v4(), 0});
            acceptor.listen();
            running = true;
            thread = std::thread([this]() { run(); });
            return true;
        } catch (...) {
            return false;
        }
    }

    void stop() {
        running = false;
        boost::system::error_code ignored_ec;
        if (acceptor.is_open()) {
            tcp::socket wake_socket(io_context);
            wake_socket.connect(
                tcp::endpoint(asio::ip::make_address("127.0.0.1"), port()),
                ignored_ec);
            wake_socket.close(ignored_ec);
        }
        if (thread.joinable()) {
            thread.join();
        }
        acceptor.close(ignored_ec);
        io_context.stop();
    }

    std::uint16_t port() const {
        return acceptor.local_endpoint().port();
    }

private:
    void run() {
        while (running) {
            boost::system::error_code ec;
            tcp::socket socket(io_context);
            acceptor.accept(socket, ec);
            if (ec) {
                if (!running) {
                    return;
                }
                continue;
            }

            beast::flat_buffer buffer;
            http::request<http::string_body> request;
            http::read(socket, buffer, request, ec);
            if (!ec) {
                std::lock_guard lock(mutex);
                last_target = std::string(request.target());
                last_body = request.body();
                ++request_count;
            }

            http::response<http::string_body> response{http::status::ok, 11};
            response.set(http::field::content_type, "application/json");
            response.body() = R"({"ok":true})";
            response.prepare_payload();
            http::write(socket, response, ec);
            socket.shutdown(tcp::socket::shutdown_both, ec);
        }
    }
};

class GrpcGatewayAdapterE2ETest : public ::testing::Test {
protected:
    using SecurityOptions = v2::grpc::GrpcGatewayAdapter::SecurityOptions;
    using AuthenticatedPrincipal = v2::grpc::GatewayGrpcServer::AuthenticatedPrincipal;
    using ObservabilityOptions = v2::grpc::GrpcGatewayAdapter::ObservabilityOptions;

    virtual bool prepare_security_materials(std::string& out_error) {
        (void)out_error;
        return true;
    }

    virtual SecurityOptions adapter_security_options() const {
        return {};
    }

    virtual std::shared_ptr<grpc::ChannelCredentials> channel_credentials() const {
        return grpc::InsecureChannelCredentials();
    }

    virtual ObservabilityOptions adapter_observability_options() const {
        return {};
    }

    virtual bool connect_sdk_client(boost_gateway::sdk::GrpcClient& client) const {
        return client.connect("127.0.0.1", adapter_->port());
    }

    void SetUp() override {
        std::string prepare_error;
        ASSERT_TRUE(prepare_security_materials(prepare_error)) << prepare_error;

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
            },
            adapter_security_options(),
            adapter_observability_options());
        ASSERT_TRUE(adapter_->start());
        ASSERT_GT(adapter_->port(), 0U);

        channel_ = grpc::CreateChannel(
            "127.0.0.1:" + std::to_string(adapter_->port()),
            channel_credentials());
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

class GrpcGatewayRbacE2ETest : public GrpcGatewayAdapterE2ETest {
protected:
    SecurityOptions adapter_security_options() const override {
        SecurityOptions options;
        options.require_authenticated_principal = true;
        options.principal_resolver =
            [](const grpc::ServerContext& context, std::string& out_error)
                -> std::optional<AuthenticatedPrincipal> {
            const auto it = context.client_metadata().find("x-test-principal");
            if (it == context.client_metadata().end()) {
                out_error = "test_principal_missing";
                return std::nullopt;
            }
            const std::string metadata(it->second.data(), it->second.length());
            const auto separator = metadata.find(':');
            if (separator == std::string::npos || separator == 0 ||
                separator + 1 >= metadata.size()) {
                out_error = "test_principal_invalid";
                return std::nullopt;
            }

            AuthenticatedPrincipal principal;
            principal.subject = metadata.substr(separator + 1);
            const auto role = metadata.substr(0, separator);
            if (role == "player") {
                principal.role = v2::auth::Role::kPlayer;
            } else if (role == "observer") {
                principal.role = v2::auth::Role::kObserver;
            } else if (role == "admin") {
                principal.role = v2::auth::Role::kAdmin;
            } else {
                out_error = "test_principal_role_invalid";
                return std::nullopt;
            }
            return principal;
        };
        return options;
    }

    void set_principal(grpc::ClientContext& context, const std::string& principal) const {
        context.AddMetadata("x-test-principal", principal);
    }
};

class GrpcGatewayTlsE2ETest : public GrpcGatewayAdapterE2ETest {
protected:
    bool prepare_security_materials(std::string& out_error) override {
        cert_dir_ = make_temp_dir("grpc-gateway-tls");
        if (!generate_dev_certs(cert_dir_, requires_client_certificate(), out_error)) {
            return false;
        }
        ca_pem_ = read_text_file(cert_dir_ / "ca.crt");
        server_cert_pem_ = read_text_file(cert_dir_ / "server.crt");
        server_key_pem_ = read_text_file(cert_dir_ / "server.key");
        if (ca_pem_.empty() || server_cert_pem_.empty() || server_key_pem_.empty()) {
            out_error = "generated server certificates are empty";
            return false;
        }
        if (requires_client_certificate()) {
            client_cert_pem_ = read_text_file(cert_dir_ / "client.crt");
            client_key_pem_ = read_text_file(cert_dir_ / "client.key");
            if (client_cert_pem_.empty() || client_key_pem_.empty()) {
                out_error = "generated client certificates are empty";
                return false;
            }
        }
        return true;
    }

    SecurityOptions adapter_security_options() const override {
        SecurityOptions options;
        options.tls.certificate_chain_pem = server_cert_pem_;
        options.tls.private_key_pem = server_key_pem_;
        options.tls.root_ca_pem = ca_pem_;
        options.tls.require_client_certificate = requires_client_certificate();
        return options;
    }

    std::shared_ptr<grpc::ChannelCredentials> channel_credentials() const override {
        grpc::SslCredentialsOptions options;
        options.pem_root_certs = ca_pem_;
        if (requires_client_certificate()) {
            options.pem_cert_chain = client_cert_pem_;
            options.pem_private_key = client_key_pem_;
        }
        return grpc::SslCredentials(options);
    }

    bool connect_sdk_client(boost_gateway::sdk::GrpcClient& client) const override {
        boost_gateway::sdk::GrpcClientTlsOptions options;
        options.root_ca_pem = ca_pem_;
        if (requires_client_certificate()) {
            options.client_certificate_chain_pem = client_cert_pem_;
            options.client_private_key_pem = client_key_pem_;
        }
        return client.connect_secure("127.0.0.1", adapter_->port(), options);
    }

    virtual bool requires_client_certificate() const {
        return false;
    }

    std::filesystem::path cert_dir_;
    std::string ca_pem_;
    std::string server_cert_pem_;
    std::string server_key_pem_;
    std::string client_cert_pem_;
    std::string client_key_pem_;
};

class GrpcGatewayMtlsE2ETest : public GrpcGatewayTlsE2ETest {
protected:
    bool requires_client_certificate() const override {
        return true;
    }
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
    ASSERT_TRUE(connect_sdk_client(client));

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

class GrpcGatewayOtlpE2ETest : public GrpcGatewayAdapterE2ETest {
protected:
    void SetUp() override {
        ASSERT_TRUE(collector_.start());
        GrpcGatewayAdapterE2ETest::SetUp();
    }

    void TearDown() override {
        GrpcGatewayAdapterE2ETest::TearDown();
        collector_.stop();
    }

    ObservabilityOptions adapter_observability_options() const override {
        return {
            .otlp_export_endpoint =
                "http://127.0.0.1:" + std::to_string(collector_.port()) + "/v1/traces",
            .service_name = "boost-gateway-grpc-e2e",
            .max_batch_size = 1,
            .export_interval = std::chrono::milliseconds(5),
        };
    }

    FakeOtlpCollector collector_;
};

TEST_F(GrpcGatewayOtlpE2ETest, ExportsGatewayRouteSpansToOtlpCollector) {
    grpc::ClientContext context;
    boost::gateway::v3::LoginRequest request;
    request.set_user_id("otel-alice");
    request.set_token("token:otel-alice");
    request.set_display_name("OTel Alice");
    boost::gateway::v3::LoginResponse response;
    const auto status = stub_->RequestLogin(&context, request, &response);
    ASSERT_TRUE(status.ok());
    EXPECT_EQ(response.error_code(), 0);

    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
    while (std::chrono::steady_clock::now() < deadline) {
        {
            std::lock_guard lock(collector_.mutex);
            if (collector_.request_count > 0) {
                EXPECT_EQ(collector_.last_target, "/v1/traces");
                EXPECT_NE(collector_.last_body.find("\"operationName\":\"route.login_request\""),
                          std::string::npos);
                EXPECT_NE(collector_.last_body.find("\"serviceName\":\"login\""),
                          std::string::npos);
                return;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }

    FAIL() << "timed out waiting for OTLP export";
}

TEST_F(GrpcGatewayRbacE2ETest, ResolvesTrustedPrincipalAndAppliesAllowDeny) {
    {
        grpc::ClientContext context;
        boost::gateway::v3::RoomCreateRequest request;
        request.set_user_id("alice");
        request.set_room_id("rbac-missing-principal");
        boost::gateway::v3::RoomCreateResponse response;
        const auto status = stub_->RequestRoomCreate(&context, request, &response);
        EXPECT_EQ(status.error_code(), grpc::StatusCode::UNAUTHENTICATED);
        EXPECT_EQ(status.error_message(), "test_principal_missing");
    }

    {
        grpc::ClientContext context;
        set_principal(context, "observer:viewer");
        boost::gateway::v3::RoomCreateRequest request;
        request.set_user_id("viewer");
        request.set_room_id("rbac-denied-room");
        boost::gateway::v3::RoomCreateResponse response;
        const auto status = stub_->RequestRoomCreate(&context, request, &response);
        EXPECT_EQ(status.error_code(), grpc::StatusCode::PERMISSION_DENIED);
        EXPECT_EQ(status.error_message(), "grpc_rbac_denied");
    }

    {
        grpc::ClientContext context;
        set_principal(context, "player:alice");
        boost::gateway::v3::RoomCreateRequest request;
        request.set_user_id("alice");
        request.set_room_id("rbac-allowed-room");
        boost::gateway::v3::RoomCreateResponse response;
        const auto status = stub_->RequestRoomCreate(&context, request, &response);
        ASSERT_TRUE(status.ok());
        EXPECT_EQ(response.error_code(), 0);
    }

    {
        grpc::ClientContext submit_context;
        set_principal(submit_context, "player:alice");
        boost::gateway::v3::LeaderboardSubmitRequest submit_request;
        submit_request.set_user_id("alice");
        submit_request.set_display_name("Alice");
        submit_request.set_score(1800);
        boost::gateway::v3::LeaderboardSubmitResponse submit_response;
        ASSERT_TRUE(stub_->RequestLeaderboardSubmit(
            &submit_context, submit_request, &submit_response).ok());
    }

    {
        grpc::ClientContext context;
        set_principal(context, "player:alice");
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

TEST_F(GrpcGatewayRbacE2ETest, RejectsUnauthorizedBattleStreamWithoutLeakingMetrics) {
    grpc::ClientContext context;
    set_principal(context, "observer:viewer");
    boost::gateway::v3::BattleStateRequest request;
    request.set_battle_id("rbac-stream-denied");
    request.set_max_updates(1);
    auto reader = stub_->StreamBattleState(&context, request);

    boost::gateway::v3::BattleStateResponse response;
    EXPECT_FALSE(reader->Read(&response));
    const auto status = reader->Finish();
    EXPECT_EQ(status.error_code(), grpc::StatusCode::PERMISSION_DENIED);
    EXPECT_EQ(status.error_message(), "grpc_rbac_denied");

    std::this_thread::sleep_for(std::chrono::milliseconds(20));
    const auto metrics = adapter_->battle_state_stream_metrics();
    EXPECT_EQ(metrics.started, 0U);
    EXPECT_EQ(metrics.active, 0U);
    EXPECT_EQ(metrics.completed, 0U);
    EXPECT_EQ(metrics.cancelled, 0U);
}

TEST_F(GrpcGatewayTlsE2ETest, SdkClientCompletesRoomAndBattleFlowOverTls) {
    boost_gateway::sdk::GrpcClient client;
    ASSERT_TRUE(connect_sdk_client(client));

    ASSERT_TRUE(client.login("tls-alice", "token:tls-alice", "TLS Alice").ok);
    ASSERT_TRUE(client.create_room("tls-alice", "tls-room").ok);
    ASSERT_TRUE(client.join_room("tls-bob", "tls-room").ok);

    const auto battle = client.create_battle(
        "tls-battle", "tls-room", {"tls-alice", "tls-bob"}, 30);
    ASSERT_TRUE(battle.ok) << battle.error_message;

    const auto stream = client.stream_battle_state("tls-battle", 2);
    ASSERT_TRUE(stream.ok) << stream.error_message;
    ASSERT_EQ(stream.updates.size(), 2U);

    const auto finish = client.finish_battle("tls-alice", "tls-battle", "tls_done");
    ASSERT_TRUE(finish.ok) << finish.error_message;
}

TEST_F(GrpcGatewayMtlsE2ETest, RequiresClientCertificateAndAcceptsMutualTlsClient) {
    boost_gateway::sdk::GrpcClient missing_client_certificate;
    boost_gateway::sdk::GrpcClientTlsOptions missing_tls_options;
    missing_tls_options.root_ca_pem = ca_pem_;
    const bool connected_without_certificate = missing_client_certificate.connect_secure(
        "127.0.0.1", adapter_->port(), missing_tls_options, std::chrono::milliseconds(800));
    if (connected_without_certificate) {
        const auto rejected = missing_client_certificate.login(
            "mtls-missing-cert", "token:mtls-missing-cert", "Missing Cert",
            std::chrono::milliseconds(800));
        EXPECT_FALSE(rejected.ok);
        EXPECT_NE(rejected.error_code, 0);
    } else {
        EXPECT_FALSE(connected_without_certificate);
    }

    boost_gateway::sdk::GrpcClient client;
    ASSERT_TRUE(connect_sdk_client(client));

    const auto login = client.login("mtls-alice", "token:mtls-alice", "mTLS Alice");
    ASSERT_TRUE(login.ok) << login.error_message;
    const auto room = client.create_room("mtls-alice", "mtls-room");
    ASSERT_TRUE(room.ok) << room.error_message;
}

}  // namespace

#endif  // BOOST_BUILD_GRPC
