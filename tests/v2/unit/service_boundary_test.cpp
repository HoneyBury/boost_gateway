#include <gtest/gtest.h>

#include <nlohmann/json.hpp>

#include "v2/service/backend_envelope.h"
#include "v2/service/envelope_adapter.h"
#include "v2/service/error_codes.h"
#include "v2/service/service_id.h"
#include "v2/service/service_manifest.h"
#include "v3/proto/envelope_codec.h"

// ─── ServiceId ─────────────────────────────────────────────────

TEST(V2ServiceBoundaryTest, ServiceIdToString) {
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceId::kGateway), "gateway");
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceId::kLogin), "login");
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceId::kRoom), "room");
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceId::kBattle), "battle");
}

// ─── MessageKind ───────────────────────────────────────────────

TEST(V2ServiceBoundaryTest, MessageKindToString) {
    EXPECT_STREQ(v2::service::to_string(v2::service::MessageKind::kRequest), "request");
    EXPECT_STREQ(v2::service::to_string(v2::service::MessageKind::kResponse), "response");
    EXPECT_STREQ(v2::service::to_string(v2::service::MessageKind::kPush), "push");
    EXPECT_STREQ(v2::service::to_string(v2::service::MessageKind::kError), "error");
}

// ─── BackendEnvelope: JSON Round-Trip ──────────────────────────

TEST(V2ServiceBoundaryTest, BackendEnvelopeJsonRoundTrip) {
    v2::service::BackendEnvelope original{
        .correlation_id = 42,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .timeout_ms = 5000,
        .error_code = 0,
        .payload = R"({"user_id":"alice","token":"xyz"})",
    };

    const auto json = v2::service::to_json(original);
    const auto parsed = v2::service::from_json(json);

    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->correlation_id, 42U);
    EXPECT_EQ(parsed->source_service, v2::service::ServiceId::kGateway);
    EXPECT_EQ(parsed->target_service, v2::service::ServiceId::kLogin);
    EXPECT_EQ(parsed->kind, v2::service::MessageKind::kRequest);
    EXPECT_EQ(parsed->timeout_ms, 5000U);
    EXPECT_EQ(parsed->payload, R"({"user_id":"alice","token":"xyz"})");
}

TEST(V2ServiceBoundaryTest, BackendEnvelopeMessageTypeRoundTrip) {
    v2::service::BackendEnvelope original{
        .correlation_id = 100,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"user_id":"bob"})",
        .message_type = "login_request",
    };

    const auto json = v2::service::to_json(original);
    const auto parsed = v2::service::from_json(json);

    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->message_type, "login_request");
}

TEST(V2ServiceBoundaryTest, FromJsonMessageTypeDefaultsToEmpty) {
    // JSON without message_type (old format) still parses, message_type is ""
    const auto parsed = v2::service::from_json(
        R"({"correlation_id":1,"source_service":"gateway","target_service":"login","kind":"request","payload":"{}"})"
    );
    ASSERT_TRUE(parsed.has_value());
    EXPECT_TRUE(parsed->message_type.empty());
}

TEST(V2ServiceBoundaryTest, BackendEnvelopeResponseRoundTrip) {
    v2::service::BackendEnvelope original{
        .correlation_id = 99,
        .source_service = v2::service::ServiceId::kLogin,
        .target_service = v2::service::ServiceId::kGateway,
        .kind = v2::service::MessageKind::kResponse,
        .timeout_ms = 0,
        .payload = R"({"status":"ok"})",
    };

    const auto json = v2::service::to_json(original);
    const auto parsed = v2::service::from_json(json);

    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->kind, v2::service::MessageKind::kResponse);
    EXPECT_EQ(parsed->source_service, v2::service::ServiceId::kLogin);
    EXPECT_EQ(parsed->target_service, v2::service::ServiceId::kGateway);
}

TEST(V2ServiceBoundaryTest, BackendEnvelopeSupportsMatchmakingAndLeaderboardServices) {
    v2::service::BackendEnvelope original{
        .correlation_id = 101,
        .source_service = v2::service::ServiceId::kMatchmaking,
        .target_service = v2::service::ServiceId::kLeaderboard,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"user_id":"alice"})",
        .message_type = "leaderboard_rank",
    };

    const auto json = v2::service::to_json(original);
    const auto parsed = v2::service::from_json(json);

    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->source_service, v2::service::ServiceId::kMatchmaking);
    EXPECT_EQ(parsed->target_service, v2::service::ServiceId::kLeaderboard);
    EXPECT_EQ(parsed->message_type, "leaderboard_rank");
}

TEST(V2ServiceBoundaryTest, BackendEnvelopeErrorRoundTrip) {
    v2::service::BackendEnvelope original{
        .correlation_id = 77,
        .source_service = v2::service::ServiceId::kBattle,
        .target_service = v2::service::ServiceId::kGateway,
        .kind = v2::service::MessageKind::kError,
        .timeout_ms = 0,
        .error_code = -1003,
        .payload = "",
    };

    const auto json = v2::service::to_json(original);
    const auto parsed = v2::service::from_json(json);

    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->kind, v2::service::MessageKind::kError);
    EXPECT_EQ(parsed->error_code, -1003);
}

TEST(V2ServiceBoundaryTest, BackendEnvelopePushRoundTrip) {
    v2::service::BackendEnvelope original{
        .correlation_id = 1,
        .source_service = v2::service::ServiceId::kRoom,
        .target_service = v2::service::ServiceId::kGateway,
        .kind = v2::service::MessageKind::kPush,
        .payload = R"({"event":"player_joined"})",
    };

    const auto json = v2::service::to_json(original);
    const auto parsed = v2::service::from_json(json);

    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->kind, v2::service::MessageKind::kPush);
}

// ─── BackendEnvelope: Validation ────────────────────────────────

TEST(V2ServiceBoundaryTest, BackendEnvelopeToTypedEnvelopeCopiesMetaPayloadAndKind) {
    v2::service::BackendEnvelope backend{
        .correlation_id = 123,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kMatchmaking,
        .kind = v2::service::MessageKind::kRequest,
        .timeout_ms = 250,
        .error_code = 0,
        .payload = R"({"user_id":"alice","mmr":1200,"mode":"1v1"})",
        .message_type = "match_join",
        .trace_id = 456,
        .span_id = 789,
    };

    const auto typed = v2::service::to_typed_envelope(backend);

    ASSERT_TRUE(typed.has_value());
    EXPECT_EQ(typed->meta.correlation_id, 123U);
    EXPECT_EQ(typed->meta.source_service, "gateway");
    EXPECT_EQ(typed->meta.target_service, "matchmaking");
    EXPECT_EQ(typed->meta.timeout_ms, 250U);
    EXPECT_EQ(typed->meta.trace_id, 456U);
    EXPECT_EQ(typed->meta.span_id, 789U);
    EXPECT_EQ(typed->message_kind, v3::proto::EnvelopeMessageKind::kMatchJoinRequest);
    EXPECT_EQ(typed->payload.value("user_id", std::string{}), "alice");
}

TEST(V2ServiceBoundaryTest, TypedEnvelopeToBackendEnvelopePreservesMetaAndPayload) {
    v3::proto::TypedEnvelope typed{
        .meta = {
            .correlation_id = 321,
            .source_service = "leaderboard",
            .target_service = "gateway",
            .timeout_ms = 0,
            .error_code = -2004,
            .trace_id = 654,
            .span_id = 987,
        },
        .message_kind = v3::proto::EnvelopeMessageKind::kLeaderboardRankResponse,
        .payload = {{"rank", 7}, {"user_id", "bob"}},
    };

    const auto backend = v2::service::to_backend_envelope(
        typed,
        v2::service::MessageKind::kError);

    EXPECT_EQ(backend.correlation_id, 321U);
    EXPECT_EQ(backend.source_service, v2::service::ServiceId::kLeaderboard);
    EXPECT_EQ(backend.target_service, v2::service::ServiceId::kGateway);
    EXPECT_EQ(backend.kind, v2::service::MessageKind::kError);
    EXPECT_EQ(backend.error_code, -2004);
    EXPECT_EQ(backend.message_type, "leaderboard_rank_response");
    EXPECT_EQ(backend.trace_id, 654U);
    EXPECT_EQ(backend.span_id, 987U);

    const auto payload = nlohmann::json::parse(backend.payload);
    EXPECT_EQ(payload.value("rank", 0), 7);
    EXPECT_EQ(payload.value("user_id", std::string{}), "bob");
}

TEST(V2ServiceBoundaryTest, EnvelopeAdapterRejectsUnknownMessageType) {
    v2::service::BackendEnvelope backend{
        .correlation_id = 1,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = "{}",
        .message_type = "unknown_message_type",
    };

    EXPECT_FALSE(v2::service::to_typed_envelope(backend).has_value());
}

TEST(V2ServiceBoundaryTest, EnvelopeAdapterSupportsNewTypedKinds) {
    v2::service::BackendEnvelope backend{
        .correlation_id = 1,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kRoom,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"user_id":"alice","room_id":"room_1"})",
        .message_type = "room_leave",
    };
    const auto typed = v2::service::to_typed_envelope(backend);
    ASSERT_TRUE(typed.has_value());
    EXPECT_EQ(typed->message_kind, v3::proto::EnvelopeMessageKind::kRoomLeaveRequest);

    v3::proto::TypedEnvelope response{
        .meta = {
            .correlation_id = 1,
            .source_service = "room",
            .target_service = "gateway",
        },
        .message_kind = v3::proto::EnvelopeMessageKind::kRoomLeaveResponse,
        .payload = {{"room_id", "room_1"}, {"was_owner", true}, {"new_owner_id", "bob"}},
    };
    const auto backend_response = v2::service::to_backend_envelope(response, v2::service::MessageKind::kResponse);
    EXPECT_EQ(backend_response.message_type, "room_leave_response");
}

TEST(V2ServiceBoundaryTest, EnvelopeAdapterSupportsTokenRefreshAndReplayLoadKinds) {
    v2::service::BackendEnvelope refresh_request{
        .correlation_id = 2,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"user_id":"alice","token":"token:alice"})",
        .message_type = "token_refresh",
    };
    const auto typed_refresh = v2::service::to_typed_envelope(refresh_request);
    ASSERT_TRUE(typed_refresh.has_value());
    EXPECT_EQ(typed_refresh->message_kind, v3::proto::EnvelopeMessageKind::kTokenRefreshRequest);

    v3::proto::TypedEnvelope replay_response{
        .meta = {
            .correlation_id = 3,
            .source_service = "battle",
            .target_service = "gateway",
        },
        .message_kind = v3::proto::EnvelopeMessageKind::kReplayLoadResponse,
        .payload = {{"battle_id", "battle_1"}, {"replay", nlohmann::json::object()}},
    };
    const auto backend_response = v2::service::to_backend_envelope(replay_response, v2::service::MessageKind::kResponse);
    EXPECT_EQ(backend_response.message_type, "replay_load_response");
}

TEST(V2ServiceBoundaryTest, EnvelopeAdapterSupportsRegisterAndGuestLoginKinds) {
    v2::service::BackendEnvelope register_request{
        .correlation_id = 6,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"user_id":"alice","credential":"secret","display_name":"Alice"})",
        .message_type = "register_account",
    };
    const auto typed_register = v2::service::to_typed_envelope(register_request);
    ASSERT_TRUE(typed_register.has_value());
    EXPECT_EQ(typed_register->message_kind,
              v3::proto::EnvelopeMessageKind::kRegisterAccountRequest);

    v3::proto::TypedEnvelope guest_response{
        .meta = {
            .correlation_id = 7,
            .source_service = "login",
            .target_service = "gateway",
        },
        .message_kind = v3::proto::EnvelopeMessageKind::kGuestLoginResponse,
        .payload = {
            {"status", "ok"},
            {"user_id", "guest_001"},
            {"display_name", "Guest_001"},
            {"token", "guest_token:guest_001"},
        },
    };
    const auto backend_response = v2::service::to_backend_envelope(
        guest_response,
        v2::service::MessageKind::kResponse);
    EXPECT_EQ(backend_response.message_type, "guest_login_response");
}

TEST(V2ServiceBoundaryTest, EnvelopeAdapterSupportsRoomGovernanceKinds) {
    v2::service::BackendEnvelope list_request{
        .correlation_id = 4,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kRoom,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"visibility":"public","page":1,"page_size":20})",
        .message_type = "room_list",
    };
    const auto typed_list = v2::service::to_typed_envelope(list_request);
    ASSERT_TRUE(typed_list.has_value());
    EXPECT_EQ(typed_list->message_kind, v3::proto::EnvelopeMessageKind::kRoomListRequest);

    v3::proto::TypedEnvelope kick_response{
        .meta = {
            .correlation_id = 5,
            .source_service = "room",
            .target_service = "gateway",
        },
        .message_kind = v3::proto::EnvelopeMessageKind::kRoomKickResponse,
        .payload = {{"room_id", "room_1"}, {"kicked_user_id", "bob"}, {"member_count", 1}},
    };
    const auto backend_response = v2::service::to_backend_envelope(kick_response, v2::service::MessageKind::kResponse);
    EXPECT_EQ(backend_response.message_type, "room_kick_response");
}

TEST(V2ServiceBoundaryTest, EnvelopeAdapterRejectsMalformedJsonPayload) {
    v2::service::BackendEnvelope backend{
        .correlation_id = 1,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = "{bad json",
        .message_type = "login_request",
    };

    EXPECT_FALSE(v2::service::to_typed_envelope(backend).has_value());
}

TEST(V2ServiceBoundaryTest, DecodeHandlerPayloadExtractsTypedPayload) {
    v3::proto::EnvelopeMeta meta{
        .correlation_id = 77,
        .source_service = "gateway",
        .target_service = "login",
        .trace_id = 88,
        .span_id = 99,
    };
    v2::service::BackendEnvelope request{
        .correlation_id = 77,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = v3::proto::encode_typed_envelope(
            meta,
            v3::proto::EnvelopeMessageKind::kLoginRequest,
            {{"user_id", "alice"}, {"token", "token:alice"}}),
        .message_type = "login_request",
    };

    const auto decoded = v2::service::decode_handler_payload(request);

    ASSERT_TRUE(decoded.has_value());
    ASSERT_TRUE(decoded->typed_request.has_value());
    EXPECT_EQ(decoded->encoding, v2::service::HandlerPayloadEncoding::kTypedEnvelope);
    EXPECT_EQ(decoded->typed_request->message_kind,
              v3::proto::EnvelopeMessageKind::kLoginRequest);
    EXPECT_EQ(decoded->payload.value("user_id", std::string{}), "alice");
}

TEST(V2ServiceBoundaryTest, DecodeHandlerPayloadMarksLegacyRawJsonDeprecated) {
    v2::service::BackendEnvelope request{
        .correlation_id = 78,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = R"({"user_id":"alice","token":"token:alice"})",
        .message_type = "login_request",
    };

    const auto decoded = v2::service::decode_handler_payload(request);

    ASSERT_TRUE(decoded.has_value());
    EXPECT_FALSE(decoded->typed_request.has_value());
    EXPECT_EQ(decoded->encoding, v2::service::HandlerPayloadEncoding::kLegacyRawJson);
    EXPECT_EQ(v2::service::legacy_raw_json_deprecation_notice(),
              "legacy raw JSON backend payload is deprecated; use typed envelope");
    EXPECT_EQ(v2::service::legacy_raw_json_policy_notice(),
              "legacy raw JSON is compatibility-only and must not be used for new handlers");
}

TEST(V2ServiceBoundaryTest, WrapTypedResponseLeavesLegacyPayloadRaw) {
    v2::service::BackendEnvelope response{
        .correlation_id = 1,
        .source_service = v2::service::ServiceId::kLogin,
        .target_service = v2::service::ServiceId::kGateway,
        .kind = v2::service::MessageKind::kResponse,
        .payload = R"({"status":"ok"})",
    };

    const auto wrapped = v2::service::wrap_typed_response_if_needed(
        std::nullopt,
        response,
        v3::proto::EnvelopeMessageKind::kLoginResponse);

    EXPECT_EQ(wrapped.payload, response.payload);
    EXPECT_FALSE(v3::proto::decode_typed_envelope(wrapped.payload).has_value());
}

TEST(V2ServiceBoundaryTest, WrapTypedResponseUsesRegisterAccountResponseKind) {
    v3::proto::EnvelopeMeta meta{
        .correlation_id = 79,
        .source_service = "gateway",
        .target_service = "login",
    };
    const auto request = v3::proto::decode_typed_envelope(
        v3::proto::encode_typed_envelope(
            meta,
            v3::proto::EnvelopeMessageKind::kRegisterAccountRequest,
            {{"user_id", "alice"}, {"credential", "secret"}}));
    ASSERT_TRUE(request.has_value());

    v2::service::BackendEnvelope response{
        .correlation_id = 79,
        .source_service = v2::service::ServiceId::kLogin,
        .target_service = v2::service::ServiceId::kGateway,
        .kind = v2::service::MessageKind::kResponse,
        .payload = R"({"status":"ok","user_id":"alice","display_name":"Alice"})",
    };

    const auto wrapped = v2::service::wrap_typed_response_if_needed(
        request,
        response,
        v3::proto::EnvelopeMessageKind::kRegisterAccountResponse);
    const auto decoded = v3::proto::decode_typed_envelope(wrapped.payload);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(decoded->message_kind,
              v3::proto::EnvelopeMessageKind::kRegisterAccountResponse);
    EXPECT_EQ(decoded->payload.value("status", std::string{}), "ok");
}

TEST(V2ServiceBoundaryTest, IsValidRejectsZeroCorrelationId) {
    v2::service::BackendEnvelope envelope{
        .correlation_id = 0,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
        .payload = "{}",
    };
    EXPECT_FALSE(v2::service::is_valid(envelope));
}

TEST(V2ServiceBoundaryTest, IsValidAcceptsErrorWithoutPayload) {
    v2::service::BackendEnvelope envelope{
        .correlation_id = 5,
        .source_service = v2::service::ServiceId::kLogin,
        .target_service = v2::service::ServiceId::kGateway,
        .kind = v2::service::MessageKind::kError,
        .error_code = -1001,
    };
    EXPECT_TRUE(v2::service::is_valid(envelope));
}

TEST(V2ServiceBoundaryTest, IsValidRejectsRequestWithoutPayload) {
    v2::service::BackendEnvelope envelope{
        .correlation_id = 5,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kLogin,
        .kind = v2::service::MessageKind::kRequest,
    };
    EXPECT_FALSE(v2::service::is_valid(envelope));
}

TEST(V2ServiceBoundaryTest, FromJsonRejectsInvalidJson) {
    EXPECT_FALSE(v2::service::from_json("not json").has_value());
}

TEST(V2ServiceBoundaryTest, FromJsonRejectsMissingFields) {
    EXPECT_FALSE(v2::service::from_json(R"({"correlation_id":1})").has_value());
}

TEST(V2ServiceBoundaryTest, FromJsonRejectsUnknownService) {
    EXPECT_FALSE(v2::service::from_json(
        R"({"correlation_id":1,"source_service":"unknown","target_service":"gateway","kind":"request","payload":"{}"})"
    ).has_value());
}

// ─── CorrelationId Generation ──────────────────────────────────

TEST(V2ServiceBoundaryTest, GenerateCorrelationIdIsMonotonic) {
    const auto first = v2::service::generate_correlation_id();
    const auto second = v2::service::generate_correlation_id();
    EXPECT_LT(first, second);
}

// ─── Service Manifest: Ownership ───────────────────────────────

TEST(V2ServiceBoundaryTest, GatewayManifestOwnsSessions) {
    const auto manifest = v2::service::gateway_manifest();
    EXPECT_EQ(manifest.service_id, v2::service::ServiceId::kGateway);

    bool owns_session = false;
    for (const auto& state : manifest.owned_state) {
        if (state == "session") owns_session = true;
    }
    EXPECT_TRUE(owns_session);
}

TEST(V2ServiceBoundaryTest, LoginManifestOwnsPlayerAuth) {
    const auto manifest = v2::service::login_manifest();
    EXPECT_EQ(manifest.service_id, v2::service::ServiceId::kLogin);

    bool owns_auth = false;
    for (const auto& state : manifest.owned_state) {
        if (state == "player_auth") owns_auth = true;
    }
    EXPECT_TRUE(owns_auth);
}

TEST(V2ServiceBoundaryTest, RoomManifestOwnsRooms) {
    const auto manifest = v2::service::room_manifest();
    EXPECT_EQ(manifest.service_id, v2::service::ServiceId::kRoom);

    bool owns_room = false;
    for (const auto& state : manifest.owned_state) {
        if (state == "room") owns_room = true;
    }
    EXPECT_TRUE(owns_room);
}

TEST(V2ServiceBoundaryTest, BattleManifestOwnsFrames) {
    const auto manifest = v2::service::battle_manifest();
    EXPECT_EQ(manifest.service_id, v2::service::ServiceId::kBattle);

    bool owns_frame = false;
    bool owns_replay = false;
    for (const auto& state : manifest.owned_state) {
        if (state == "frame") owns_frame = true;
        if (state == "replay") owns_replay = true;
    }
    EXPECT_TRUE(owns_frame);
    EXPECT_TRUE(owns_replay);
}

// ─── Owner / Handler Lookup ────────────────────────────────────

TEST(V2ServiceBoundaryTest, OwnerLookupReturnsCorrectService) {
    EXPECT_EQ(v2::service::owner_of("session"), v2::service::ServiceId::kGateway);
    EXPECT_EQ(v2::service::owner_of("player_auth"), v2::service::ServiceId::kLogin);
    EXPECT_EQ(v2::service::owner_of("room"), v2::service::ServiceId::kRoom);
    EXPECT_EQ(v2::service::owner_of("battle"), v2::service::ServiceId::kBattle);
    EXPECT_EQ(v2::service::owner_of("replay"), v2::service::ServiceId::kBattle);
}

TEST(V2ServiceBoundaryTest, HandlerLookupReturnsCorrectService) {
    EXPECT_EQ(v2::service::handler_of("login_request"), v2::service::ServiceId::kLogin);
    EXPECT_EQ(v2::service::handler_of("room_create"), v2::service::ServiceId::kRoom);
    EXPECT_EQ(v2::service::handler_of("battle_input"), v2::service::ServiceId::kBattle);
}

TEST(V2ServiceBoundaryTest, OwnerLookupUnknownReturnsGateway) {
    EXPECT_EQ(v2::service::owner_of("nonexistent_state"), v2::service::ServiceId::kGateway);
}

// ─── Error Codes ───────────────────────────────────────────────

TEST(V2ServiceBoundaryTest, ErrorCodeToString) {
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceErrorCode::kOk), "ok");
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceErrorCode::kTimeout), "timeout");
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceErrorCode::kUnavailable), "unavailable");
    EXPECT_STREQ(v2::service::to_string(v2::service::ServiceErrorCode::kRejected), "rejected");
}

TEST(V2ServiceBoundaryTest, ErrorCodeToClientMapping) {
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kOk), 0);
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kTimeout), -2001);
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kUnavailable), -2002);
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kRejected), -2003);
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kInvalidRequest), -2004);
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kInternalError), -2005);
    EXPECT_EQ(v2::service::to_client_error(v2::service::ServiceErrorCode::kNotImplemented), -2006);
}

// ─── All Manifests Are Consistent ───────────────────────────────

TEST(V2ServiceBoundaryTest, AllManifestsHaveUniqueServiceIds) {
    std::vector<v2::service::ServiceId> ids;
    ids.push_back(v2::service::gateway_manifest().service_id);
    ids.push_back(v2::service::login_manifest().service_id);
    ids.push_back(v2::service::room_manifest().service_id);
    ids.push_back(v2::service::battle_manifest().service_id);

    for (std::size_t i = 0; i < ids.size(); ++i) {
        for (std::size_t j = i + 1; j < ids.size(); ++j) {
            EXPECT_NE(ids[i], ids[j]);
        }
    }
}

TEST(V2ServiceBoundaryTest, AllManifestsHaveDescriptions) {
    EXPECT_FALSE(v2::service::gateway_manifest().description.empty());
    EXPECT_FALSE(v2::service::login_manifest().description.empty());
    EXPECT_FALSE(v2::service::room_manifest().description.empty());
    EXPECT_FALSE(v2::service::battle_manifest().description.empty());
}
