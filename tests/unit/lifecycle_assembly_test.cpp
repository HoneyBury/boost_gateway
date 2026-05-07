#include "app/config.h"
#include "app/config_watcher.h"
#include "app/logging.h"
#include "game/battle/battle_manager.h"
#include "game/battle/battle_service.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/push_service.h"
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

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

void write_text(const std::filesystem::path& path, const std::string& content) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output << content;
}

void bump_last_write_time(const std::filesystem::path& path) {
    const auto current = std::filesystem::last_write_time(path);
    std::filesystem::last_write_time(path, current + std::chrono::seconds(1));
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

struct LifecycleRuntime {
    asio::io_context io_context;
    boost::asio::thread_pool business_pool{2};
    net::MessageDispatcher dispatcher{business_pool};
    game::gateway::SessionManager session_manager;
    game::room::RoomManager room_manager;
    game::battle::BattleManager battle_manager;
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push_service;
    game::login::DevTokenValidator token_validator;
    std::unique_ptr<game::gateway::GatewayService> gateway_service;
    std::unique_ptr<game::login::LoginService> login_service;
    std::unique_ptr<game::room::RoomService> room_service;
    std::unique_ptr<game::battle::BattleService> battle_service;
    std::unique_ptr<game::gateway::GatewayServer> server;
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

            server = std::make_unique<game::gateway::GatewayServer>(
                io_context,
                dispatcher,
                session_manager,
                room_manager,
                battle_manager,
                metrics,
                0,
                0,
                net::SessionOptions{},
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
        const auto outbound = net::packet::encode(message_id, request_id, 0, body);
        asio::write(socket_, asio::buffer(outbound));

        net::packet::LengthHeader header{};
        asio::read(socket_, asio::buffer(header));
        const auto payload_length = net::packet::decode_length(header);
        std::vector<char> payload(payload_length);
        asio::read(socket_, asio::buffer(payload));
        return net::packet::decode_payload(payload);
    }

private:
    asio::io_context io_context_;
    tcp::socket socket_;
};

TEST(LifecycleAssemblyTest, ConfigWatcherSkipsInvalidReloadAndAppliesValidReloadOnly) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "gateway_config_watcher_test.json";
    write_text(path, "{\n  \"gateway\": {\"port\": 9100}\n}\n");

    asio::io_context io_context;
    std::vector<std::uint16_t> reloaded_ports;

    app::config::ConfigWatcher watcher(
        io_context.get_executor(),
        path,
        [&](const app::config::GatewayAppConfig& cfg) {
            reloaded_ports.push_back(cfg.port);
        });

    watcher.start(std::chrono::seconds(1));
    std::thread io_thread([&]() { io_context.run(); });

    std::this_thread::sleep_for(std::chrono::milliseconds(40));

    write_text(path, "{ invalid json");
    bump_last_write_time(path);
    std::this_thread::sleep_for(std::chrono::milliseconds(1100));

    write_text(path, "{\n  \"gateway\": {\"port\": 9200}\n}\n");
    bump_last_write_time(path);
    std::this_thread::sleep_for(std::chrono::milliseconds(1100));

    watcher.stop();
    io_context.stop();
    io_thread.join();

    EXPECT_EQ(reloaded_ports.size(), 1U);
    EXPECT_EQ(reloaded_ports.front(), 9200);

    std::filesystem::remove(path);
}

TEST(LifecycleAssemblyTest, ConfigWatcherStopPreventsFutureReloadCallback) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "gateway_config_watcher_stop_test.json";
    write_text(path, "{\n  \"gateway\": {\"port\": 9300}\n}\n");

    asio::io_context io_context;
    std::atomic<int> reload_count{0};

    app::config::ConfigWatcher watcher(
        io_context.get_executor(),
        path,
        [&](const app::config::GatewayAppConfig&) {
            reload_count.fetch_add(1, std::memory_order_relaxed);
        });

    watcher.start(std::chrono::seconds(1));
    std::thread io_thread([&]() { io_context.run(); });

    watcher.stop();
    write_text(path, "{\n  \"gateway\": {\"port\": 9301}\n}\n");
    bump_last_write_time(path);
    std::this_thread::sleep_for(std::chrono::milliseconds(1100));

    io_context.stop();
    io_thread.join();

    EXPECT_EQ(reload_count.load(std::memory_order_relaxed), 0);

    std::filesystem::remove(path);
}

TEST(LifecycleAssemblyTest, ServerStopClosesSessionsAndCleansRoomState) {
    app::logging::init("project_tests");

    LifecycleRuntime runtime;
    if (!runtime.start()) {
        GTEST_SKIP() << "socket bind unavailable in this environment: " << runtime.startup_error;
    }

    TestClient client;
    client.connect(runtime.server->local_port());

    const auto login_response =
        client.exchange(net::protocol::kLoginRequest, 8001, "lifecycle_user|token:lifecycle_user|LifecycleUser");
    EXPECT_EQ(login_response.message_id, net::protocol::kLoginResponse);

    const auto room_response = client.exchange(net::protocol::kRoomCreateRequest, 8002, "room_lifecycle");
    EXPECT_EQ(room_response.message_id, net::protocol::kRoomCreateResponse);

    ASSERT_TRUE(wait_until(std::chrono::milliseconds(300), [&]() {
        return runtime.server->active_connections() == 1 &&
               runtime.session_manager.snapshot().active_sessions == 1 &&
               runtime.room_manager.room_count() == 1;
    }));

    runtime.server->stop();

    EXPECT_TRUE(wait_until(std::chrono::milliseconds(500), [&]() {
        return runtime.server->active_connections() == 0 &&
               runtime.session_manager.snapshot().active_sessions == 0 &&
               runtime.room_manager.room_count() == 0;
    }));

    runtime.stop();
}

}  // namespace
