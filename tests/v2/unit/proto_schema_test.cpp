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
    EXPECT_TRUE(proto_has_message(c, "TokenValidateRequest"));
    EXPECT_TRUE(proto_has_field(c, "user_id"));
    EXPECT_TRUE(proto_has_field(c, "token"));
    EXPECT_TRUE(proto_has_field(c, "role"));
}

// ─── Room.proto schema ───────────────────────────────────────────────────

TEST(ProtoSchemaTest, RoomProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/room.proto"));
    EXPECT_TRUE(proto_has_message(c, "RoomCreateRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomJoinRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomLeaveRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomReadyRequest"));
    EXPECT_TRUE(proto_has_message(c, "RoomStatePush"));
}

// ─── Battle.proto schema ─────────────────────────────────────────────────

TEST(ProtoSchemaTest, BattleProtoHasAllMessages) {
    auto c = read_file(proto_path("proto/v3/battle.proto"));
    EXPECT_TRUE(proto_has_message(c, "BattleCreateRequest"));
    EXPECT_TRUE(proto_has_message(c, "BattleInputRequest"));
    EXPECT_TRUE(proto_has_message(c, "BattleStatePush"));
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
    const auto room = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kRoomCreateRequest,
        {{"user_id", "alice"}, {"room_id", "room_01"}});
    const auto battle = v3::proto::encode_typed_envelope(
        meta,
        v3::proto::EnvelopeMessageKind::kBattleInputRequest,
        {{"user_id", "alice"}, {"input_data", "move:1,2"}, {"submitted_frame", 1}});

    auto login_decoded = v3::proto::decode_typed_envelope(login);
    auto room_decoded = v3::proto::decode_typed_envelope(room);
    auto battle_decoded = v3::proto::decode_typed_envelope(battle);

    ASSERT_TRUE(login_decoded.has_value());
    ASSERT_TRUE(room_decoded.has_value());
    ASSERT_TRUE(battle_decoded.has_value());
    EXPECT_EQ(login_decoded->message_kind, v3::proto::EnvelopeMessageKind::kLoginRequest);
    EXPECT_EQ(room_decoded->message_kind, v3::proto::EnvelopeMessageKind::kRoomCreateRequest);
    EXPECT_EQ(battle_decoded->message_kind, v3::proto::EnvelopeMessageKind::kBattleInputRequest);
}
