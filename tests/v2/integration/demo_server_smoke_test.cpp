#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/gateway/demo_server.h"

#include <boost/asio.hpp>

#include <chrono>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

struct V2DemoRuntime {
    asio::io_context io_context;
    std::unique_ptr<v2::gateway::DemoServer> server;
    std::thread io_thread;
    std::string startup_error;

    bool start() {
        try {
            server = std::make_unique<v2::gateway::DemoServer>(io_context, 0);
            server->start();
            io_thread = std::thread([this]() { io_context.run(); });
            return true;
        } catch (const std::exception& ex) {
            startup_error = ex.what();
            return false;
        }
    }

    void stop() {
        if (server) {
            server->stop();
        }
        io_context.stop();
        if (io_thread.joinable()) {
            io_thread.join();
        }
    }
};

class TestClient {
public:
    TestClient() : socket_(io_context_) {}

    void connect(std::uint16_t port) {
        socket_.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));
    }

    void close() {
        boost::system::error_code ignored_ec;
        socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        socket_.close(ignored_ec);
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
        for (;;) {
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

#define SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime) \
    do { \
        if (!(runtime).start()) { \
            GTEST_SKIP() << "socket bind unavailable in this environment: " << (runtime).startup_error; \
        } \
    } while (false)

TEST(V2DemoServerSmokeTest, RealSocketFlowSupportsBootstrapAndDisconnectCleanup) {
    app::logging::init("project_tests");

    V2DemoRuntime runtime;
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime);

    TestClient owner;
    TestClient member;
    owner.connect(runtime.server->local_port());
    member.connect(runtime.server->local_port());

    const auto owner_login =
        owner.exchange(net::protocol::kLoginRequest, 1, "owner|token:owner|Owner");
    EXPECT_EQ(owner_login.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(owner_login.body, "login_ok:owner");

    const auto member_login =
        member.exchange(net::protocol::kLoginRequest, 2, "member|token:member|Member");
    EXPECT_EQ(member_login.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(member_login.body, "login_ok:member");

    const auto room_create =
        owner.exchange(net::protocol::kRoomCreateRequest, 3, "room_alpha");
    EXPECT_EQ(room_create.message_id, net::protocol::kRoomCreateResponse);

    const auto room_join =
        member.exchange(net::protocol::kRoomJoinRequest, 4, "room_alpha");
    EXPECT_EQ(room_join.message_id, net::protocol::kRoomJoinResponse);

    const auto owner_ready =
        owner.exchange(net::protocol::kRoomReadyRequest, 5, "true");
    EXPECT_EQ(owner_ready.message_id, net::protocol::kRoomReadyResponse);

    const auto member_ready =
        member.exchange(net::protocol::kRoomReadyRequest, 6, "true");
    EXPECT_EQ(member_ready.message_id, net::protocol::kRoomReadyResponse);

    owner.send(net::protocol::kBattleStartRequest, 7, "room_alpha");
    const auto start_response = owner.expect_message(net::protocol::kBattleStartResponse);
    EXPECT_EQ(start_response.body, "battle_started:room_alpha:battle_0001");

    const auto owner_started = owner.expect_message(net::protocol::kBattleStatePush);
    const auto member_started = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(owner_started.body, "battle_state:room_alpha:battle_0001");
    EXPECT_EQ(member_started.body, "battle_state:room_alpha:battle_0001");

    owner.send(net::protocol::kBattleInputRequest, 8, "move:1,2");
    const auto input_response = owner.expect_message(net::protocol::kBattleInputResponse);
    const auto input_push = member.expect_message(net::protocol::kBattleInputPush);
    EXPECT_EQ(input_response.body, "input_seq:1");
    EXPECT_EQ(input_push.body, "owner:1:move:1,2");

    owner.close();

    const auto finish_push = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(finish_push.body, "battle_finished:room_alpha:battle_0001:player_disconnected:owner");

    const auto battle_after_finish =
        member.exchange(net::protocol::kBattleInputRequest, 9, "move:9,9");
    EXPECT_EQ(battle_after_finish.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(battle_after_finish.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted));

    member.close();
    runtime.stop();
}
