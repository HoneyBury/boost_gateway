#include <gtest/gtest.h>

#include "net/protocol.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

TEST(V2ConnectedFlowTest, LoginCreateJoinReadyStartAndInputFlowThroughActors) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    auto login_owner = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "owner|token:owner|Owner",
    });
    ASSERT_EQ(login_owner.size(), 1U);
    EXPECT_EQ(login_owner.front().envelope.protocol_message_id, net::protocol::kLoginResponse);

    auto login_member = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "member|token:member|Member",
    });
    ASSERT_EQ(login_member.size(), 1U);
    EXPECT_EQ(login_member.front().envelope.protocol_message_id, net::protocol::kLoginResponse);

    auto create_room = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomCreateRequest,
        .request_id = 3,
        .body = "room_alpha",
    });
    ASSERT_EQ(create_room.size(), 1U);
    EXPECT_EQ(create_room.front().envelope.protocol_message_id, net::protocol::kRoomCreateResponse);

    auto join_room = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomJoinRequest,
        .request_id = 4,
        .body = "room_alpha",
    });
    ASSERT_EQ(join_room.size(), 1U);
    EXPECT_EQ(join_room.front().envelope.protocol_message_id, net::protocol::kRoomJoinResponse);

    auto owner_ready = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 5,
        .body = "true",
    });
    ASSERT_EQ(owner_ready.size(), 1U);
    EXPECT_EQ(owner_ready.front().envelope.protocol_message_id, net::protocol::kRoomReadyResponse);

    auto member_ready = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 6,
        .body = "true",
    });
    ASSERT_EQ(member_ready.size(), 1U);
    EXPECT_EQ(member_ready.front().envelope.protocol_message_id, net::protocol::kRoomReadyResponse);

    auto battle_start = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleStartRequest,
        .request_id = 7,
        .body = "room_alpha",
    });
    ASSERT_EQ(battle_start.size(), 3U);
    EXPECT_EQ(battle_start.front().envelope.protocol_message_id, net::protocol::kBattleStartResponse);
    EXPECT_EQ(battle_start.front().envelope.body,
              "battle_started:room_id=room_alpha:battle_id=battle_0001");
    EXPECT_EQ(battle_start[1].envelope.protocol_message_id, net::protocol::kBattleStatePush);
    EXPECT_EQ(battle_start[2].envelope.protocol_message_id, net::protocol::kBattleStatePush);

    auto battle_input = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 8,
        .body = "attack:target",
    });
    ASSERT_EQ(battle_input.size(), 4U);
    EXPECT_EQ(battle_input[0].envelope.protocol_message_id, net::protocol::kBattleInputResponse);
    EXPECT_EQ(battle_input[0].envelope.body, "input_seq:seq=1");
    EXPECT_EQ(battle_input[1].envelope.protocol_message_id, net::protocol::kBattleInputPush);
    EXPECT_EQ(battle_input[1].envelope.session_id, 200U);
    EXPECT_EQ(battle_input[1].envelope.body, "battle_input:user_id=owner:seq=1:input=attack:target");
    EXPECT_EQ(battle_input[2].envelope.protocol_message_id, net::protocol::kBattleStatePush);
    EXPECT_EQ(battle_input[2].envelope.body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=1:trigger=input:owner:1");
    EXPECT_EQ(battle_input[3].envelope.protocol_message_id, net::protocol::kBattleStatePush);

    auto second_input = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 9,
        .body = "attack:other",
    });
    ASSERT_EQ(second_input.size(), 4U);
    EXPECT_EQ(second_input[0].envelope.body, "input_seq:seq=2");
    EXPECT_EQ(second_input[1].envelope.body, "battle_input:user_id=owner:seq=2:input=attack:other");
    EXPECT_EQ(second_input[2].envelope.body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=2:trigger=input:owner:2");

    auto third_input = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 10,
        .body = "attack:someone",
    });
    ASSERT_EQ(third_input.size(), 8U);
    EXPECT_EQ(third_input[0].envelope.body, "input_seq:seq=3");
    EXPECT_EQ(third_input[1].envelope.body, "battle_input:user_id=owner:seq=3:input=attack:someone");
    EXPECT_EQ(third_input[2].envelope.body,
              "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=3:trigger=input:owner:3");
    EXPECT_EQ(third_input[4].envelope.protocol_message_id, net::protocol::kBattleStatePush);
    EXPECT_EQ(third_input[4].envelope.body,
              "battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=frame_limit_reached:user_id=input:owner:3");
    EXPECT_EQ(third_input[6].envelope.protocol_message_id, net::protocol::kBattleStatePush);
    EXPECT_EQ(third_input[6].envelope.body,
              "battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=frame_limit_reached:user_id=input:owner:3");
}

TEST(V2ConnectedFlowTest, BattleCanFinishByRequestedReason) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "owner|token:owner|Owner",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "member|token:member|Member",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomCreateRequest,
        .request_id = 3,
        .body = "room_alpha",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomJoinRequest,
        .request_id = 4,
        .body = "room_alpha",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 5,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 6,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleStartRequest,
        .request_id = 7,
        .body = "room_alpha",
    });

    auto finish = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 8,
        .body = "finish:surrender",
    });
    ASSERT_EQ(finish.size(), 5U);
    EXPECT_EQ(finish[0].envelope.protocol_message_id, net::protocol::kBattleStatePush);
    EXPECT_EQ(finish[0].envelope.body,
              "battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(finish[2].envelope.protocol_message_id, net::protocol::kBattleInputResponse);
    EXPECT_EQ(finish[2].envelope.body, "battle_end_accepted:surrender");
    EXPECT_EQ(finish[3].envelope.protocol_message_id, net::protocol::kBattleStatePush);
    EXPECT_EQ(finish[3].envelope.body,
              "battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=surrender:user_id=owner");

    auto after_finish = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 9,
        .body = "move:9,9",
    });
    ASSERT_EQ(after_finish.size(), 1U);
    EXPECT_EQ(after_finish[0].envelope.protocol_message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(after_finish[0].envelope.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted));
}

TEST(V2ConnectedFlowTest, RejectsInvalidBattleStartAndInputBodies) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    const auto gateway_actor = runtime.create_gateway_actor();
    adapter.bind_gateway(gateway_actor);

    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "owner|token:owner|Owner",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "member|token:member|Member",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomCreateRequest,
        .request_id = 3,
        .body = "room_alpha",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomJoinRequest,
        .request_id = 4,
        .body = "room_alpha",
    });

    const auto invalid_start = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleStartRequest,
        .request_id = 5,
        .body = "room_other",
    });
    ASSERT_EQ(invalid_start.size(), 1U);
    EXPECT_EQ(invalid_start[0].envelope.protocol_message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(invalid_start[0].envelope.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidRoomId));

    const auto invalid_input = adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 6,
        .body = "",
    });
    ASSERT_EQ(invalid_input.size(), 1U);
    EXPECT_EQ(invalid_input[0].envelope.protocol_message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(invalid_input[0].envelope.body, "invalid_battle_input");
}
