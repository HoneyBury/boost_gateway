// SDK v4.1.0: Client tests
#include <gtest/gtest.h>
#include "boost_gateway/sdk/client.h"
using namespace boost_gateway::sdk;
using namespace std::chrono_literals;

TEST(ClientV4Test, DefaultState) {
    SdkClient c;
    EXPECT_FALSE(c.is_connected());
}

TEST(ClientV4Test, ConnectFailGracefully) {
    SdkClient c;
    EXPECT_FALSE(c.connect("127.0.0.1", 1, 100ms));
    EXPECT_FALSE(c.is_connected());
}

TEST(ClientV4Test, LoginWithoutConnection) {
    SdkClient c;
    auto r = c.login("u", "t", 100ms);
    EXPECT_FALSE(r.ok);
}

TEST(ClientV4Test, RoomOpsWithoutConnection) {
    SdkClient c;
    EXPECT_FALSE(c.create_room("r", 100ms).ok);
    EXPECT_FALSE(c.join_room("r", 100ms).ok);
    EXPECT_FALSE(c.leave_room("r", 100ms).ok);
}

TEST(ClientV4Test, DisconnectSafeWhenNotConnected) {
    SdkClient c;
    c.disconnect();  // no crash
    SUCCEED();
}

TEST(ClientV4Test, EchoWithoutConnection) {
    SdkClient c;
    auto r = c.echo("hi", 100ms);
    EXPECT_FALSE(r.ok);
}
