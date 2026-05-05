#include "app/logging.h"
#include "game/battle/battle_service.h"
#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/login_service.h"
#include "game/login/token_validator.h"
#include "game/room/room_manager.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <array>
#include <chrono>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

struct GatewayTestRuntime {
    asio::io_context io_context;
    boost::asio::thread_pool business_pool{2};
    net::MessageDispatcher dispatcher{business_pool};
    game::gateway::SessionManager session_manager;
    game::room::RoomManager room_manager;
    game::battle::BattleManager battle_manager;
    game::gateway::GatewayMetrics metrics;
    game::login::DevTokenValidator token_validator;
    net::SessionOptions options;
    std::unique_ptr<game::gateway::GatewayServer> server;
    std::unique_ptr<game::gateway::GatewayService> gateway_service;
    std::unique_ptr<game::login::LoginService> login_service;
    std::unique_ptr<game::room::RoomService> room_service;
    std::unique_ptr<game::battle::BattleService> battle_service;
    std::thread io_thread;

    void start() {
        gateway_service = std::make_unique<game::gateway::GatewayService>(session_manager, metrics);
        login_service = std::make_unique<game::login::LoginService>(session_manager, token_validator, metrics);
        room_service =
            std::make_unique<game::room::RoomService>(session_manager, battle_manager, room_manager, metrics);
        battle_service =
            std::make_unique<game::battle::BattleService>(session_manager, room_manager, battle_manager, metrics);

        gateway_service->register_handlers(dispatcher);
        login_service->register_handlers(dispatcher);
        room_service->register_handlers(dispatcher);
        battle_service->register_handlers(dispatcher);

        dispatcher.register_handler(
            net::protocol::kEchoRequest,
            [](const net::DispatchContext& context) {
                context.session->send(net::protocol::kEchoResponse,
                                      context.request_id,
                                      static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                      context.body);
            });

        server = std::make_unique<game::gateway::GatewayServer>(
            io_context,
            dispatcher,
            session_manager,
            room_manager,
            battle_manager,
            metrics,
            0,
            options,
            std::chrono::milliseconds(1000));
        server->start();
        io_thread = std::thread([this]() { io_context.run(); });
    }

    void stop() {
        if (server) {
            server->stop();
        }
        io_context.stop();
        if (io_thread.joinable()) {
            io_thread.join();
        }
        business_pool.join();
    }
};

class TestClient {
public:
    TestClient() : socket_(io_context_) {}

    void connect(std::uint16_t port) {
        socket_.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));
    }

    net::packet::DecodedPacket exchange(std::uint16_t message_id,
                                        std::uint32_t request_id,
                                        const std::string& body) {
        send(message_id, request_id, body);
        return read();
    }

    void send(std::uint16_t message_id, std::uint32_t request_id, const std::string& body) {
        const auto outbound = net::packet::encode(message_id, request_id, 0, body);
        asio::write(socket_, asio::buffer(outbound));
    }

    net::packet::DecodedPacket read() {
        net::packet::LengthHeader header{};
        asio::read(socket_, asio::buffer(header));
        const auto payload_length = net::packet::decode_length(header);

        std::vector<char> payload(payload_length);
        asio::read(socket_, asio::buffer(payload));
        return net::packet::decode_payload(payload);
    }

    net::packet::DecodedPacket expect_message(std::uint16_t message_id) {
        while (true) {
            const auto packet = read();
            if (packet.message_id == message_id) {
                return packet;
            }
        }
    }

private:
    asio::io_context io_context_;
    tcp::socket socket_;
};

}  // namespace

TEST(GatewayIntegrationTest, EchoRequestRoundTrip) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto response = client.exchange(net::protocol::kEchoRequest, 100, "integration_echo");
    EXPECT_EQ(response.message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(response.request_id, 100U);
    EXPECT_EQ(response.error_code, 0);
    EXPECT_EQ(response.body, "integration_echo");

    runtime.stop();
}

TEST(GatewayIntegrationTest, UnauthenticatedBusinessRequestIsBlocked) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto response = client.exchange(net::protocol::kRoomJoinRequest, 101, "room_alpha");
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(response.request_id, 101U);
    EXPECT_EQ(response.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired));
    EXPECT_EQ(response.body, "auth_required");

    runtime.stop();
}

TEST(GatewayIntegrationTest, LoginAndRoomJoinCloseLoopWorks) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto login_response =
        client.exchange(net::protocol::kLoginRequest, 102, "player_01|token:player_01|PlayerOne");
    EXPECT_EQ(login_response.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(login_response.request_id, 102U);
    EXPECT_EQ(login_response.error_code, 0);
    EXPECT_EQ(login_response.body, "login_ok:player_01:PlayerOne");

    const auto create_response = client.exchange(net::protocol::kRoomCreateRequest, 103, "room_alpha");
    EXPECT_EQ(create_response.message_id, net::protocol::kRoomCreateResponse);
    EXPECT_EQ(create_response.request_id, 103U);
    EXPECT_EQ(create_response.error_code, 0);
    EXPECT_EQ(create_response.body, "room_created:room_alpha");

    const auto metrics_snapshot = runtime.metrics.snapshot();
    EXPECT_EQ(metrics_snapshot.login_successes, 1U);
    EXPECT_EQ(metrics_snapshot.room_join_successes, 0U);
    EXPECT_EQ(metrics_snapshot.blocked_packets, 0U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, BattleStartRequiresTwoPlayersInSameRoom) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    TestClient first;
    TestClient second;
    first.connect(runtime.server->local_port());
    second.connect(runtime.server->local_port());

    EXPECT_EQ(first.exchange(net::protocol::kLoginRequest, 104, "player_01|token:player_01").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(second.exchange(net::protocol::kLoginRequest, 105, "player_02|token:player_02").message_id,
              net::protocol::kLoginResponse);

    EXPECT_EQ(first.exchange(net::protocol::kRoomCreateRequest, 106, "room_beta").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(second.exchange(net::protocol::kRoomJoinRequest, 107, "room_beta").message_id,
              net::protocol::kRoomJoinResponse);
    EXPECT_EQ(first.expect_message(net::protocol::kRoomStatePush).message_id, net::protocol::kRoomStatePush);

    EXPECT_EQ(first.exchange(net::protocol::kRoomReadyRequest, 108, "true").message_id,
              net::protocol::kRoomReadyResponse);
    EXPECT_EQ(second.expect_message(net::protocol::kRoomStatePush).message_id, net::protocol::kRoomStatePush);
    EXPECT_EQ(second.exchange(net::protocol::kRoomReadyRequest, 109, "true").message_id,
              net::protocol::kRoomReadyResponse);
    EXPECT_EQ(first.expect_message(net::protocol::kRoomStatePush).message_id, net::protocol::kRoomStatePush);

    const auto battle_response = first.exchange(net::protocol::kBattleStartRequest, 110, "");
    EXPECT_EQ(battle_response.message_id, net::protocol::kBattleStartResponse);
    EXPECT_EQ(battle_response.request_id, 110U);
    EXPECT_EQ(battle_response.error_code, 0);
    EXPECT_EQ(battle_response.body, "battle_started:room_beta:2");
    EXPECT_EQ(second.expect_message(net::protocol::kBattleStatePush).message_id,
              net::protocol::kBattleStatePush);

    const auto input_response = first.exchange(net::protocol::kBattleInputRequest, 111, "move:left");
    EXPECT_EQ(input_response.message_id, net::protocol::kBattleInputResponse);
    EXPECT_EQ(input_response.request_id, 111U);
    EXPECT_EQ(input_response.error_code, 0);
    EXPECT_EQ(second.expect_message(net::protocol::kBattleInputPush).message_id,
              net::protocol::kBattleInputPush);

    const auto metrics_snapshot = runtime.metrics.snapshot();
    EXPECT_EQ(metrics_snapshot.battle_start_successes, 1U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, InvalidTokenIsRejected) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto response = client.exchange(net::protocol::kLoginRequest, 112, "player_01|wrong_token");
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(response.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken));
    EXPECT_EQ(response.body, "invalid_token");

    runtime.stop();
}

TEST(GatewayIntegrationTest, IdleSessionTimesOutAndDisconnects) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.options.heartbeat_check_interval = std::chrono::milliseconds(100);
    runtime.options.heartbeat_timeout = std::chrono::milliseconds(300);
    runtime.start();

    asio::io_context io_context;
    tcp::socket socket(io_context);
    socket.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), runtime.server->local_port()));

    std::this_thread::sleep_for(std::chrono::milliseconds(600));

    std::array<char, 1> buffer{};
    boost::system::error_code ec;
    socket.read_some(asio::buffer(buffer), ec);

    EXPECT_TRUE(ec == asio::error::eof || ec == asio::error::connection_reset);

    runtime.stop();
}

TEST(GatewayIntegrationTest, RateLimitMiddlewareBlocksBurstTraffic) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    TestClient client;
    client.connect(runtime.server->local_port());

    net::packet::DecodedPacket response;
    for (std::uint32_t request_id = 200; request_id < 240; ++request_id) {
        response = client.exchange(net::protocol::kEchoRequest, request_id, "burst_echo");
        if (response.message_id == net::protocol::kErrorResponse) {
            break;
        }
    }

    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(response.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited));
    EXPECT_EQ(response.body, "rate_limited");
    EXPECT_GT(runtime.metrics.snapshot().blocked_packets, 0U);

    runtime.stop();
}
