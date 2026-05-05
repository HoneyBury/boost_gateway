#include "game/battle/battle_service.h"
#include "game/gateway/gateway_service.h"
#include "game/login/login_service.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio/thread_pool.hpp>

#include <gtest/gtest.h>

TEST(ServiceRegistrationTest, RegistersCoreBusinessHandlers) {
    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);

    game::gateway::GatewayService gateway_service;
    game::login::LoginService login_service;
    game::room::RoomService room_service;
    game::battle::BattleService battle_service;

    gateway_service.register_handlers(dispatcher);
    login_service.register_handlers(dispatcher);
    room_service.register_handlers(dispatcher);
    battle_service.register_handlers(dispatcher);

    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kHeartbeatRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kLoginRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kRoomJoinRequest));
    EXPECT_TRUE(dispatcher.has_handler(net::protocol::kBattleStartRequest));
    EXPECT_EQ(dispatcher.handler_count(), 4U);
}
