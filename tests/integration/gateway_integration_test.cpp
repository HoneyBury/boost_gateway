#include "app/logging.h"
#include "game/battle/battle_service.h"
#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/push_service.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/login_service.h"
#include "game/login/token_validator.h"
#include "game/room/room_manager.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/packet_codec.h"
#include "net/packet_compressor.h"
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
    game::gateway::PushService push_service;
    game::login::DevTokenValidator token_validator;
    net::SessionOptions options;
    std::unique_ptr<game::gateway::GatewayServer> server;
    std::unique_ptr<game::gateway::GatewayService> gateway_service;
    std::unique_ptr<game::login::LoginService> login_service;
    std::unique_ptr<game::room::RoomService> room_service;
    std::unique_ptr<game::battle::BattleService> battle_service;
    std::thread io_thread;
    std::string startup_error;

    bool start() {
        try {
            room_manager.set_battle_active_query([this](const std::string& room_id) {
                return battle_manager.battle_started(room_id);
            });

            gateway_service = std::make_unique<game::gateway::GatewayService>(session_manager, metrics);
            login_service =
                std::make_unique<game::login::LoginService>(session_manager, push_service, room_manager, token_validator, metrics);
            room_service =
                std::make_unique<game::room::RoomService>(session_manager, push_service, battle_manager, room_manager, metrics);
            battle_service =
                std::make_unique<game::battle::BattleService>(session_manager, push_service, room_manager, battle_manager, metrics);

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
                0,
                options,
                std::chrono::milliseconds(1000));
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

    // 仅供拒绝路径 / 故意构造畸形包的测试使用，正常路径走 send/exchange/read。
    tcp::socket& socket_for_test() { return socket_; }

private:
    asio::io_context io_context_;
    tcp::socket socket_;
};

// RoomService 通过 PushService 下发 kRoomStatePush，业务 handler 在 thread_pool 上完成；
// 响应包与推送的到达顺序在个别步骤上可能交错，集成测试应跳过中间的推送再断言响应。
net::packet::DecodedPacket read_until_message(TestClient& client, std::uint16_t message_id) {
    for (;;) {
        const auto packet = client.read();
        if (packet.message_id == message_id) {
            return packet;
        }
        EXPECT_EQ(packet.message_id, net::protocol::kRoomStatePush)
            << "unexpected message_id=" << packet.message_id;
    }
}

}  // namespace

#define SKIP_IF_RUNTIME_UNAVAILABLE(runtime) \
    do { \
        if (!(runtime).start()) { \
            GTEST_SKIP() << "socket bind unavailable in this environment: " << (runtime).startup_error; \
        } \
    } while (false)

TEST(GatewayIntegrationTest, EchoRequestRoundTrip) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto response = client.exchange(net::protocol::kEchoRequest, 100, "integration_echo");
    EXPECT_EQ(response.message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(response.request_id, 100U);
    EXPECT_EQ(response.error_code, 0);
    EXPECT_EQ(response.body, "integration_echo");

    runtime.stop();
}

TEST(GatewayIntegrationTest, DefaultRuntimeDoesNotRegisterAdminHandlers) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    EXPECT_FALSE(runtime.dispatcher.has_handler(net::protocol::kAdminKickPlayer));
    EXPECT_FALSE(runtime.dispatcher.has_handler(net::protocol::kAdminBanIp));
    EXPECT_FALSE(runtime.dispatcher.has_handler(net::protocol::kAdminServerStatus));
    EXPECT_FALSE(runtime.dispatcher.has_handler(net::protocol::kAdminReloadConfig));

    runtime.stop();
}

TEST(GatewayIntegrationTest, UnauthenticatedBusinessRequestIsBlocked) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

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
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

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
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

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
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto response = client.exchange(net::protocol::kLoginRequest, 112, "player_01|wrong_token");
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(response.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken));
    EXPECT_EQ(response.body, "invalid_token");

    runtime.stop();
}

TEST(GatewayIntegrationTest, DuplicateLoginKicksOldSessionAndResumesRoomState) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient first;
    TestClient second;
    first.connect(runtime.server->local_port());
    second.connect(runtime.server->local_port());

    const auto first_login =
        first.exchange(net::protocol::kLoginRequest, 113, "player_resume|token:player_resume|ResumePlayer");
    EXPECT_EQ(first_login.message_id, net::protocol::kLoginResponse);

    const auto create_room = first.exchange(net::protocol::kRoomCreateRequest, 114, "room_resume");
    EXPECT_EQ(create_room.message_id, net::protocol::kRoomCreateResponse);

    const auto second_login =
        second.exchange(net::protocol::kLoginRequest, 115, "player_resume|token:player_resume|ResumePlayer");
    EXPECT_EQ(second_login.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(second_login.body, "login_ok:player_resume:ResumePlayer:room=room_resume");

    const auto kicked_push = first.expect_message(net::protocol::kSessionKickedPush);
    EXPECT_EQ(kicked_push.body, "session_kicked:duplicate_login:room_transferred");

    const auto resumed_push = second.expect_message(net::protocol::kSessionResumedPush);
    EXPECT_EQ(resumed_push.body, "session_resumed:room_resume:battle=0");

    const auto leave_room = second.exchange(net::protocol::kRoomLeaveRequest, 116, "");
    EXPECT_EQ(leave_room.message_id, net::protocol::kRoomLeaveResponse);
    EXPECT_EQ(leave_room.body, "room_left:room_resume");

    runtime.stop();
}

TEST(GatewayIntegrationTest, IdleSessionTimesOutAndDisconnects) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    runtime.options.heartbeat_check_interval = std::chrono::milliseconds(100);
    runtime.options.heartbeat_timeout = std::chrono::milliseconds(300);
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

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

TEST(GatewayIntegrationTest, ServerStopClosesAuthenticatedSessionsAndCleansRooms) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto login_response =
        client.exchange(net::protocol::kLoginRequest, 150, "cleanup_user|token:cleanup_user|CleanupUser");
    EXPECT_EQ(login_response.message_id, net::protocol::kLoginResponse);

    const auto create_response = client.exchange(net::protocol::kRoomCreateRequest, 151, "room_cleanup");
    EXPECT_EQ(create_response.message_id, net::protocol::kRoomCreateResponse);

    for (int i = 0; i < 50; ++i) {
        if (runtime.server->active_connections() == 1 &&
            runtime.session_manager.snapshot().active_sessions == 1 &&
            runtime.room_manager.room_count() == 1) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    EXPECT_EQ(runtime.server->active_connections(), 1U);
    EXPECT_EQ(runtime.session_manager.snapshot().active_sessions, 1U);
    EXPECT_EQ(runtime.room_manager.room_count(), 1U);

    runtime.server->stop();

    for (int i = 0; i < 50; ++i) {
        if (runtime.server->active_connections() == 0 &&
            runtime.session_manager.snapshot().active_sessions == 0 &&
            runtime.room_manager.room_count() == 0) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    EXPECT_EQ(runtime.server->active_connections(), 0U);
    EXPECT_EQ(runtime.session_manager.snapshot().active_sessions, 0U);
    EXPECT_EQ(runtime.room_manager.room_count(), 0U);

    runtime.stop();
}

// v1.1.2 / T04 集成回归: 当客户端在一个 *没有压缩后端* 的 build 上发送
// 带 packet::flags::kCompressed 的包时，服务端必须直接拒绝这个连接，
// 而不是用 fallback 的 "长度前缀透传 decompress_body" 静默把语义弄错。
//
// 见 docs/development-optimization.md §6.A / §8.3：标志位的语义跨 build 必须自洽。
// 本用例在 HAS_ZLIB build 下默认跳过——因为那时 kCompressed 是合法的，
// 服务端的拒绝路径不会触发。
TEST(GatewayIntegrationTest, CompressedFlagWithoutBackendIsRejected) {
    if (net::packet::is_compression_available()) {
        GTEST_SKIP() << "Compression backend is linked; rejection path is intentionally not exercised.";
    }

    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient client;
    client.connect(runtime.server->local_port());

    // 故意把一个普通字符串体伪装成 "已压缩" 的包发出去，body 不带 4 字节长度前缀，
    // 模拟跨 build 误传 kCompressed 的破坏路径。
    const auto encoded =
        net::packet::encode(net::protocol::kEchoRequest, 1, 0, "not-actually-compressed",
                            net::packet::flags::kCompressed);
    asio::write(client.socket_for_test(), asio::buffer(encoded));

    // 服务端应当走 invalid_argument 路径关闭连接：客户端读时会拿到 EOF / reset。
    std::array<char, 1> buffer{};
    boost::system::error_code ec;
    client.socket_for_test().read_some(asio::buffer(buffer), ec);

    EXPECT_TRUE(ec == asio::error::eof || ec == asio::error::connection_reset)
        << "v1.1.2 T04: 服务端必须拒绝带 kCompressed 但本端无压缩后端的包，"
           "而不是使用 fallback 透传 decompress_body 让上层拿到错乱语义。 "
           "实际错误码: " << ec.message();

    runtime.stop();
}

TEST(GatewayIntegrationTest, RateLimitMiddlewareBlocksBurstTraffic) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

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

// --- T17 / v1.2.1：login / room / battle 业务边界（集成回归）---

TEST(GatewayIntegrationTest, BattleStartRejectedForNonOwner) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient owner;
    TestClient member;
    owner.connect(runtime.server->local_port());
    member.connect(runtime.server->local_port());

    ASSERT_EQ(owner.exchange(net::protocol::kLoginRequest, 1201, "p_own|token:p_own|Own").message_id,
              net::protocol::kLoginResponse);
    ASSERT_EQ(member.exchange(net::protocol::kLoginRequest, 1202, "p_mem|token:p_mem|Mem").message_id,
              net::protocol::kLoginResponse);

    ASSERT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 1203, "room_owner_gate").message_id,
              net::protocol::kRoomCreateResponse);
    ASSERT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 1204, "room_owner_gate").message_id,
              net::protocol::kRoomJoinResponse);

    owner.send(net::protocol::kRoomReadyRequest, 1205, "true");
    EXPECT_EQ(read_until_message(owner, net::protocol::kRoomReadyResponse).request_id, 1205U);
    member.send(net::protocol::kRoomReadyRequest, 1206, "true");
    EXPECT_EQ(read_until_message(member, net::protocol::kRoomReadyResponse).request_id, 1206U);

    member.send(net::protocol::kBattleStartRequest, 1207, "");
    const auto battle = read_until_message(member, net::protocol::kErrorResponse);
    EXPECT_EQ(battle.request_id, 1207U);
    EXPECT_EQ(battle.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kNotRoomOwner));
    EXPECT_EQ(battle.body, "not_room_owner");

    runtime.stop();
}

TEST(GatewayIntegrationTest, BattleStartRejectedWhenNotAllReady) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient owner;
    TestClient member;
    owner.connect(runtime.server->local_port());
    member.connect(runtime.server->local_port());

    ASSERT_EQ(owner.exchange(net::protocol::kLoginRequest, 1211, "r_a|token:r_a|Ra").message_id,
              net::protocol::kLoginResponse);
    ASSERT_EQ(member.exchange(net::protocol::kLoginRequest, 1212, "r_b|token:r_b|Rb").message_id,
              net::protocol::kLoginResponse);

    ASSERT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 1213, "room_ready_gate").message_id,
              net::protocol::kRoomCreateResponse);
    ASSERT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 1214, "room_ready_gate").message_id,
              net::protocol::kRoomJoinResponse);

    owner.send(net::protocol::kRoomReadyRequest, 1215, "true");
    EXPECT_EQ(read_until_message(owner, net::protocol::kRoomReadyResponse).request_id, 1215U);
    // member 保持未 ready — owner 开战应失败

    owner.send(net::protocol::kBattleStartRequest, 1216, "");
    const auto battle = read_until_message(owner, net::protocol::kErrorResponse);
    EXPECT_EQ(battle.request_id, 1216U);
    EXPECT_EQ(battle.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kNotAllReady));
    EXPECT_EQ(battle.body, "not_all_ready");

    runtime.stop();
}

TEST(GatewayIntegrationTest, BattleStartRejectedWhenOnlyOnePlayer) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient solo;
    solo.connect(runtime.server->local_port());

    ASSERT_EQ(solo.exchange(net::protocol::kLoginRequest, 1221, "solo|token:solo").message_id,
              net::protocol::kLoginResponse);
    ASSERT_EQ(solo.exchange(net::protocol::kRoomCreateRequest, 1222, "room_solo").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(solo.exchange(net::protocol::kRoomReadyRequest, 1223, "true").message_id,
              net::protocol::kRoomReadyResponse);

    const auto battle = solo.exchange(net::protocol::kBattleStartRequest, 1224, "");
    EXPECT_EQ(battle.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(battle.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kNotEnoughPlayers));
    EXPECT_EQ(battle.body, "not_enough_players");

    runtime.stop();
}

TEST(GatewayIntegrationTest, BattleInputRejectedWhenBattleNotStarted) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient a;
    TestClient b;
    a.connect(runtime.server->local_port());
    b.connect(runtime.server->local_port());

    ASSERT_EQ(a.exchange(net::protocol::kLoginRequest, 1231, "ia|token:ia|Ia").message_id,
              net::protocol::kLoginResponse);
    ASSERT_EQ(b.exchange(net::protocol::kLoginRequest, 1232, "ib|token:ib|Ib").message_id,
              net::protocol::kLoginResponse);

    ASSERT_EQ(a.exchange(net::protocol::kRoomCreateRequest, 1233, "room_no_battle").message_id,
              net::protocol::kRoomCreateResponse);
    ASSERT_EQ(b.exchange(net::protocol::kRoomJoinRequest, 1234, "room_no_battle").message_id,
              net::protocol::kRoomJoinResponse);

    a.send(net::protocol::kRoomReadyRequest, 1235, "true");
    EXPECT_EQ(read_until_message(a, net::protocol::kRoomReadyResponse).request_id, 1235U);
    b.send(net::protocol::kRoomReadyRequest, 1236, "true");
    EXPECT_EQ(read_until_message(b, net::protocol::kRoomReadyResponse).request_id, 1236U);

    a.send(net::protocol::kBattleInputRequest, 1237, "early_move");
    const auto input = read_until_message(a, net::protocol::kErrorResponse);
    EXPECT_EQ(input.request_id, 1237U);
    EXPECT_EQ(input.error_code, static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted));
    EXPECT_EQ(input.body, "battle_not_started");

    runtime.stop();
}
