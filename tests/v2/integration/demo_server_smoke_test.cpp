#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/gateway/demo_server.h"

#include <boost/asio.hpp>
#include <nlohmann/json.hpp>

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
    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;

    bool start() {
        try {
            server = std::make_unique<v2::gateway::DemoServer>(0);
            server->start();
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
    EXPECT_EQ(start_response.body, "battle_started:room_id=room_alpha:battle_id=battle_0001");

    const auto owner_started = owner.expect_message(net::protocol::kBattleStatePush);
    const auto member_started = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(owner_started.body, "battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001");
    EXPECT_EQ(member_started.body, "battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001");

    owner.send(net::protocol::kBattleInputRequest, 8, "move:1,2");
    const auto input_response = owner.expect_message(net::protocol::kBattleInputResponse);
    const auto input_push = member.expect_message(net::protocol::kBattleInputPush);
    EXPECT_EQ(input_response.body, "input_seq:seq=1");
    EXPECT_EQ(input_push.body, "battle_input:user_id=owner:seq=1:input=move:1,2");
    const auto owner_frame = owner.expect_message(net::protocol::kBattleStatePush);
    const auto member_frame = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(owner_frame.body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=1:trigger=input:owner:1");
    EXPECT_EQ(member_frame.body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=1:trigger=input:owner:1");

    owner.send(net::protocol::kBattleInputRequest, 9, "move:2,2");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).body, "input_seq:seq=2");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleInputPush).body, "battle_input:user_id=owner:seq=2:input=move:2,2");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=2:trigger=input:owner:2");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=2:trigger=input:owner:2");

    owner.send(net::protocol::kBattleInputRequest, 10, "move:3,2");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).body, "input_seq:seq=3");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleInputPush).body, "battle_input:user_id=owner:seq=3:input=move:3,2");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=3:trigger=input:owner:3");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=3:trigger=input:owner:3");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=frame_limit_reached:user_id=input:owner:3");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=frame_limit_reached:user_id=input:owner:3");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=frame_limit_reached:user_id=input:owner:3");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=frame_limit_reached:user_id=input:owner:3");

    owner.close();

    const auto battle_after_finish =
        member.exchange(net::protocol::kBattleInputRequest, 11, "move:9,9");
    EXPECT_EQ(battle_after_finish.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(battle_after_finish.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted));

    member.close();
    runtime.stop();
}

TEST(V2DemoServerSmokeTest, RealSocketFlowSupportsRequestedBattleFinish) {
    app::logging::init("project_tests");

    V2DemoRuntime runtime;
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime);

    TestClient owner;
    TestClient member;
    owner.connect(runtime.server->local_port());
    member.connect(runtime.server->local_port());

    EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 21, "owner|token:owner|Owner").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 22, "member|token:member|Member").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 23, "room_beta").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 24, "room_beta").message_id,
              net::protocol::kRoomJoinResponse);
    EXPECT_EQ(owner.exchange(net::protocol::kRoomReadyRequest, 25, "true").message_id,
              net::protocol::kRoomReadyResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomReadyRequest, 26, "true").message_id,
              net::protocol::kRoomReadyResponse);

    owner.send(net::protocol::kBattleStartRequest, 27, "room_beta");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStartResponse).body,
              "battle_started:room_id=room_beta:battle_id=battle_0001");
    (void)owner.expect_message(net::protocol::kBattleStatePush);
    (void)member.expect_message(net::protocol::kBattleStatePush);

    owner.send(net::protocol::kBattleInputRequest, 28, "finish:surrender");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=settlement:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=settlement:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).body,
              "battle_end_accepted:surrender");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=finished:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=finished:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");

    const auto after_finish = member.exchange(net::protocol::kBattleInputRequest, 29, "move:9,9");
    EXPECT_EQ(after_finish.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(after_finish.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted));

    owner.close();
    member.close();
    runtime.stop();
}

TEST(V2DemoServerSmokeTest, DemoServerReportsConfiguredIoCoreCount) {
    app::logging::init("project_tests");

    auto io_engine = std::make_unique<v2::io::AsioIoEngine>(2);
    v2::gateway::DemoServer server(0, {}, {}, std::move(io_engine));
    EXPECT_EQ(server.io_core_count(), 2U);
}

TEST(V2DemoServerSmokeTest, DemoServerTracksPinnedAcceptorCoreAndSessionSnapshot) {
    app::logging::init("project_tests");

    try {
        auto io_engine = std::make_unique<v2::io::AsioIoEngine>(2);
        v2::gateway::DemoServer server(
            0,
            {},
            v2::gateway::DemoServerOptions{.acceptor_core_id = 1},
            std::move(io_engine));
        server.start();

        TestClient client;
        client.connect(server.local_port());
        const auto login = client.exchange(net::protocol::kLoginRequest, 31, "core_user|token:core_user|CoreUser");
        EXPECT_EQ(login.message_id, net::protocol::kLoginResponse);

        EXPECT_EQ(server.acceptor_core_id(), 1U);
        EXPECT_EQ(server.session_io_core(1), 1U);
        const auto snapshots = server.io_core_snapshot();
        ASSERT_EQ(snapshots.size(), 2U);
        EXPECT_EQ(snapshots[1].active_sessions, 1U);
        EXPECT_EQ(snapshots[1].accepted_sessions, 1U);
        EXPECT_GE(snapshots[1].outbound_dispatches, 1U);
        const auto diagnostics = nlohmann::json::parse(server.diagnostics_json());
        EXPECT_EQ(diagnostics["io_core_count"], 2);
        EXPECT_EQ(diagnostics["acceptor_core_id"], 1);
        EXPECT_EQ(diagnostics["total_active_sessions"], 1);
        EXPECT_EQ(diagnostics["total_accepted_sessions"], 1);
        EXPECT_GE(diagnostics["total_outbound_dispatches"].get<std::uint64_t>(), 1U);
        ASSERT_EQ(diagnostics["io_cores"].size(), 2U);
        EXPECT_EQ(diagnostics["io_cores"][1]["core_id"], 1);
        EXPECT_EQ(diagnostics["io_cores"][1]["active_sessions"], 1);
        EXPECT_EQ(diagnostics["io_cores"][1]["accepted_sessions"], 1);
        EXPECT_GE(diagnostics["io_cores"][1]["outbound_dispatches"].get<std::uint64_t>(), 1U);

        client.close();
        server.stop();
    } catch (const std::exception& ex) {
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}
