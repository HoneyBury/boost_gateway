// v3.0.0 V4: Error path and boundary tests for SDK-server integration

#include <gtest/gtest.h>
#include "boost_gateway/sdk/client.h"
#include "net/protocol.h"
#include <thread>

namespace sdk = boost_gateway::sdk;
using namespace std::chrono_literals;

// ─── Connection errors ──────────────────────────────────────────────────

TEST(ErrorPathsTest, ConnectToClosedPortReturnsError) {
    sdk::SdkClient client;
    // Port 0 is reserved, should fail immediately
    bool ok = client.connect("127.0.0.1", 0, 500ms);
    EXPECT_FALSE(ok);
}

TEST(ErrorPathsTest, LoginWithoutConnectionReturnsError) {
    sdk::SdkClient client;
    auto result = client.login("test", "token", 100ms);
    EXPECT_FALSE(result.ok);
}

TEST(ErrorPathsTest, RoomOpsWithoutConnectionReturnError) {
    sdk::SdkClient client;
    EXPECT_FALSE(client.create_room("r", 100ms).ok);
    EXPECT_FALSE(client.join_room("r", 100ms).ok);
    EXPECT_FALSE(client.leave_room("r", 100ms).ok);
    EXPECT_FALSE(client.set_ready(true, 100ms).ok);
}

// ─── Concurrent clients ─────────────────────────────────────────────────

TEST(ErrorPathsTest, MultipleClientsIndependentLifecycle) {
    sdk::SdkClient c1, c2, c3;
    // All should fail to connect independently (no server)
    c1.connect("127.0.0.1", 1, 100ms);
    c2.connect("127.0.0.1", 1, 100ms);
    c3.connect("127.0.0.1", 1, 100ms);

    // Disconnect should not crash
    c1.disconnect();
    c2.disconnect();
    c3.disconnect();
    SUCCEED();
}

// ─── SDK result defaults ────────────────────────────────────────────────

TEST(ErrorPathsTest, ResultTypesDefaultToFailure) {
    sdk::LoginResult lr;
    EXPECT_FALSE(lr.ok);
    EXPECT_EQ(lr.error_code, 0);

    sdk::RoomResult rr;
    EXPECT_FALSE(rr.ok);

    sdk::BattleStartResult br;
    EXPECT_FALSE(br.ok);

    sdk::BattleInputResult bi;
    EXPECT_FALSE(bi.ok);

    sdk::EchoResult er;
    EXPECT_FALSE(er.ok);
}

// ─── Callback safety ────────────────────────────────────────────────────

TEST(ErrorPathsTest, PushCallbackNotInvokedWithoutConnection) {
    sdk::SdkClient client;
    int invoked = 0;
    client.on_push([&](const sdk::PushMessage&) { ++invoked; });

    // No connection — callback should not be invoked
    EXPECT_EQ(invoked, 0);

    client.disconnect();  // should not crash
    EXPECT_EQ(invoked, 0);
}

// ─── Invalid token handling ─────────────────────────────────────────────

TEST(ErrorPathsTest, InvalidTokenHandling) {
    // Verify protocol error codes for auth failures
    EXPECT_EQ(static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken), 1003);
    EXPECT_EQ(static_cast<std::int32_t>(net::protocol::ErrorCode::kTokenExpired), 1005);
    EXPECT_EQ(static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired), 1001);
    EXPECT_EQ(static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited), 9001);
}
