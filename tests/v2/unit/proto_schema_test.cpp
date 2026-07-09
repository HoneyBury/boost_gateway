// v3.0.0 Phase 12: Proto schema validation tests.
// Validates that the .proto definitions are complete and consistent.

#include <gtest/gtest.h>
#include <fstream>
#include <string>
#include <vector>

#include "v3/proto/envelope_codec.h"

namespace {

#ifndef PROJECT_SOURCE_DIR
#define PROJECT_SOURCE_DIR "."
#endif

std::string proto_path(const std::string& rel) {
    return std::string(PROJECT_SOURCE_DIR) + "/" + rel;
}

std::string read_file(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) return {};
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());
    return content;
}

bool proto_has_message(const std::string& content, const std::string& name) {
    return content.find("message " + name) != std::string::npos;
}

bool proto_has_field(const std::string& content, const std::string& field_name) {
    return content.find(field_name + " =") != std::string::npos;
}

}  // namespace

// ─── Proto file existence ────────────────────────────────────────────────

TEST(ProtoSchemaTest, AllProtoFilesExist) {
    std::vector<std::string> files = {
        "proto/v3/common.proto",
        "proto/v3/login.proto",
        "proto/v3/room.proto",
        "proto/v3/battle.proto",
        "proto/v3/match.proto",
        "proto/v3/leaderboard.proto",
    };
    for (const auto& f : files) {
        auto content = read_file(proto_path(f));
        EXPECT_FALSE(content.empty()) << "Missing proto file: " << f;
    }
}

// ─── Common.proto schema ─────────────────────────────────────────────────

TEST(ProtoSchemaTest, CommonProtoHasServiceEnvelope) {
    auto c = read_file(proto_path("proto/v3/common.proto"));
    EXPECT_TRUE(proto_has_message(c, "ServiceEnvelope"));
    EXPECT_TRUE(proto_has_field(c, "correlation_id"));
    EXPECT_TRUE(proto_has_field(c, "trace_id"));
    EXPECT_TRUE(proto_has_field(c, "span_id"));
    EXPECT_TRUE(proto_has_field(c, "login"));
    EXPECT_TRUE(proto_has_field(c, "room"));
    EXPECT_TRUE(proto_has_field(c, "battle"));
    EXPECT_TRUE(proto_has_field(c, "match"));
    EXPECT_TRUE(proto_has_field(c, "leaderboard"));
}

// ─── Login.proto schema ──────────────────────────────────────────────────

TEST(ProtoSchemaTest, LoginProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/login.proto"));
    EXPECT_TRUE(proto_has_message(c, "LoginRequest"));
    EXPECT_TRUE(proto_has_message(c, "LoginResponse"));
    EXPECT_TRUE(proto_has_message(c, "RegisterAccountRequest"));
    EXPECT_TRUE(proto_has_message(c, "RegisterAccountResponse"));
    EXPECT_TRUE(proto_has_message(c, "GuestLoginRequest"));
    EXPECT_TRUE(proto_has_message(c, "GuestLoginResponse"));
    EXPECT_TRUE(proto_has_message(c, "TokenValidateRequest"));
    EXPECT_TRUE(proto_has_message(c, "TokenValidateResponse"));
    EXPECT_TRUE(proto_has_message(c, "SessionBindResponse"));
    EXPECT_TRUE(proto_has_message(c, "SessionCloseResponse"));
    EXPECT_TRUE(proto_has_message(c, "TokenRefreshRequest"));
    EXPECT_TRUE(proto_has_message(c, "TokenRefreshResponse"));
    EXPECT_TRUE(proto_has_field(c, "user_id"));
    EXPECT_TRUE(proto_has_field(c, "token"));
    EXPECT_TRUE(proto_has_field(c, "credential"));
    EXPECT_TRUE(proto_has_field(c, "role"));
}

TEST(ProtoSchemaTest, CommonProtoCarriesExtendedLoginTransportFields) {
    auto c = read_file(proto_path("proto/v3/common.proto"));
    EXPECT_TRUE(proto_has_field(c, "register_account"));
    EXPECT_TRUE(proto_has_field(c, "register_account_response"));
    EXPECT_TRUE(proto_has_field(c, "guest_login"));
    EXPECT_TRUE(proto_has_field(c, "guest_login_response"));
}

// ─── Room.proto schema ───────────────────────────────────────────────────

TEST(ProtoSchemaTest, RoomProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/room.proto"));
    EXPECT_TRUE(proto_has_message(c, "RoomCreateRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomJoinRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomLeaveRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomLeaveResponse"));
    EXPECT_TRUE(proto_has_message(c, "RoomReadyRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomStartBattleResponse"));
    EXPECT_TRUE(proto_has_message(c, "RoomStatePush"));
    EXPECT_TRUE(proto_has_message(c, "RoomListRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomListResponse"));
    EXPECT_TRUE(proto_has_message(c, "RoomDetailRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomDetailResponse"));
    EXPECT_TRUE(proto_has_message(c, "RoomKickRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomKickResponse"));
    EXPECT_TRUE(proto_has_message(c, "RoomTransferOwnerRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomTransferOwnerResponse"));
    EXPECT_TRUE(proto_has_message(c, "RoomBattleFinishedRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomBattleFinishedResponse"));
}

// ─── Battle.proto schema ─────────────────────────────────────────────────

TEST(ProtoSchemaTest, BattleProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/battle.proto"));
    EXPECT_TRUE(proto_has_message(c, "BattleCreateRequest"));
    EXPECT_TRUE(proto_has_message(c, "BattleCreateResponse"));
    EXPECT_TRUE(proto_has_message(c, "BattleInputRequest"));
    EXPECT_TRUE(proto_has_message(c, "BattleStateRequest"));
    EXPECT_TRUE(proto_has_message(c, "BattleStateResponse"));
    EXPECT_TRUE(proto_has_message(c, "BattleStatePush"));
    EXPECT_TRUE(proto_has_message(c, "BattleFinishResponse"));
    EXPECT_TRUE(proto_has_message(c, "ReplayLoadRequest"));
    EXPECT_TRUE(proto_has_message(c, "ReplayLoadResponse"));
    EXPECT_TRUE(proto_has_field(c, "input_data"));
    EXPECT_TRUE(proto_has_field(c, "kind"));
    EXPECT_TRUE(proto_has_field(c, "frame_number"));
}

// ─── Match.proto schema ──────────────────────────────────────────────────

TEST(ProtoSchemaTest, MatchProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/match.proto"));
    EXPECT_TRUE(proto_has_message(c, "MatchJoinRequest"));
    EXPECT_TRUE(proto_has_message(c, "MatchFoundPush"));
    EXPECT_TRUE(proto_has_field(c, "mmr"));
    EXPECT_TRUE(proto_has_field(c, "mode"));
    EXPECT_TRUE(proto_has_field(c, "player_ids"));
}

// ─── Leaderboard.proto schema ────────────────────────────────────────────

TEST(ProtoSchemaTest, LeaderboardProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/leaderboard.proto"));
    EXPECT_TRUE(proto_has_message(c, "LeaderboardSubmitRequest"));
    EXPECT_TRUE(proto_has_message(c, "LeaderboardTopRequest"));
    EXPECT_TRUE(proto_has_message(c, "LeaderboardEntry"));
    EXPECT_TRUE(proto_has_field(c, "score"));
    EXPECT_TRUE(proto_has_field(c, "rank"));
}

// ─── Cross-file consistency ──────────────────────────────────────────────

TEST(ProtoSchemaTest, AllProtosUseProto3) {
    std::vector<std::string> files = {
        "proto/v3/common.proto", "proto/v3/login.proto",
        "proto/v3/room.proto", "proto/v3/battle.proto",
        "proto/v3/match.proto", "proto/v3/leaderboard.proto",
    };
    for (const auto& f : files) {
        auto c = read_file(proto_path(f));
        EXPECT_NE(c.find("syntax = \"proto3\""), std::string::npos)
            << f << " missing proto3 syntax";
        EXPECT_NE(c.find("package boost.gateway.v3"), std::string::npos)
            << f << " missing package declaration";
    }
}

TEST(ProtoSchemaTest, EnvelopeCodecRoundTripsMatchPayload) {
    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 7;
    meta.source_service = "gateway";
    meta.target_service = "match";
    meta.timeout_ms = 500;
    meta.trace_id = 1234;
    meta.span_id = 5678;

    const auto encoded = v3::proto::encode_envelope(
        meta,
        v3::proto::EnvelopeDomain::kMatch,
        v3::proto::EnvelopeMessageKind::kMatchJoinRequest,
        {{"user_id", "alice"}, {"mmr", 1000}, {"mode", "1v1"}});

    const auto decoded = v3::proto::decode_envelope(encoded);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->meta.correlation_id, 7U);
    EXPECT_EQ(decoded->domain, v3::proto::EnvelopeDomain::kMatch);
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kMatchJoinRequest);
    EXPECT_EQ(decoded->payload.value("user_id", ""), "alice");
    EXPECT_EQ(decoded->payload.value("mmr", 0), 1000);
}

TEST(ProtoSchemaTest, EnvelopeCodecRejectsMalformedPayload) {
    EXPECT_FALSE(v3::proto::decode_envelope("{bad json").has_value());
    EXPECT_FALSE(v3::proto::decode_envelope(R"({"payload":{}})").has_value());
}

TEST(ProtoSchemaTest, EnvelopeCodecSupportsLoginRoomAndBattleKinds) {
    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 9;
    meta.source_service = "gateway";

    const auto login = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kLoginRequest,
        {{"user_id", "alice"}, {"token", "t1"}, {"display_name", "Alice"}});
    const auto register_account = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kRegisterAccountRequest,
        {{"user_id", "alice"}, {"credential", "secret"}, {"display_name", "Alice"}});
    const auto room = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kRoomCreateRequest,
        {{"user_id", "alice"}, {"room_id", "room_01"}});
    const auto battle = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kBattleInputRequest,
        {{"user_id", "alice"}, {"input_data", "move:1,2"}, {"submitted_frame", 1}});

    auto login_decoded = v3::proto::decode_typed_envelope(login);
    auto register_decoded = v3::proto::decode_typed_envelope(register_account);
    auto room_decoded = v3::proto::decode_typed_envelope(room);
    auto battle_decoded = v3::proto::decode_typed_envelope(battle);

    ASSERT_TRUE(login_decoded.has_value());
    ASSERT_TRUE(register_decoded.has_value());
    ASSERT_TRUE(room_decoded.has_value());
    ASSERT_TRUE(battle_decoded.has_value());
    EXPECT_EQ(login_decoded->message_kind, v3::proto::EnvelopeMessageKind::kLoginRequest);
    EXPECT_EQ(register_decoded->message_kind,
              v3::proto::EnvelopeMessageKind::kRegisterAccountRequest);
    EXPECT_EQ(room_decoded->message_kind, v3::proto::EnvelopeMessageKind::kRoomCreateRequest);
    EXPECT_EQ(battle_decoded->message_kind, v3::proto::EnvelopeMessageKind::kBattleInputRequest);
}

TEST(ProtoSchemaTest, EveryTypedKindMapsToConcreteDomain) {
    const std::vector<v3::proto::EnvelopeMessageKind> kinds = {
        v3::proto::EnvelopeMessageKind::kLoginRequest,
        v3::proto::EnvelopeMessageKind::kLoginResponse,
        v3::proto::EnvelopeMessageKind::kRegisterAccountRequest,
        v3::proto::EnvelopeMessageKind::kRegisterAccountResponse,
        v3::proto::EnvelopeMessageKind::kGuestLoginRequest,
        v3::proto::EnvelopeMessageKind::kGuestLoginResponse,
        v3::proto::EnvelopeMessageKind::kTokenValidateRequest,
        v3::proto::EnvelopeMessageKind::kTokenValidateResponse,
        v3::proto::EnvelopeMessageKind::kSessionBindRequest,
        v3::proto::EnvelopeMessageKind::kSessionBindResponse,
        v3::proto::EnvelopeMessageKind::kSessionCloseRequest,
        v3::proto::EnvelopeMessageKind::kSessionCloseResponse,
        v3::proto::EnvelopeMessageKind::kTokenRefreshRequest,
        v3::proto::EnvelopeMessageKind::kTokenRefreshResponse,
        v3::proto::EnvelopeMessageKind::kRoomCreateRequest,
        v3::proto::EnvelopeMessageKind::kRoomCreateResponse,
        v3::proto::EnvelopeMessageKind::kRoomJoinRequest,
        v3::proto::EnvelopeMessageKind::kRoomJoinResponse,
        v3::proto::EnvelopeMessageKind::kRoomLeaveRequest,
        v3::proto::EnvelopeMessageKind::kRoomLeaveResponse,
        v3::proto::EnvelopeMessageKind::kRoomReadyRequest,
        v3::proto::EnvelopeMessageKind::kRoomReadyResponse,
        v3::proto::EnvelopeMessageKind::kRoomStartBattleRequest,
        v3::proto::EnvelopeMessageKind::kRoomStartBattleResponse,
        v3::proto::EnvelopeMessageKind::kRoomStatePush,
        v3::proto::EnvelopeMessageKind::kRoomListRequest,
        v3::proto::EnvelopeMessageKind::kRoomListResponse,
        v3::proto::EnvelopeMessageKind::kRoomDetailRequest,
        v3::proto::EnvelopeMessageKind::kRoomDetailResponse,
        v3::proto::EnvelopeMessageKind::kRoomKickRequest,
        v3::proto::EnvelopeMessageKind::kRoomKickResponse,
        v3::proto::EnvelopeMessageKind::kRoomTransferOwnerRequest,
        v3::proto::EnvelopeMessageKind::kRoomTransferOwnerResponse,
        v3::proto::EnvelopeMessageKind::kRoomBattleFinishedRequest,
        v3::proto::EnvelopeMessageKind::kRoomBattleFinishedResponse,
        v3::proto::EnvelopeMessageKind::kBattleCreateRequest,
        v3::proto::EnvelopeMessageKind::kBattleCreateResponse,
        v3::proto::EnvelopeMessageKind::kBattleInputRequest,
        v3::proto::EnvelopeMessageKind::kBattleInputResponse,
        v3::proto::EnvelopeMessageKind::kBattleStateRequest,
        v3::proto::EnvelopeMessageKind::kBattleStateResponse,
        v3::proto::EnvelopeMessageKind::kBattleStatePush,
        v3::proto::EnvelopeMessageKind::kBattleFinishRequest,
        v3::proto::EnvelopeMessageKind::kBattleFinishResponse,
        v3::proto::EnvelopeMessageKind::kReplayLoadRequest,
        v3::proto::EnvelopeMessageKind::kReplayLoadResponse,
        v3::proto::EnvelopeMessageKind::kMatchJoinRequest,
        v3::proto::EnvelopeMessageKind::kMatchJoinResponse,
        v3::proto::EnvelopeMessageKind::kMatchLeaveRequest,
        v3::proto::EnvelopeMessageKind::kMatchLeaveResponse,
        v3::proto::EnvelopeMessageKind::kMatchStatusRequest,
        v3::proto::EnvelopeMessageKind::kMatchStatusResponse,
        v3::proto::EnvelopeMessageKind::kLeaderboardSubmitRequest,
        v3::proto::EnvelopeMessageKind::kLeaderboardSubmitResponse,
        v3::proto::EnvelopeMessageKind::kLeaderboardTopRequest,
        v3::proto::EnvelopeMessageKind::kLeaderboardTopResponse,
        v3::proto::EnvelopeMessageKind::kLeaderboardRankRequest,
        v3::proto::EnvelopeMessageKind::kLeaderboardRankResponse,
    };

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 11;
    for (const auto kind : kinds) {
        const auto encoded = v3::proto::encode_typed_envelope(meta, kind, {{"ok", true}});
        ASSERT_FALSE(encoded.empty()) << "kind did not encode: " << static_cast<int>(kind);
        const auto decoded = v3::proto::decode_typed_envelope(encoded);
        ASSERT_TRUE(decoded.has_value()) << encoded;
        EXPECT_EQ(decoded->message_kind, kind);
    }
}

TEST(ProtoSchemaTest, MaybeWrapTypedResponsePreservesLegacyRawJsonCompatibility) {
    const nlohmann::json payload{{"status", "ok"}};

    const auto legacy = v3::proto::maybe_wrap_typed_response(
        std::nullopt,
        v3::proto::EnvelopeMessageKind::kLoginResponse,
        payload);
    EXPECT_EQ(nlohmann::json::parse(legacy).value("status", ""), "ok");

    v3::proto::EnvelopeMeta meta;
    meta.correlation_id = 22;
    meta.trace_id = 33;
    const auto request = v3::proto::decode_typed_envelope(
        v3::proto::encode_typed_envelope(
            meta,
            v3::proto::EnvelopeMessageKind::kLoginRequest,
            {{"user_id", "alice"}}));
    ASSERT_TRUE(request.has_value());

    const auto wrapped = v3::proto::maybe_wrap_typed_response(
        request,
        v3::proto::EnvelopeMessageKind::kLoginResponse,
        payload,
        1001);
    const auto decoded = v3::proto::decode_typed_envelope(wrapped);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->meta.correlation_id, 22U);
    EXPECT_EQ(decoded->meta.trace_id, 33U);
    EXPECT_EQ(decoded->meta.error_code, 1001);
    EXPECT_EQ(decoded->message_kind, v3::proto::EnvelopeMessageKind::kLoginResponse);
    EXPECT_EQ(decoded->payload.value("status", ""), "ok");
}
