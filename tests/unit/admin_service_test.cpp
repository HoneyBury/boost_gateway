#include "game/gateway/admin_service.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio/thread_pool.hpp>

#include <gtest/gtest.h>

TEST(AdminServiceTest, RegistersAllAdminHandlers) {
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
    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager sm;
    game::gateway::GatewayMetrics metrics;

    game::gateway::AdminService admin(sm, metrics);
    admin.set_status_callback([] { return "{\"status\":\"ok\"}"; });
    admin.register_handlers(dispatcher);

    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kAdminServerStatus));
}
