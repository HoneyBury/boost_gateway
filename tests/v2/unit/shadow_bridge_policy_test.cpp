#include "net/protocol.h"
#include "v2/gateway/gateway_server_bridge.h"

#include <gtest/gtest.h>

TEST(V2ShadowBridgePolicyTest, MirrorsConfiguredProtocolDomainsOnly) {
    v2::gateway::GatewayServerShadowBridge::MirrorPolicy policy;
    policy.login = true;
    policy.room = false;
    policy.battle = true;
    policy.echo = false;

    v2::gateway::GatewayServerShadowBridge bridge(policy, false);

    EXPECT_TRUE(bridge.should_forward(net::protocol::kLoginRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kRoomCreateRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kRoomJoinRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kRoomReadyRequest));
    EXPECT_TRUE(bridge.should_forward(net::protocol::kBattleStartRequest));
    EXPECT_TRUE(bridge.should_forward(net::protocol::kBattleInputRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kEchoRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kHeartbeatRequest));
}
