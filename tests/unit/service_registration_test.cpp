#include "game/battle/battle_service.h"
#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/login_service.h"
#include "game/login/token_validator.h"
#include "game/room/room_manager.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio/thread_pool.hpp>

#include <gtest/gtest.h>

TEST(ServiceRegistrationTest, RegistersCoreBusinessHandlersAndMiddleware) {
    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager session_manager;
    game::room::RoomManager room_manager;
    game::battle::BattleManager battle_manager;
    game::gateway::GatewayMetrics metrics;
    game::login::DevTokenValidator token_validator;

    game::gateway::GatewayService gateway_service(session_manager, metrics);
    game::login::LoginService login_service(session_manager, token_validator, metrics);
    game::room::RoomService room_service(session_manager, battle_manager, room_manager, metrics);
    game::battle::BattleService battle_service(session_manager, room_manager, battle_manager, metrics);

    gateway_service.register_handlers(dispatcher);
    login_service.register_handlers(dispatcher);
    room_service.register_handlers(dispatcher);
    battle_service.register_handlers(dispatcher);

    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kHeartbeatRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kLoginRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kRoomCreateRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kRoomJoinRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kRoomLeaveRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kRoomReadyRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kBattleStartRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kBattleInputRequest));
    EXPECT_EQ(dispatcher.handler_count(), 8U);
    EXPECT_EQ(dispatcher.middleware_count(), 2U);
}
