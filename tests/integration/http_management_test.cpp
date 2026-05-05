#include "app/config.h"
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
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>
#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/http/message.hpp>
#include <boost/beast/http/read.hpp>
#include <boost/beast/http/string_body.hpp>
#include <boost/beast/http/write.hpp>

#include <gtest/gtest.h>

#include <cstdint>
#include <memory>
#include <string>
#include <thread>

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
using tcp = asio::ip::tcp;

class HttpManagementTest : public ::testing::Test {
protected:
    void SetUp() override {
        app::logging::init("http_management_test");

        token_validator = std::make_unique<game::login::DevTokenValidator>();

        gateway_service = std::make_unique<game::gateway::GatewayService>(session_manager, metrics);
        login_service = std::make_unique<game::login::LoginService>(
            session_manager, push_service, room_manager, *token_validator, metrics);
        room_service = std::make_unique<game::room::RoomService>(
            session_manager, push_service, battle_manager, room_manager, metrics);
        battle_service = std::make_unique<game::battle::BattleService>(
            session_manager, push_service, room_manager, battle_manager, metrics);

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
            0,             // game port (auto-assign — not used in this test)
            kManagementPort,
            net::SessionOptions{},
            std::chrono::milliseconds(60000));
        server->start();

        io_thread = std::thread([this]() { io_context.run(); });
    }

    void TearDown() override {
        server->stop();
        io_context.stop();
        if (io_thread.joinable()) {
            io_thread.join();
        }
        business_pool.join();
    }

    [[nodiscard]] std::string http_get(std::string_view path) {
        tcp::resolver resolver(io_context);
        const auto endpoints = resolver.resolve("127.0.0.1", std::to_string(kManagementPort));

        tcp::socket socket(io_context);
        asio::connect(socket, endpoints);

        http::request<http::string_body> req{http::verb::get, std::string(path), 11};
        req.set(http::field::host, "127.0.0.1");
        http::write(socket, req);

        beast::flat_buffer buffer;
        http::response<http::string_body> res;
        http::read(socket, buffer, res);

        socket.close();
        return res.body();
    }

    static constexpr std::uint16_t kManagementPort = 19080;

    asio::io_context io_context;
    boost::asio::thread_pool business_pool{2};
    net::MessageDispatcher dispatcher{business_pool};
    game::gateway::SessionManager session_manager;
    game::room::RoomManager room_manager;
    game::battle::BattleManager battle_manager;
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push_service;
    std::unique_ptr<game::login::TokenValidator> token_validator;
    std::unique_ptr<game::gateway::GatewayService> gateway_service;
    std::unique_ptr<game::login::LoginService> login_service;
    std::unique_ptr<game::room::RoomService> room_service;
    std::unique_ptr<game::battle::BattleService> battle_service;
    std::unique_ptr<game::gateway::GatewayServer> server;
    std::thread io_thread;
};

TEST_F(HttpManagementTest, HealthEndpointReturnsOk) {
    const auto body = http_get("/health");
    EXPECT_NE(body.find("ok"), std::string::npos);
}

TEST_F(HttpManagementTest, MetricsEndpointReturnsPrometheusText) {
    const auto body = http_get("/metrics");
    EXPECT_NE(body.find("# TYPE gateway_sessions_accepted_total counter"), std::string::npos);
    EXPECT_NE(body.find("gateway_sessions_accepted_total "), std::string::npos);
    EXPECT_NE(body.find("gateway_active_sessions "), std::string::npos);
}

TEST_F(HttpManagementTest, MetricsJsonEndpointReturnsJson) {
    const auto body = http_get("/metrics/json");
    EXPECT_NE(body.find("\"accepted_sessions\""), std::string::npos);
    EXPECT_NE(body.find("\"active_rooms\""), std::string::npos);
}

TEST_F(HttpManagementTest, UnknownPathReturnsNotFound) {
    const auto body = http_get("/bogus");
    EXPECT_NE(body.find("Not Found"), std::string::npos);
}
