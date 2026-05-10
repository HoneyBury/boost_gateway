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
#include "v2/gateway/gateway_server_bridge.h"
#include "v2/io/io_engine.h"

#include <boost/asio.hpp>
#include <boost/process/v1.hpp>
#include <boost/asio/thread_pool.hpp>

#include <array>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
namespace bp = boost::process::v1;
using tcp = asio::ip::tcp;

struct GatewayTestRuntime {
    struct PushDispatchObservation {
        std::mutex mutex;
        std::vector<std::optional<std::uint32_t>> observed_cores;
    };

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
    std::shared_ptr<game::gateway::GatewayPacketBridge> packet_bridge;
    std::unique_ptr<game::gateway::GatewayServer> server;
    std::unique_ptr<game::gateway::GatewayService> gateway_service;
    std::unique_ptr<game::login::LoginService> login_service;
    std::unique_ptr<game::room::RoomService> room_service;
    std::unique_ptr<game::battle::BattleService> battle_service;
    std::thread io_thread;
    std::string startup_error;
    std::shared_ptr<PushDispatchObservation> push_dispatch_observation =
        std::make_shared<PushDispatchObservation>();
    std::shared_ptr<std::atomic<bool>> push_scheduler_active =
        std::make_shared<std::atomic<bool>>(true);

    bool start() {
        try {
            room_manager.set_battle_active_query([this](const std::string& room_id) {
                return battle_manager.battle_started(room_id);
            });

            gateway_service = std::make_unique<game::gateway::GatewayService>(session_manager, metrics, push_service);
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
                [this](const net::DispatchContext& context) {
                    push_service.send_ok(context.session,
                                         net::protocol::kEchoResponse,
                                         context.request_id,
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
                std::chrono::milliseconds(1000),
                game::gateway::GatewayMetricsExportOptions{},
                std::make_unique<v2::io::AsioIoEngine>(2));
            auto* server_ptr = server.get();
            auto push_observation = push_dispatch_observation;
            auto push_scheduler_active_flag = push_scheduler_active;
            push_scheduler_active_flag->store(true, std::memory_order_relaxed);
            push_service.set_write_scheduler(
                [server_ptr, push_observation, push_scheduler_active_flag](
                    const game::gateway::PushService::SessionPtr& session,
                    game::gateway::PushService::SessionWriteTask task) {
                    if (!push_scheduler_active_flag->load(std::memory_order_relaxed)) {
                        return false;
                    }
                    return server_ptr->dispatch_to_session_core(
                        session,
                        [server_ptr, push_observation, push_scheduler_active_flag, task = std::move(task)]() mutable {
                            if (push_scheduler_active_flag->load(std::memory_order_relaxed)) {
                                std::scoped_lock lock(push_observation->mutex);
                                push_observation->observed_cores.push_back(server_ptr->current_io_core());
                            }
                            task();
                        });
                });
            if (packet_bridge) {
                if (const auto shadow_bridge =
                        std::dynamic_pointer_cast<v2::gateway::GatewayServerShadowBridge>(packet_bridge)) {
                    shadow_bridge->set_write_scheduler(
                        [server_ptr](const std::shared_ptr<net::Session>& session,
                                     v2::gateway::GatewayServerShadowBridge::SessionWriteTask task) {
                            return server_ptr->dispatch_to_session_core(session, std::move(task));
                        });
                }
                server->set_packet_bridge(packet_bridge);
            }
            server->start();
            io_thread = std::thread([this]() { io_context.run(); });
            return true;
        } catch (const std::exception& ex) {
            startup_error = ex.what();
            return false;
        }
    }

    void stop() {
        push_scheduler_active->store(false, std::memory_order_relaxed);
        push_service.set_write_scheduler({});
        if (const auto shadow_bridge =
                std::dynamic_pointer_cast<v2::gateway::GatewayServerShadowBridge>(packet_bridge)) {
            shadow_bridge->set_write_scheduler({});
        }
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

    std::optional<net::packet::DecodedPacket> try_read_for(std::chrono::milliseconds timeout) {
        const auto deadline = std::chrono::steady_clock::now() + timeout;
        boost::system::error_code ec;
        while (std::chrono::steady_clock::now() < deadline) {
            if (socket_.available(ec) >= sizeof(net::packet::LengthHeader) && !ec) {
                return read();
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        return std::nullopt;
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

std::vector<net::packet::DecodedPacket> collect_matching_messages(TestClient& client,
                                                                  std::uint16_t message_id,
                                                                  std::chrono::milliseconds timeout) {
    std::vector<net::packet::DecodedPacket> packets;
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        const auto packet = client.try_read_for(std::chrono::milliseconds(25));
        if (!packet.has_value()) {
            continue;
        }
        if (packet->message_id == message_id) {
            packets.push_back(std::move(*packet));
        }
    }
    return packets;
}

bool contains_body(const std::vector<net::packet::DecodedPacket>& packets, std::string_view body) {
    for (const auto& packet : packets) {
        if (packet.body == body) {
            return true;
        }
    }
    return false;
}

class RecordingPacketBridge final : public game::gateway::GatewayPacketBridge {
public:
    void on_packet(const std::shared_ptr<net::Session>& session,
                   const net::Session::PacketMessage& message) override {
        (void)session;
        packets.push_back(message);
    }

    void on_close(const std::shared_ptr<net::Session>& session) override {
        (void)session;
        close_count++;
    }

    std::vector<net::Session::PacketMessage> packets;
    std::size_t close_count = 0;
};

std::optional<std::uint16_t> reserve_free_port() {
    try {
        asio::io_context io_context;
        tcp::acceptor acceptor(io_context, tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));
        const auto port = acceptor.local_endpoint().port();
        acceptor.close();
        return port;
    } catch (const std::exception&) {
        return std::nullopt;
    }
}

bool wait_for_tcp_server(std::uint16_t port, std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        try {
            asio::io_context io_context;
            tcp::socket socket(io_context);
            socket.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));
            boost::system::error_code ignored_ec;
            socket.shutdown(tcp::socket::shutdown_both, ignored_ec);
            socket.close(ignored_ec);
            return true;
        } catch (const std::exception&) {
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
    }
    return false;
}

bool wait_until(std::chrono::milliseconds timeout, const std::function<bool()>& predicate) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) {
            return true;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return predicate();
}

class EchoServerProcess {
public:
    explicit EchoServerProcess(const std::filesystem::path& config_path)
        : config_path_(config_path) {}

    ~EchoServerProcess() { stop(); }

    bool start() {
        try {
            child_ = std::make_unique<bp::child>(
                PROJECT_ECHO_SERVER_PATH,
                config_path_.string(),
                bp::std_out > bp::null,
                bp::std_err > bp::null);
            return true;
        } catch (const std::exception& ex) {
            startup_error_ = ex.what();
            return false;
        }
    }

    void stop() {
        if (!child_) {
            return;
        }
        if (child_->running()) {
            child_->terminate();
            child_->wait();
        }
        child_.reset();
    }

    [[nodiscard]] const std::string& startup_error() const noexcept { return startup_error_; }

private:
    std::filesystem::path config_path_;
    std::unique_ptr<bp::child> child_;
    std::string startup_error_;
};

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

TEST(GatewayIntegrationTest, OptionalPacketBridgeMirrorsTrafficWithoutChangingV1Responses) {
    app::logging::init("project_tests");

    auto bridge = std::make_shared<RecordingPacketBridge>();
    GatewayTestRuntime runtime;
    runtime.packet_bridge = bridge;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    {
        TestClient client;
        client.connect(runtime.server->local_port());

        const auto response = client.exchange(net::protocol::kEchoRequest, 170, "bridge_echo");
        EXPECT_EQ(response.message_id, net::protocol::kEchoResponse);
        EXPECT_EQ(response.body, "bridge_echo");
    }

    for (int i = 0; i < 50; ++i) {
        if (bridge->close_count == 1) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    ASSERT_EQ(bridge->packets.size(), 1U);
    EXPECT_EQ(bridge->packets.front().message_id, net::protocol::kEchoRequest);
    EXPECT_EQ(bridge->packets.front().request_id, 170U);
    EXPECT_EQ(bridge->close_count, 1U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, OptionalPacketBridgeMirrorsExternallyAttachedSessionTraffic) {
    app::logging::init("project_tests");

    auto bridge = std::make_shared<RecordingPacketBridge>();
    GatewayTestRuntime runtime;
    runtime.packet_bridge = bridge;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    tcp::acceptor acceptor(runtime.io_context, tcp::endpoint(asio::ip::make_address("127.0.0.1"), 0));

    {
        TestClient client;
        client.connect(acceptor.local_endpoint().port());

        tcp::socket server_socket(runtime.io_context);
        acceptor.accept(server_socket);

        auto session = std::make_shared<net::Session>(std::move(server_socket), runtime.options);
        ASSERT_TRUE(runtime.server->attach_session(session));

        const auto response = client.exchange(net::protocol::kEchoRequest, 175, "bridge_external_echo");
        EXPECT_EQ(response.message_id, net::protocol::kEchoResponse);
        EXPECT_EQ(response.body, "bridge_external_echo");
    }

    for (int i = 0; i < 50; ++i) {
        if (bridge->close_count == 1) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    ASSERT_EQ(bridge->packets.size(), 1U);
    EXPECT_EQ(bridge->packets.front().message_id, net::protocol::kEchoRequest);
    EXPECT_EQ(bridge->packets.front().request_id, 175U);
    EXPECT_EQ(bridge->close_count, 1U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, GatewayServerTracksSessionIoCoreAndDispatchesBackToOwningCore) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);
    ASSERT_EQ(runtime.server->io_core_count(), 2U);

    TestClient client;
    client.connect(runtime.server->local_port());

    ASSERT_TRUE(wait_until(std::chrono::milliseconds(300), [&]() {
        return runtime.session_manager.snapshot().active_sessions == 1;
    }));

    const auto sessions = runtime.session_manager.all_sessions();
    ASSERT_EQ(sessions.size(), 1U);

    const auto session_core = runtime.server->session_io_core(sessions.front());
    ASSERT_TRUE(session_core.has_value());
    EXPECT_LT(*session_core, runtime.server->io_core_count());

    std::promise<std::optional<std::uint32_t>> dispatched_core_promise;
    auto dispatched_core = dispatched_core_promise.get_future();
    EXPECT_TRUE(runtime.server->dispatch_to_session_core(
        sessions.front(),
        [&runtime, &dispatched_core_promise]() mutable {
            dispatched_core_promise.set_value(runtime.server->current_io_core());
        }));

    EXPECT_EQ(dispatched_core.get(), session_core);

    runtime.stop();
}

TEST(GatewayIntegrationTest, PushServiceBusinessResponsesReturnToOwningIoCore) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto login = client.exchange(net::protocol::kLoginRequest, 601, "core_user|token:core_user");
    EXPECT_EQ(login.message_id, net::protocol::kLoginResponse);

    const auto room = client.exchange(net::protocol::kRoomCreateRequest, 602, "core_room");
    EXPECT_EQ(room.message_id, net::protocol::kRoomCreateResponse);

    ASSERT_TRUE(wait_until(std::chrono::milliseconds(1500), [&runtime]() {
        return runtime.server->dispatch_back_task_count() >= 2U;
    }));

    const auto sessions = runtime.session_manager.all_sessions();
    ASSERT_EQ(sessions.size(), 1U);
    const auto session_core = runtime.server->session_io_core(sessions.front());
    ASSERT_TRUE(session_core.has_value());

    {
        std::scoped_lock lock(runtime.push_dispatch_observation->mutex);
        ASSERT_FALSE(runtime.push_dispatch_observation->observed_cores.empty());
        for (const auto& observed_core : runtime.push_dispatch_observation->observed_cores) {
            ASSERT_TRUE(observed_core.has_value());
            EXPECT_EQ(*observed_core, *session_core);
        }
    }

    EXPECT_GE(runtime.server->dispatch_back_task_count(), 2U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, GatewayServerCanFanOutTasksAcrossAllIoCores) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    std::promise<void> promise;
    auto future = promise.get_future();
    std::mutex mutex;
    std::set<std::uint32_t> seen_cores;
    std::atomic<bool> completed{false};

    ASSERT_TRUE(runtime.server->dispatch_to_all_io_cores(
        [&](std::uint32_t core_id) {
            {
                std::scoped_lock lock(mutex);
                seen_cores.insert(core_id);
                if (const auto current_core = runtime.server->current_io_core(); current_core.has_value()) {
                    seen_cores.insert(*current_core);
                }
                if (seen_cores.size() >= runtime.server->io_core_count() &&
                    !completed.exchange(true, std::memory_order_relaxed)) {
                    promise.set_value();
                }
            }
        }));

    future.get();
    EXPECT_EQ(seen_cores.size(), runtime.server->io_core_count());
    EXPECT_EQ(runtime.server->dispatch_back_task_count(), 0U);
    runtime.stop();
}

TEST(GatewayIntegrationTest, GatewayServerSchedulesMaintenanceProbeAcrossAllIoCores) {
    app::logging::init("project_tests");

    GatewayTestRuntime runtime;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    ASSERT_TRUE(wait_until(std::chrono::milliseconds(500), [&runtime]() {
        const auto snapshots = runtime.server->io_core_snapshot();
        if (snapshots.size() != runtime.server->io_core_count()) {
            return false;
        }
        for (const auto& snapshot : snapshots) {
            if (snapshot.maintenance_probes == 0) {
                return false;
            }
        }
        return true;
    }));

    EXPECT_GE(runtime.server->maintenance_probe_task_count(), runtime.server->io_core_count());
    runtime.stop();
}

TEST(GatewayIntegrationTest, OptionalPacketBridgeMirrorsLoginRoomAndBattleTraffic) {
    app::logging::init("project_tests");

    auto bridge = std::make_shared<RecordingPacketBridge>();
    GatewayTestRuntime runtime;
    runtime.packet_bridge = bridge;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    {
        TestClient owner;
        TestClient member;
        owner.connect(runtime.server->local_port());
        member.connect(runtime.server->local_port());

        EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 180, "bridge_owner|token:bridge_owner").message_id,
                  net::protocol::kLoginResponse);
        EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 181, "bridge_member|token:bridge_member").message_id,
                  net::protocol::kLoginResponse);
        EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 182, "bridge_room").message_id,
                  net::protocol::kRoomCreateResponse);
        EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 183, "bridge_room").message_id,
                  net::protocol::kRoomJoinResponse);
        EXPECT_EQ(owner.expect_message(net::protocol::kRoomStatePush).message_id, net::protocol::kRoomStatePush);

        owner.send(net::protocol::kRoomReadyRequest, 184, "true");
        EXPECT_EQ(read_until_message(owner, net::protocol::kRoomReadyResponse).request_id, 184U);
        member.send(net::protocol::kRoomReadyRequest, 185, "true");
        EXPECT_EQ(read_until_message(member, net::protocol::kRoomReadyResponse).request_id, 185U);

        owner.send(net::protocol::kBattleStartRequest, 186, "");
        EXPECT_EQ(read_until_message(owner, net::protocol::kBattleStartResponse).request_id, 186U);
        EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).message_id,
                  net::protocol::kBattleStatePush);
        owner.send(net::protocol::kBattleInputRequest, 187, "move:right");
        EXPECT_EQ(read_until_message(owner, net::protocol::kBattleInputResponse).request_id, 187U);
    }

    for (int i = 0; i < 50; ++i) {
        if (bridge->close_count >= 2) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    ASSERT_GE(bridge->packets.size(), 7U);
    EXPECT_EQ(bridge->packets[0].message_id, net::protocol::kLoginRequest);
    EXPECT_EQ(bridge->packets[1].message_id, net::protocol::kLoginRequest);
    EXPECT_EQ(bridge->packets[2].message_id, net::protocol::kRoomCreateRequest);
    EXPECT_EQ(bridge->packets[3].message_id, net::protocol::kRoomJoinRequest);
    EXPECT_EQ(bridge->packets[4].message_id, net::protocol::kRoomReadyRequest);
    EXPECT_EQ(bridge->packets[5].message_id, net::protocol::kRoomReadyRequest);
    EXPECT_EQ(bridge->packets[6].message_id, net::protocol::kBattleStartRequest);
    ASSERT_GE(bridge->packets.size(), 8U);
    EXPECT_EQ(bridge->packets[7].message_id, net::protocol::kBattleInputRequest);
    EXPECT_GE(bridge->close_count, 2U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, ShadowBridgePolicyCanDisableRoomAndEchoMirroring) {
    app::logging::init("project_tests");

    v2::gateway::GatewayServerShadowBridge::MirrorPolicy policy;
    policy.login = true;
    policy.room = false;
    policy.battle = true;
    policy.echo = false;

    auto bridge = std::make_shared<v2::gateway::GatewayServerShadowBridge>(
        policy, v2::gateway::GatewayServerShadowBridge::EmitPolicy{}, false);
    GatewayTestRuntime runtime;
    runtime.packet_bridge = bridge;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    {
        TestClient owner;
        TestClient member;
        owner.connect(runtime.server->local_port());
        member.connect(runtime.server->local_port());

        EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 280, "bridge_owner|token:bridge_owner").message_id,
                  net::protocol::kLoginResponse);
        EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 281, "bridge_member|token:bridge_member").message_id,
                  net::protocol::kLoginResponse);
        EXPECT_EQ(owner.exchange(net::protocol::kEchoRequest, 282, "skip_echo").message_id,
                  net::protocol::kEchoResponse);
        EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 283, "bridge_room").message_id,
                  net::protocol::kRoomCreateResponse);
        EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 284, "bridge_room").message_id,
                  net::protocol::kRoomJoinResponse);
        EXPECT_EQ(owner.expect_message(net::protocol::kRoomStatePush).message_id, net::protocol::kRoomStatePush);

        owner.send(net::protocol::kRoomReadyRequest, 285, "true");
        EXPECT_EQ(read_until_message(owner, net::protocol::kRoomReadyResponse).request_id, 285U);
        member.send(net::protocol::kRoomReadyRequest, 286, "true");
        EXPECT_EQ(read_until_message(member, net::protocol::kRoomReadyResponse).request_id, 286U);
        owner.send(net::protocol::kBattleStartRequest, 287, "");
        EXPECT_EQ(read_until_message(owner, net::protocol::kBattleStartResponse).request_id, 287U);
    }

    EXPECT_TRUE(bridge->should_forward(net::protocol::kLoginRequest));
    EXPECT_FALSE(bridge->should_forward(net::protocol::kEchoRequest));
    EXPECT_FALSE(bridge->should_forward(net::protocol::kRoomCreateRequest));
    EXPECT_TRUE(bridge->should_forward(net::protocol::kBattleStartRequest));

    runtime.stop();
}

TEST(GatewayIntegrationTest, ShadowBridgeTracksMirrorsAndScheduledWrites) {
    app::logging::init("project_tests");

    auto bridge = std::make_shared<v2::gateway::GatewayServerShadowBridge>(
        v2::gateway::GatewayServerShadowBridge::MirrorPolicy(false, false, false, true),
        v2::gateway::GatewayServerShadowBridge::EmitPolicy{},
        true);
    GatewayTestRuntime runtime;
    runtime.packet_bridge = bridge;
    SKIP_IF_RUNTIME_UNAVAILABLE(runtime);

    TestClient client;
    client.connect(runtime.server->local_port());
    const auto first = client.exchange(net::protocol::kEchoRequest, 901, "shadow_stats");
    EXPECT_EQ(first.message_id, net::protocol::kEchoResponse);
    const auto mirrored = client.try_read_for(std::chrono::milliseconds(300));
    ASSERT_TRUE(mirrored.has_value());
    EXPECT_EQ(mirrored->message_id, net::protocol::kEchoResponse);

    ASSERT_TRUE(wait_until(std::chrono::milliseconds(300), [&]() {
        return bridge->dispatch_stats().mirrored_packets >= 1 &&
               bridge->dispatch_stats().emitted_writes >= 1;
    }));
    const auto stats = bridge->dispatch_stats();
    EXPECT_GE(stats.mirrored_packets, 1U);
    EXPECT_GE(stats.emitted_writes, 1U);
    EXPECT_GE(stats.scheduled_writes, 1U);
    EXPECT_EQ(stats.inline_writes, 0U);

    runtime.stop();
}

TEST(GatewayIntegrationTest, EchoServerConfigEnablesEchoShadowBridgeResponses) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_shadow_on.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": true,\n";
        output << "    \"v2_shadow_bridge_login\": false,\n";
        output << "    \"v2_shadow_bridge_room\": false,\n";
        output << "    \"v2_shadow_bridge_battle\": false,\n";
        output << "    \"v2_shadow_bridge_echo\": true,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient client;
    client.connect(*port);
    const auto first = client.exchange(net::protocol::kEchoRequest, 310, "shadow_echo");
    EXPECT_EQ(first.message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(first.body, "shadow_echo");

    const auto second = client.try_read_for(std::chrono::milliseconds(300));
    ASSERT_TRUE(second.has_value());
    EXPECT_EQ(second->message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(second->body, "shadow_echo");

    server.stop();
    std::filesystem::remove(path);
}

TEST(GatewayIntegrationTest, EchoServerConfigCanDisableEchoShadowBridgeResponses) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_shadow_off.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": true,\n";
        output << "    \"v2_shadow_bridge_login\": false,\n";
        output << "    \"v2_shadow_bridge_room\": false,\n";
        output << "    \"v2_shadow_bridge_battle\": false,\n";
        output << "    \"v2_shadow_bridge_echo\": false,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient client;
    client.connect(*port);
    const auto first = client.exchange(net::protocol::kEchoRequest, 320, "plain_echo");
    EXPECT_EQ(first.message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(first.body, "plain_echo");

    const auto second = client.try_read_for(std::chrono::milliseconds(300));
    EXPECT_FALSE(second.has_value());

    server.stop();
    std::filesystem::remove(path);
}

TEST(GatewayIntegrationTest, EchoServerConfigCanRestrictBattleShadowResponsesByKind) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_battle_shadow_started_only.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": true,\n";
        output << "    \"v2_shadow_bridge_login\": true,\n";
        output << "    \"v2_shadow_bridge_room\": true,\n";
        output << "    \"v2_shadow_bridge_battle\": true,\n";
        output << "    \"v2_shadow_bridge_echo\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_input_push\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_started\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_frame\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_settlement\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_finished\": false,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient owner;
    TestClient member;
    owner.connect(*port);
    member.connect(*port);

    EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 330, "bridge_owner|token:bridge_owner").message_id,
              net::protocol::kLoginResponse);
    ASSERT_TRUE(owner.try_read_for(std::chrono::milliseconds(300)).has_value());
    EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 331, "bridge_member|token:bridge_member").message_id,
              net::protocol::kLoginResponse);
    ASSERT_TRUE(member.try_read_for(std::chrono::milliseconds(300)).has_value());

    EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 332, "bridge_room").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 333, "bridge_room").message_id,
              net::protocol::kRoomJoinResponse);
    (void)owner.expect_message(net::protocol::kRoomStatePush);

    owner.send(net::protocol::kRoomReadyRequest, 334, "true");
    EXPECT_EQ(owner.expect_message(net::protocol::kRoomReadyResponse).request_id, 334U);
    member.send(net::protocol::kRoomReadyRequest, 335, "true");
    EXPECT_EQ(member.expect_message(net::protocol::kRoomReadyResponse).request_id, 335U);

    owner.send(net::protocol::kBattleStartRequest, 336, "");
    std::vector<net::packet::DecodedPacket> start_responses;
    start_responses.push_back(owner.expect_message(net::protocol::kBattleStartResponse));
    for (auto& packet : collect_matching_messages(owner, net::protocol::kBattleStartResponse, std::chrono::milliseconds(300))) {
        start_responses.push_back(std::move(packet));
    }
    EXPECT_TRUE(contains_body(start_responses, "battle_started:room_id=bridge_room:battle_id=battle_0001"));

    std::vector<net::packet::DecodedPacket> member_started;
    member_started.push_back(member.expect_message(net::protocol::kBattleStatePush));
    for (auto& packet : collect_matching_messages(member, net::protocol::kBattleStatePush, std::chrono::milliseconds(300))) {
        member_started.push_back(std::move(packet));
    }
    EXPECT_TRUE(contains_body(member_started, "battle_state:started:bridge_room:2"));
    EXPECT_TRUE(contains_body(member_started, "battle_state:kind=started:room_id=bridge_room:battle_id=battle_0001"));

    owner.send(net::protocol::kBattleInputRequest, 337, "move:right");
    std::vector<net::packet::DecodedPacket> input_responses;
    input_responses.push_back(owner.expect_message(net::protocol::kBattleInputResponse));
    for (auto& packet : collect_matching_messages(owner, net::protocol::kBattleInputResponse, std::chrono::milliseconds(300))) {
        input_responses.push_back(std::move(packet));
    }
    EXPECT_TRUE(contains_body(input_responses, "input_seq:seq=1"));

    const auto member_input = member.expect_message(net::protocol::kBattleInputPush);
    EXPECT_EQ(member_input.body, "battle_input:bridge_room:bridge_owner:1:move:right");
    EXPECT_FALSE(member.try_read_for(std::chrono::milliseconds(300)).has_value());

    server.stop();
    std::filesystem::remove(path);
}

TEST(GatewayIntegrationTest, EchoServerConfigCanMirrorOnlyBattleFinishKinds) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_battle_shadow_finish_only.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": true,\n";
        output << "    \"v2_shadow_bridge_login\": true,\n";
        output << "    \"v2_shadow_bridge_room\": true,\n";
        output << "    \"v2_shadow_bridge_battle\": true,\n";
        output << "    \"v2_shadow_bridge_echo\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_input_push\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_started\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_frame\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_settlement\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_finished\": true,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient owner;
    TestClient member;
    owner.connect(*port);
    member.connect(*port);

    EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 340, "bridge_owner|token:bridge_owner").message_id,
              net::protocol::kLoginResponse);
    ASSERT_TRUE(owner.try_read_for(std::chrono::milliseconds(300)).has_value());
    EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 341, "bridge_member|token:bridge_member").message_id,
              net::protocol::kLoginResponse);
    ASSERT_TRUE(member.try_read_for(std::chrono::milliseconds(300)).has_value());

    EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 342, "bridge_room_finish").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 343, "bridge_room_finish").message_id,
              net::protocol::kRoomJoinResponse);
    (void)owner.expect_message(net::protocol::kRoomStatePush);

    owner.send(net::protocol::kRoomReadyRequest, 344, "true");
    EXPECT_EQ(owner.expect_message(net::protocol::kRoomReadyResponse).request_id, 344U);
    member.send(net::protocol::kRoomReadyRequest, 345, "true");
    EXPECT_EQ(member.expect_message(net::protocol::kRoomReadyResponse).request_id, 345U);

    owner.send(net::protocol::kBattleStartRequest, 346, "");
    (void)owner.expect_message(net::protocol::kBattleStartResponse);
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body, "battle_state:started:bridge_room_finish:2");
    EXPECT_FALSE(member.try_read_for(std::chrono::milliseconds(300)).has_value());

    owner.send(net::protocol::kBattleInputRequest, 347, "finish:surrender");
    const auto owner_settlement = owner.expect_message(net::protocol::kBattleStatePush);
    const auto member_settlement = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(owner_settlement.body,
              "battle_state:kind=settlement:room_id=bridge_room_finish:battle_id=battle_0001:reason=surrender:user_id=bridge_owner");
    EXPECT_EQ(member_settlement.body,
              "battle_state:kind=settlement:room_id=bridge_room_finish:battle_id=battle_0001:reason=surrender:user_id=bridge_owner");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).body, "battle_end_accepted:surrender");
    const auto owner_finished = owner.expect_message(net::protocol::kBattleStatePush);
    const auto member_finished = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(owner_finished.body,
              "battle_state:kind=finished:room_id=bridge_room_finish:battle_id=battle_0001:reason=surrender:user_id=bridge_owner");
    EXPECT_EQ(member_finished.body,
              "battle_state:kind=finished:room_id=bridge_room_finish:battle_id=battle_0001:reason=surrender:user_id=bridge_owner");

    server.stop();
    std::filesystem::remove(path);
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

TEST(GatewayIntegrationTest, FullChainShadowBridgeBattleLifecycle) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_full_chain.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": false,\n";
        output << "    \"v2_shadow_bridge_login\": true,\n";
        output << "    \"v2_shadow_bridge_room\": false,\n";
        output << "    \"v2_shadow_bridge_battle\": true,\n";
        output << "    \"v2_shadow_bridge_echo\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_input_push\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_started\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_frame\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_settlement\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_finished\": true,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient owner;
    TestClient member;
    owner.connect(*port);
    member.connect(*port);

    // Use exchange() exclusively — each call sends a request and reads the first
    // response. With emit_responses=true, the v2 shadow response arrives first
    // (sync), and the v1 response arrives second (async). exchange() returns the
    // v2 response; the v1 response stays in the buffer. This test just verifies
    // the flow completes without errors.

    // Step 1: Login owner
    ASSERT_EQ(owner.exchange(net::protocol::kLoginRequest, 400, "owner_fc|token:owner_fc|OwnerFC").message_id,
              net::protocol::kLoginResponse);

    // Step 2: Login member
    ASSERT_EQ(member.exchange(net::protocol::kLoginRequest, 401, "member_fc|token:member_fc|MemberFC").message_id,
              net::protocol::kLoginResponse);

    // Step 3: Create room
    ASSERT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 402, "room_fc").message_id,
              net::protocol::kRoomCreateResponse);

    // Step 4: Join room
    ASSERT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 403, "room_fc").message_id,
              net::protocol::kRoomJoinResponse);

    // Step 5: Drain room state push
    (void)owner.expect_message(net::protocol::kRoomStatePush);

    // Step 6: Ready both — use send+expect_message to handle interleaved pushes
    owner.send(net::protocol::kRoomReadyRequest, 404, "true");
    EXPECT_EQ(owner.expect_message(net::protocol::kRoomReadyResponse).request_id, 404U);
    member.send(net::protocol::kRoomReadyRequest, 405, "true");
    EXPECT_EQ(member.expect_message(net::protocol::kRoomReadyResponse).request_id, 405U);

    // Step 7: Start battle (v2 mirrors battle)
    owner.send(net::protocol::kBattleStartRequest, 406, "");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStartResponse).message_id,
              net::protocol::kBattleStartResponse);

    // Step 8: Submit scored input
    owner.send(net::protocol::kBattleInputRequest, 407, "score=30:move:up");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).request_id, 407U);

    // Step 9: Finish battle (surrender)
    owner.send(net::protocol::kBattleInputRequest, 408, "finish:surrender");
    const auto finish = owner.expect_message(net::protocol::kBattleInputResponse);
    EXPECT_EQ(finish.request_id, 408U);

    server.stop();
    std::filesystem::remove(path);
}

TEST(GatewayIntegrationTest, EchoServerConfigEchoOnlyMirror) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_echo_only_mirror.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": true,\n";
        output << "    \"v2_shadow_bridge_login\": false,\n";
        output << "    \"v2_shadow_bridge_room\": false,\n";
        output << "    \"v2_shadow_bridge_battle\": false,\n";
        output << "    \"v2_shadow_bridge_echo\": true,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient client;
    client.connect(*port);

    EXPECT_EQ(client.exchange(net::protocol::kLoginRequest, 500, "echo_user|token:echo_user").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(client.exchange(net::protocol::kEchoRequest, 501, "hello_v2_echo").message_id,
              net::protocol::kEchoResponse);

    const auto echo_second = client.expect_message(net::protocol::kEchoResponse);
    EXPECT_EQ(echo_second.body, "hello_v2_echo");

    server.stop();
    std::filesystem::remove(path);
}

TEST(GatewayIntegrationTest, EchoServerConfigAllEmitPolicyBitsOn) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_all_emit_on.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": false,\n";
        output << "    \"v2_shadow_bridge_login\": true,\n";
        output << "    \"v2_shadow_bridge_room\": true,\n";
        output << "    \"v2_shadow_bridge_battle\": true,\n";
        output << "    \"v2_shadow_bridge_echo\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_input_push\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_started\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_frame\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_settlement\": true,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_finished\": true,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient owner;
    TestClient member;
    owner.connect(*port);
    member.connect(*port);

    EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 510, "bridge_owner|token:bridge_owner").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 511, "bridge_member|token:bridge_member").message_id,
              net::protocol::kLoginResponse);

    EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 512, "bridge_room_all").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 513, "bridge_room_all").message_id,
              net::protocol::kRoomJoinResponse);
    (void)owner.expect_message(net::protocol::kRoomStatePush);

    owner.send(net::protocol::kRoomReadyRequest, 514, "true");
    EXPECT_EQ(owner.expect_message(net::protocol::kRoomReadyResponse).request_id, 514U);
    member.send(net::protocol::kRoomReadyRequest, 515, "true");
    EXPECT_EQ(member.expect_message(net::protocol::kRoomReadyResponse).request_id, 515U);

    owner.send(net::protocol::kBattleStartRequest, 516, "");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStartResponse).message_id,
              net::protocol::kBattleStartResponse);

    owner.send(net::protocol::kBattleInputRequest, 517, "score=10:move:right");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).request_id, 517U);

    owner.send(net::protocol::kBattleInputRequest, 518, "finish:surrender");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).request_id, 518U);

    server.stop();
    std::filesystem::remove(path);
}

TEST(GatewayIntegrationTest, EchoServerConfigV2BattleWithoutRoomIsGraceful) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "echo_server_v2_battle_no_room.json";
    const auto port = reserve_free_port();
    if (!port.has_value()) {
        GTEST_SKIP() << "socket bind unavailable in this environment";
    }
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"port\": " << *port << ",\n";
        output << "    \"io_threads\": 1,\n";
        output << "    \"business_threads\": 1,\n";
        output << "    \"http_management_port\": 0,\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_emit_responses\": true,\n";
        output << "    \"v2_shadow_bridge_login\": true,\n";
        output << "    \"v2_shadow_bridge_room\": false,\n";
        output << "    \"v2_shadow_bridge_battle\": true,\n";
        output << "    \"v2_shadow_bridge_echo\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_input_push\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_started\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_frame\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_settlement\": false,\n";
        output << "    \"v2_shadow_bridge_emit_battle_state_finished\": false,\n";
        output << "    \"auth\": {\n";
        output << "      \"provider\": \"dev\"\n";
        output << "    }\n";
        output << "  }\n";
        output << "}\n";
    }

    EchoServerProcess server(path);
    if (!server.start()) {
        std::filesystem::remove(path);
        GTEST_SKIP() << "failed to start echo_server process: " << server.startup_error();
    }
    if (!wait_for_tcp_server(*port, std::chrono::milliseconds(1500))) {
        server.stop();
        std::filesystem::remove(path);
        GTEST_SKIP() << "echo_server did not start listening in time";
    }

    TestClient owner;
    TestClient member;
    owner.connect(*port);
    member.connect(*port);

    EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 520, "bridge_owner|token:bridge_owner").message_id,
              net::protocol::kLoginResponse);
    (void)owner.expect_message(net::protocol::kLoginResponse);
    EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 521, "bridge_member|token:bridge_member").message_id,
              net::protocol::kLoginResponse);
    (void)member.expect_message(net::protocol::kLoginResponse);

    EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 522, "bridge_room_v2").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 523, "bridge_room_v2").message_id,
              net::protocol::kRoomJoinResponse);
    (void)owner.expect_message(net::protocol::kRoomStatePush);

    owner.send(net::protocol::kRoomReadyRequest, 524, "true");
    EXPECT_EQ(owner.expect_message(net::protocol::kRoomReadyResponse).request_id, 524U);
    member.send(net::protocol::kRoomReadyRequest, 525, "true");
    EXPECT_EQ(member.expect_message(net::protocol::kRoomReadyResponse).request_id, 525U);

    owner.send(net::protocol::kBattleStartRequest, 526, "");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStartResponse).message_id,
              net::protocol::kBattleStartResponse);

    owner.send(net::protocol::kBattleInputRequest, 527, "move:right");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).request_id, 527U);

    server.stop();
    std::filesystem::remove(path);
}
