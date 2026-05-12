// BoostGateway SDK unit tests.

#include <gtest/gtest.h>

#include "boost_gateway/sdk/client.h"
#include "boost_gateway/sdk/error.h"
#include "boost_gateway/sdk/types.h"

#include <chrono>
#include <thread>

using namespace std::chrono_literals;
namespace sdk = boost_gateway::sdk;

TEST(SdkTest, ClientInitialState) {
    sdk::SdkClient client;
    EXPECT_FALSE(client.is_connected());
}

TEST(SdkTest, ConnectAndDisconnect) {
    sdk::SdkClient client;
    // Attempt to connect to a likely-unused port (should fail or succeed gracefully)
    bool connected = client.connect("127.0.0.1", 19999, 500ms);
    // Verify disconnect doesn't crash regardless of connect result
    client.disconnect();
    SUCCEED();
}

TEST(SdkTest, LoginWithoutConnectionReturnsError) {
    sdk::SdkClient client;
    auto result = client.login("alice", "token:alice", 100ms);
    EXPECT_FALSE(result.ok);
}

TEST(SdkTest, CreateRoomWithoutConnectionReturnsError) {
    sdk::SdkClient client;
    auto result = client.create_room("test_room", 100ms);
    EXPECT_FALSE(result.ok);
}

TEST(SdkTest, TypesDefaultValues) {
    sdk::LoginResult lr;
    EXPECT_FALSE(lr.ok);
    EXPECT_EQ(lr.error_code, 0);

    sdk::RoomResult rr;
    EXPECT_FALSE(rr.ok);

    sdk::BattleStartResult br;
    EXPECT_FALSE(br.ok);

    sdk::BattleInputResult bi;
    EXPECT_FALSE(bi.ok);

    sdk::PushMessage pm;
    EXPECT_EQ(pm.message_id, 0U);
    EXPECT_TRUE(pm.body.empty());
}

TEST(SdkTest, EchoResult) {
    sdk::EchoResult er;
    EXPECT_FALSE(er.ok);
    EXPECT_TRUE(er.echo_body.empty());
}

TEST(SdkTest, SdkErrorToString) {
    EXPECT_STREQ(sdk::to_string(sdk::SdkError::kOk), "ok");
    EXPECT_STREQ(sdk::to_string(sdk::SdkError::kNotConnected), "not_connected");
    EXPECT_STREQ(sdk::to_string(sdk::SdkError::kTimeout), "timeout");
}

TEST(SdkTest, CallbackRegistration) {
    sdk::SdkClient client;
    int push_count = 0;
    client.on_push([&](const sdk::PushMessage&) { ++push_count; });

    bool disconnected = false;
    client.on_disconnect([&]() { disconnected = true; });

    // Callbacks should be stored but not invoked yet
    EXPECT_EQ(push_count, 0);
    EXPECT_FALSE(disconnected);
}
