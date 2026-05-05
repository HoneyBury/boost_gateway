#include "app/logging.h"
#include "game/battle/battle_service.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/login/login_service.h"
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
    net::SessionOptions options;
    std::unique_ptr<game::gateway::GatewayServer> server;
    std::thread io_thread;

    void start() {
        game::gateway::GatewayService gateway_service;
        game::login::LoginService login_service;
        game::room::RoomService room_service;
        game::battle::BattleService battle_service;

        gateway_service.register_handlers(dispatcher);
        login_service.register_handlers(dispatcher);
        room_service.register_handlers(dispatcher);
        battle_service.register_handlers(dispatcher);

        dispatcher.register_handler(
            net::protocol::kEchoRequest,
            [](const std::shared_ptr<net::Session>& session, std::string body) {
                session->send(net::protocol::kEchoResponse, std::move(body));
            });

        server = std::make_unique<game::gateway::GatewayServer>(io_context, dispatcher, 0, options);
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

net::packet::DecodedPacket exchange_packet(std::uint16_t port,
                                           std::uint16_t message_id,
                                           const std::string& body) {
    asio::io_context io_context;
    tcp::socket socket(io_context);
    socket.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));

    const auto outbound = net::packet::encode(message_id, body);
    asio::write(socket, asio::buffer(outbound));

    net::packet::LengthHeader header{};
    asio::read(socket, asio::buffer(header));
    const auto payload_length = net::packet::decode_length(header);

    std::vector<char> payload(payload_length);
    asio::read(socket, asio::buffer(payload));

    return net::packet::decode_payload(payload);
}

}  // namespace

TEST(GatewayIntegrationTest, EchoRequestRoundTrip) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    const auto response =
        exchange_packet(runtime.server->local_port(), net::protocol::kEchoRequest, "integration_echo");

    EXPECT_EQ(response.message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(response.body, "integration_echo");

    runtime.stop();
}

TEST(GatewayIntegrationTest, HeartbeatRequestGetsResponse) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    const auto response =
        exchange_packet(runtime.server->local_port(), net::protocol::kHeartbeatRequest, "");

    EXPECT_EQ(response.message_id, net::protocol::kHeartbeatResponse);
    EXPECT_EQ(response.body, "pong");

    runtime.stop();
}

TEST(GatewayIntegrationTest, LoginRequestGetsBusinessResponse) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.start();

    const auto response =
        exchange_packet(runtime.server->local_port(), net::protocol::kLoginRequest, "player_01");

    EXPECT_EQ(response.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(response.body, "login_ok:player_01");

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
