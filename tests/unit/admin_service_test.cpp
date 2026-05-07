#include "app/logging.h"
#include "game/gateway/admin_service.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio/thread_pool.hpp>

#include <filesystem>
#include <fstream>
#include <future>
#include <memory>
#include <string>

#include <gtest/gtest.h>

namespace {

std::string slurp(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(input), {});
}

}  // namespace

TEST(AdminServiceTest, RegistersAllAdminHandlers) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager sm;
    game::gateway::GatewayMetrics metrics;

    game::gateway::AdminService admin(sm, metrics);
    admin.register_handlers(dispatcher);

    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kAdminKickPlayer));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kAdminBanIp));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kAdminServerStatus));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kAdminReloadConfig));
    EXPECT_EQ(dispatcher.handler_count(), 4u);
}

TEST(AdminServiceTest, ServerStatusCallback) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager sm;
    game::gateway::GatewayMetrics metrics;

    game::gateway::AdminService admin(sm, metrics);
    admin.set_status_callback([] { return "{\"status\":\"ok\"}"; });
    admin.register_handlers(dispatcher);

    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kAdminServerStatus));
}

TEST(AdminServiceTest, DispatchInvokesCallbacksWithoutGatewayDefaultWiring) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager sm;
    game::gateway::GatewayMetrics metrics;

    auto kick_promise = std::make_shared<std::promise<std::string>>();
    auto ban_promise = std::make_shared<std::promise<std::string>>();
    auto reload_promise = std::make_shared<std::promise<bool>>();

    game::gateway::AdminService admin(sm, metrics);
    admin.set_kick_callback([kick_promise](const std::string& user_id) {
        kick_promise->set_value(user_id);
    });
    admin.set_ban_callback([ban_promise](const std::string& ip, std::uint32_t duration_sec) {
        ban_promise->set_value(ip + ":" + std::to_string(duration_sec));
    });
    admin.set_reload_callback([reload_promise] {
        reload_promise->set_value(true);
    });
    admin.register_handlers(dispatcher);

    EXPECT_TRUE(dispatcher.dispatch({}, net::protocol::kAdminKickPlayer, 7001, 0, "player_to_kick", 9101));
    EXPECT_TRUE(dispatcher.dispatch({}, net::protocol::kAdminBanIp, 7002, 0, "127.0.0.1", 9102));
    EXPECT_TRUE(dispatcher.dispatch({}, net::protocol::kAdminReloadConfig, 7003, 0, "reload", 9103));

    pool.join();

    EXPECT_EQ(kick_promise->get_future().get(), "player_to_kick");
    EXPECT_EQ(ban_promise->get_future().get(), "127.0.0.1:3600");
    EXPECT_TRUE(reload_promise->get_future().get());
}

TEST(AdminServiceTest, WritesAdminInvokeAuditWithRequiredKeysAndSanitizedExcerpt) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager sm;
    game::gateway::GatewayMetrics metrics;

    game::gateway::AdminService admin(sm, metrics);
    admin.register_handlers(dispatcher);

    const std::string payload = "10.0.0.1\"\n\t\\oops";
    ASSERT_TRUE(dispatcher.dispatch({}, net::protocol::kAdminBanIp, 7311, 0, payload, 88117));

    pool.join();

    const auto log_text = slurp("logs/audit.log");
    EXPECT_NE(log_text.find("\"event\":\"admin_invoke\""), std::string::npos);
    EXPECT_NE(log_text.find("layer=L3_admin action=ban_ip outcome=accepted actor_endpoint=none request_id=7311 trace_id=88117"),
              std::string::npos);
    EXPECT_NE(log_text.find("payload_excerpt=10.0.0.1____oops"), std::string::npos);
}
