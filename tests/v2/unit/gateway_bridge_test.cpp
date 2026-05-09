#include <gtest/gtest.h>

#include <memory>

#include "net/protocol.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

TEST(V2GatewayBridgeTest, EchoRequestReturnsEchoResponse) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 7;
    envelope.protocol_message_id = net::protocol::kEchoRequest;
    envelope.request_id = 99;
    envelope.body = "hello-gateway";

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(writes.front().envelope.request_id, 99U);
    EXPECT_EQ(writes.front().envelope.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kOk));
    EXPECT_EQ(writes.front().envelope.body, "hello-gateway");
}

TEST(V2GatewayBridgeTest, LoginRequestReturnsLoginResponse) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 8;
    envelope.protocol_message_id = net::protocol::kLoginRequest;
    envelope.request_id = 100;
    envelope.body = "player_01|token:player_01|PlayerOne";

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(writes.front().envelope.body, "login_ok:player_01");
}

TEST(V2GatewayBridgeTest, LoginRequestRejectsEmptyUserId) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 8;
    envelope.protocol_message_id = net::protocol::kLoginRequest;
    envelope.request_id = 101;
    envelope.body = "|token:player_01";

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(writes.front().envelope.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidUserId));
}

TEST(V2GatewayBridgeTest, NonWhitelistedMessageIsRejected) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 9;
    envelope.protocol_message_id = net::protocol::kRoomJoinRequest;
    envelope.request_id = 101;
    envelope.body = "room_alpha";

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(writes.front().envelope.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kAuthRequired));
    EXPECT_EQ(writes.front().envelope.body,
              net::protocol::to_string(net::protocol::ErrorCode::kAuthRequired));
}

TEST(V2GatewayBridgeTest, RateLimitPolicyCanRejectRequest) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(
            adapter,
            nullptr,
            [](const v2::gateway::ClientEnvelope&) { return false; }));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 10;
    envelope.protocol_message_id = net::protocol::kEchoRequest;
    envelope.request_id = 102;
    envelope.body = "rate_limited";

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(writes.front().envelope.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kRateLimited));
    EXPECT_EQ(writes.front().envelope.body,
              net::protocol::to_string(net::protocol::ErrorCode::kRateLimited));
}

TEST(V2GatewayBridgeTest, EchoWithEmptyBodyReturnsEmptyEcho) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 11;
    envelope.protocol_message_id = net::protocol::kEchoRequest;
    envelope.request_id = 200;
    envelope.body.clear();

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(writes.front().envelope.body, "");
}

TEST(V2GatewayBridgeTest, EchoRequestPreservesRequestId) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    v2::gateway::ClientEnvelope envelope;
    envelope.session_id = 12;
    envelope.protocol_message_id = net::protocol::kEchoRequest;
    envelope.request_id = 0;
    envelope.body = "zero-reqid";

    const auto writes = adapter.handle_incoming(envelope);
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.request_id, 0U);
    EXPECT_EQ(writes.front().envelope.body, "zero-reqid");
}

TEST(V2GatewayBridgeTest, MultipleLoginReturnsSeparateResponses) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    const auto writes1 = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 20,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "user_a|token:a|Alice",
    });
    ASSERT_EQ(writes1.size(), 1U);
    EXPECT_EQ(writes1.front().envelope.body, "login_ok:user_a");

    const auto writes2 = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 21,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "user_b|token:b|Bob",
    });
    ASSERT_EQ(writes2.size(), 1U);
    EXPECT_EQ(writes2.front().envelope.body, "login_ok:user_b");
}

TEST(V2GatewayBridgeTest, VerifyLoginUsersCanEcho) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 30,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "user_echo|token:e|EchoUser",
    });

    const auto writes = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 30,
        .protocol_message_id = net::protocol::kEchoRequest,
        .request_id = 2,
        .body = "echo_after_login",
    });
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kEchoResponse);
    EXPECT_EQ(writes.front().envelope.body, "echo_after_login");
}

TEST(V2GatewayBridgeTest, HeartbeatReturnsResponse) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    auto gateway_actor = actor_system.create_actor(
        std::make_unique<v2::gateway::GatewayActor>(adapter));
    adapter.bind_gateway(gateway_actor);

    const auto writes = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 40,
        .protocol_message_id = net::protocol::kHeartbeatRequest,
        .request_id = 0,
        .body = {},
    });
    ASSERT_EQ(writes.size(), 1U);
    EXPECT_EQ(writes.front().envelope.protocol_message_id, net::protocol::kHeartbeatResponse);
}

TEST(V2GatewayBridgeTest, SessionKickOnDuplicateLogin) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    auto first_login = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 30,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "user_a|token:a|Alice",
    });
    ASSERT_EQ(first_login.size(), 1U);
    EXPECT_EQ(first_login[0].envelope.protocol_message_id, net::protocol::kLoginResponse);

    auto duplicate_login = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 31,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "user_a|token:a2|Alice2",
    });
    ASSERT_EQ(duplicate_login.size(), 2U);
    EXPECT_EQ(duplicate_login[0].envelope.protocol_message_id, net::protocol::kSessionKickedPush);
    EXPECT_EQ(duplicate_login[0].envelope.body, "duplicate_login");
    EXPECT_EQ(duplicate_login[1].envelope.protocol_message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(duplicate_login[1].envelope.body, "login_ok:user_a");
}

TEST(V2GatewayBridgeTest, SessionResumeWithRoomAndBattle) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 40,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "owner|token:o|Owner",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 41,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "member|token:m|Member",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 40,
        .protocol_message_id = net::protocol::kRoomCreateRequest,
        .request_id = 3,
        .body = "room_resume",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 41,
        .protocol_message_id = net::protocol::kRoomJoinRequest,
        .request_id = 4,
        .body = "room_resume",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 40,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 5,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 41,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 6,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 40,
        .protocol_message_id = net::protocol::kBattleStartRequest,
        .request_id = 7,
        .body = "room_resume",
    });

    runtime.on_session_closed(40);

    auto resume = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 42,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 8,
        .body = "owner|token:o2|Owner2",
    });
    ASSERT_GE(resume.size(), 2U);
    bool has_resume_push = false;
    for (const auto& write : resume) {
        if (write.envelope.protocol_message_id == net::protocol::kSessionResumedPush) {
            has_resume_push = true;
            EXPECT_NE(write.envelope.body.find("room_resume"), std::string::npos);
        }
    }
    EXPECT_TRUE(has_resume_push);
}

TEST(V2GatewayBridgeTest, ConcurrentSessionsDoNotCrossContaminate) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 50,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "user_a|token:a|UserA",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 51,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "user_b|token:b|UserB",
    });

    auto echo_a = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 50,
        .protocol_message_id = net::protocol::kEchoRequest,
        .request_id = 10,
        .body = "data-from-a",
    });
    ASSERT_EQ(echo_a.size(), 1U);
    EXPECT_EQ(echo_a[0].envelope.session_id, 50U);
    EXPECT_EQ(echo_a[0].envelope.body, "data-from-a");

    auto echo_b = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 51,
        .protocol_message_id = net::protocol::kEchoRequest,
        .request_id = 11,
        .body = "data-from-b",
    });
    ASSERT_EQ(echo_b.size(), 1U);
    EXPECT_EQ(echo_b[0].envelope.session_id, 51U);
    EXPECT_EQ(echo_b[0].envelope.body, "data-from-b");
}

TEST(V2GatewayBridgeTest, DisconnectDuringBattleEndsBattleAndResumeReturnsToRoom) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 60,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "owner|token:o|Owner",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 61,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "member|token:m|Member",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 60,
        .protocol_message_id = net::protocol::kRoomCreateRequest,
        .request_id = 3,
        .body = "room_battle",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 61,
        .protocol_message_id = net::protocol::kRoomJoinRequest,
        .request_id = 4,
        .body = "room_battle",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 60,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 5,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 61,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 6,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 60,
        .protocol_message_id = net::protocol::kBattleStartRequest,
        .request_id = 7,
        .body = "room_battle",
    });

    runtime.on_session_closed(60);

    auto resume = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 62,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 8,
        .body = "owner|token:o3|Owner3",
    });
    ASSERT_GE(resume.size(), 2U);
    bool has_resume = false;
    for (const auto& write : resume) {
        if (write.envelope.protocol_message_id == net::protocol::kSessionResumedPush) {
            has_resume = true;
            EXPECT_NE(write.envelope.body.find("room_battle"), std::string::npos);
        }
    }
    EXPECT_TRUE(has_resume);
}
