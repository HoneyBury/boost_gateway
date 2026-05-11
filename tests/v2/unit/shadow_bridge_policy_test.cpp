#include "app/config.h"
#include "app/logging.h"
#include "net/protocol.h"
#include "v2/gateway/gateway_server_bridge.h"

#include <nlohmann/json.hpp>

#include <filesystem>
#include <fstream>

#include <gtest/gtest.h>

TEST(V2ShadowBridgePolicyTest, MirrorsConfiguredProtocolDomainsOnly) {
    v2::gateway::GatewayServerShadowBridge::MirrorPolicy policy;
    policy.login = true;
    policy.room = false;
    policy.battle = true;
    policy.echo = false;

    v2::gateway::GatewayServerShadowBridge bridge(policy, {}, false);

    EXPECT_TRUE(bridge.should_forward(net::protocol::kLoginRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kRoomCreateRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kRoomJoinRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kRoomReadyRequest));
    EXPECT_TRUE(bridge.should_forward(net::protocol::kBattleStartRequest));
    EXPECT_TRUE(bridge.should_forward(net::protocol::kBattleInputRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kEchoRequest));
    EXPECT_FALSE(bridge.should_forward(net::protocol::kHeartbeatRequest));
}

TEST(V2ShadowBridgePolicyTest, EmitsConfiguredBattleResponseKindsOnly) {
    v2::gateway::GatewayServerShadowBridge::EmitPolicy emit_policy;
    emit_policy.battle_input_push = false;
    emit_policy.battle_state_started = true;
    emit_policy.battle_state_frame = false;
    emit_policy.battle_state_settlement = true;
    emit_policy.battle_state_finished = false;

    v2::gateway::GatewayServerShadowBridge bridge({}, emit_policy, true);
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleInputPush, "battle_input:user_id=owner:seq=1:input=move"));
    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleStatePush,
                                   "battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=1:trigger=input"));
    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleStatePush,
                                   "battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=surrender:user_id=owner"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=surrender:user_id=owner"));
}

TEST(V2ShadowBridgePolicyTest, EmitPolicyCanIndependentlyGateEveryBattleStateKind) {
    v2::gateway::GatewayServerShadowBridge::EmitPolicy emit_policy(
        true, false, true, false, true);
    v2::gateway::GatewayServerShadowBridge bridge({}, emit_policy, true);

    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleInputPush,
                                   "battle_input:user_id=owner:seq=3:input=move"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001"));
    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleStatePush,
                                   "battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=3:trigger=input"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=timeout:user_id=owner"));
    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleStatePush,
                                   "battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=timeout:user_id=owner"));
}

TEST(V2ShadowBridgePolicyTest, BuildsMirrorPolicyFromGatewayConfig) {
    app::config::GatewayAppConfig config;
    config.v2_shadow_bridge_login = false;
    config.v2_shadow_bridge_room = true;
    config.v2_shadow_bridge_battle = false;
    config.v2_shadow_bridge_echo = true;

    const auto policy = v2::gateway::make_shadow_bridge_policy(config);
    EXPECT_FALSE(policy.login);
    EXPECT_TRUE(policy.room);
    EXPECT_FALSE(policy.battle);
    EXPECT_TRUE(policy.echo);
}

TEST(V2ShadowBridgePolicyTest, BuildsEmitPolicyFromGatewayConfig) {
    app::config::GatewayAppConfig config;
    config.v2_shadow_bridge_emit_battle_input_push = false;
    config.v2_shadow_bridge_emit_battle_state_started = true;
    config.v2_shadow_bridge_emit_battle_state_frame = false;
    config.v2_shadow_bridge_emit_battle_state_settlement = true;
    config.v2_shadow_bridge_emit_battle_state_finished = false;

    const auto policy = v2::gateway::make_shadow_bridge_emit_policy(config);
    EXPECT_FALSE(policy.battle_input_push);
    EXPECT_TRUE(policy.battle_state_started);
    EXPECT_FALSE(policy.battle_state_frame);
    EXPECT_TRUE(policy.battle_state_settlement);
    EXPECT_FALSE(policy.battle_state_finished);
}

TEST(V2ShadowBridgePolicyTest, LoadedGatewayConfigDrivesMirrorPolicy) {
    app::logging::init("project_tests");

    const auto path = std::filesystem::temp_directory_path() / "v2_shadow_bridge_policy.json";
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"gateway\": {\n";
        output << "    \"v2_shadow_bridge_enabled\": true,\n";
        output << "    \"v2_shadow_bridge_login\": false,\n";
        output << "    \"v2_shadow_bridge_room\": true,\n";
        output << "    \"v2_shadow_bridge_battle\": false,\n";
        output << "    \"v2_shadow_bridge_echo\": true\n";
        output << "  }\n";
        output << "}\n";
    }

    const auto config = app::config::load_gateway_config(path);
    const auto policy = v2::gateway::make_shadow_bridge_policy(config);
    EXPECT_FALSE(policy.login);
    EXPECT_TRUE(policy.room);
    EXPECT_FALSE(policy.battle);
    EXPECT_TRUE(policy.echo);

    std::filesystem::remove(path);
}

TEST(V2ShadowBridgePolicyTest, ForwardUnknownMessageReturnsFalse) {
    v2::gateway::GatewayServerShadowBridge bridge({}, {}, false);
    EXPECT_FALSE(bridge.should_forward(0));
    EXPECT_FALSE(bridge.should_forward(9999));
}

TEST(V2ShadowBridgePolicyTest, ShouldEmitReturnsTrueForNonBattleMessages) {
    v2::gateway::GatewayServerShadowBridge bridge({}, {}, false);
    EXPECT_TRUE(bridge.should_emit(net::protocol::kLoginResponse, "login_ok:user"));
    EXPECT_TRUE(bridge.should_emit(net::protocol::kRoomCreateResponse, "room_alpha"));
    EXPECT_TRUE(bridge.should_emit(net::protocol::kRoomJoinResponse, "room_beta"));
}

TEST(V2ShadowBridgePolicyTest, ShouldEmitBattleStateWithUnknownKindReturnsTrue) {
    v2::gateway::GatewayServerShadowBridge bridge({}, {}, false);
    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleStatePush,
                                   "battle_state:kind=unknown_kind:room_id=r:battle_id=b"));
}

TEST(V2ShadowBridgePolicyTest, ShouldEmitBattleStateWithUnparseableBodyReturnsTrue) {
    v2::gateway::GatewayServerShadowBridge bridge({}, {}, false);
    EXPECT_TRUE(bridge.should_emit(net::protocol::kBattleStatePush, "not:battle_state"));
}

TEST(V2ShadowBridgePolicyTest, EmitPolicyAllBitsCanBeToggledIndependently) {
    v2::gateway::GatewayServerShadowBridge::EmitPolicy all_off(false, false, false, false, false);
    v2::gateway::GatewayServerShadowBridge bridge({}, all_off, true);

    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleInputPush,
                                    "battle_input:user_id=u:seq=1:input=data"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=started:room_id=r:battle_id=b"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=frame:room_id=r:battle_id=b:frame=1:trigger=input"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=settlement:room_id=r:battle_id=b:reason=surrender:user_id=u"));
    EXPECT_FALSE(bridge.should_emit(net::protocol::kBattleStatePush,
                                    "battle_state:kind=finished:room_id=r:battle_id=b:reason=surrender:user_id=u"));
}

TEST(V2ShadowBridgePolicyTest, DiagnosticsJsonReportsPoliciesAndDispatchStats) {
    v2::gateway::GatewayServerShadowBridge bridge(
        v2::gateway::GatewayServerShadowBridge::MirrorPolicy(false, true, false, true),
        v2::gateway::GatewayServerShadowBridge::EmitPolicy(true, false, true, false, true),
        true);

    const auto diagnostics = nlohmann::json::parse(bridge.diagnostics_json());
    EXPECT_EQ(diagnostics["emit_responses"], true);
    EXPECT_EQ(diagnostics["mirror_policy"]["login"], false);
    EXPECT_EQ(diagnostics["mirror_policy"]["room"], true);
    EXPECT_EQ(diagnostics["mirror_policy"]["battle"], false);
    EXPECT_EQ(diagnostics["mirror_policy"]["echo"], true);
    EXPECT_EQ(diagnostics["emit_policy"]["battle_input_push"], true);
    EXPECT_EQ(diagnostics["emit_policy"]["battle_state_started"], false);
    EXPECT_EQ(diagnostics["emit_policy"]["battle_state_frame"], true);
    EXPECT_EQ(diagnostics["emit_policy"]["battle_state_settlement"], false);
    EXPECT_EQ(diagnostics["emit_policy"]["battle_state_finished"], true);
    EXPECT_EQ(diagnostics["dispatch_stats"]["mirrored_packets"], 0);
    EXPECT_EQ(diagnostics["dispatch_stats"]["emitted_writes"], 0);
    EXPECT_EQ(diagnostics["dispatch_stats"]["scheduled_writes"], 0);
    EXPECT_EQ(diagnostics["dispatch_stats"]["inline_writes"], 0);
    EXPECT_EQ(diagnostics["tracked_sessions"], 0);
    EXPECT_EQ(diagnostics["active_sessions"], 0);
}
