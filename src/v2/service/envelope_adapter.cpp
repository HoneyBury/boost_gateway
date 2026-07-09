#include "v2/service/envelope_adapter.h"

#include <array>

#include <nlohmann/json.hpp>

namespace v2::service {
namespace {

using v3::proto::EnvelopeMessageKind;

struct MessageTypeMapping {
    std::string_view backend_type;
    EnvelopeMessageKind typed_kind;
};

constexpr std::array<MessageTypeMapping, 59> kMessageTypeMappings{{
    {"login_request", EnvelopeMessageKind::kLoginRequest},
    {"login_response", EnvelopeMessageKind::kLoginResponse},
    {"token_validate", EnvelopeMessageKind::kTokenValidateRequest},
    {"token_validate_response", EnvelopeMessageKind::kTokenValidateResponse},
    {"session_bind", EnvelopeMessageKind::kSessionBindRequest},
    {"session_bind_response", EnvelopeMessageKind::kSessionBindResponse},
    {"session_close", EnvelopeMessageKind::kSessionCloseRequest},
    {"session_close_response", EnvelopeMessageKind::kSessionCloseResponse},
    {"token_refresh", EnvelopeMessageKind::kTokenRefreshRequest},
    {"token_refresh_response", EnvelopeMessageKind::kTokenRefreshResponse},
    {"room_create", EnvelopeMessageKind::kRoomCreateRequest},
    {"room_create_response", EnvelopeMessageKind::kRoomCreateResponse},
    {"room_join", EnvelopeMessageKind::kRoomJoinRequest},
    {"room_join_response", EnvelopeMessageKind::kRoomJoinResponse},
    {"room_leave", EnvelopeMessageKind::kRoomLeaveRequest},
    {"room_leave_response", EnvelopeMessageKind::kRoomLeaveResponse},
    {"room_ready", EnvelopeMessageKind::kRoomReadyRequest},
    {"room_ready_response", EnvelopeMessageKind::kRoomReadyResponse},
    {"room_start_battle", EnvelopeMessageKind::kRoomStartBattleRequest},
    {"room_start_battle_response", EnvelopeMessageKind::kRoomStartBattleResponse},
    {"room_state_push", EnvelopeMessageKind::kRoomStatePush},
    {"room_list", EnvelopeMessageKind::kRoomListRequest},
    {"room_list_response", EnvelopeMessageKind::kRoomListResponse},
    {"room_detail", EnvelopeMessageKind::kRoomDetailRequest},
    {"room_detail_response", EnvelopeMessageKind::kRoomDetailResponse},
    {"room_kick", EnvelopeMessageKind::kRoomKickRequest},
    {"room_kick_response", EnvelopeMessageKind::kRoomKickResponse},
    {"room_transfer_owner", EnvelopeMessageKind::kRoomTransferOwnerRequest},
    {"room_transfer_owner_response", EnvelopeMessageKind::kRoomTransferOwnerResponse},
    {"room_battle_finished", EnvelopeMessageKind::kRoomBattleFinishedRequest},
    {"room_battle_finished_response", EnvelopeMessageKind::kRoomBattleFinishedResponse},
    {"battle_create", EnvelopeMessageKind::kBattleCreateRequest},
    {"battle_create_response", EnvelopeMessageKind::kBattleCreateResponse},
    {"battle_input", EnvelopeMessageKind::kBattleInputRequest},
    {"battle_input_response", EnvelopeMessageKind::kBattleInputResponse},
    {"battle_state", EnvelopeMessageKind::kBattleStateRequest},
    {"battle_state_response", EnvelopeMessageKind::kBattleStateResponse},
    {"battle_state_push", EnvelopeMessageKind::kBattleStatePush},
    {"battle_finish", EnvelopeMessageKind::kBattleFinishRequest},
    {"battle_finish_response", EnvelopeMessageKind::kBattleFinishResponse},
    {"replay_load", EnvelopeMessageKind::kReplayLoadRequest},
    {"replay_load_response", EnvelopeMessageKind::kReplayLoadResponse},
    {"match_join", EnvelopeMessageKind::kMatchJoinRequest},
    {"match_join_response", EnvelopeMessageKind::kMatchJoinResponse},
    {"match_leave", EnvelopeMessageKind::kMatchLeaveRequest},
    {"match_leave_response", EnvelopeMessageKind::kMatchLeaveResponse},
    {"match_status", EnvelopeMessageKind::kMatchStatusRequest},
    {"match_status_response", EnvelopeMessageKind::kMatchStatusResponse},
    {"leaderboard_submit", EnvelopeMessageKind::kLeaderboardSubmitRequest},
    {"leaderboard_submit_response", EnvelopeMessageKind::kLeaderboardSubmitResponse},
    {"leaderboard_top", EnvelopeMessageKind::kLeaderboardTopRequest},
    {"leaderboard_top_response", EnvelopeMessageKind::kLeaderboardTopResponse},
    {"leaderboard_rank", EnvelopeMessageKind::kLeaderboardRankRequest},
    {"leaderboard_rank_response", EnvelopeMessageKind::kLeaderboardRankResponse},
    {"match_found_push", EnvelopeMessageKind::kMatchFoundPush},
    {"match_to_room", EnvelopeMessageKind::kMatchToRoomRequest},
    {"match_to_room_response", EnvelopeMessageKind::kMatchToRoomResponse},
}};

[[nodiscard]] v3::proto::EnvelopeMeta to_meta(const BackendEnvelope& envelope) {
    return v3::proto::EnvelopeMeta{
        .correlation_id = envelope.correlation_id,
        .source_service = to_string(envelope.source_service),
        .target_service = to_string(envelope.target_service),
        .timeout_ms = envelope.timeout_ms,
        .error_code = envelope.error_code,
        .trace_id = envelope.trace_id,
        .span_id = envelope.span_id,
    };
}

[[nodiscard]] ServiceId service_from_meta(std::string_view service) {
    if (service == "login") return ServiceId::kLogin;
    if (service == "room") return ServiceId::kRoom;
    if (service == "battle") return ServiceId::kBattle;
    if (service == "match" || service == "matchmaking") return ServiceId::kMatchmaking;
    if (service == "leaderboard") return ServiceId::kLeaderboard;
    return ServiceId::kGateway;
}

}  // namespace

std::optional<v3::proto::EnvelopeMessageKind>
message_kind_from_backend_type(std::string_view message_type) {
    for (const auto& mapping : kMessageTypeMappings) {
        if (mapping.backend_type == message_type) {
            return mapping.typed_kind;
        }
    }
    return std::nullopt;
}

std::string backend_type_from_message_kind(v3::proto::EnvelopeMessageKind kind) {
    for (const auto& mapping : kMessageTypeMappings) {
        if (mapping.typed_kind == kind) {
            return std::string(mapping.backend_type);
        }
    }
    return {};
}

std::optional<v3::proto::TypedEnvelope> to_typed_envelope(const BackendEnvelope& envelope) {
    const auto typed_kind = message_kind_from_backend_type(envelope.message_type);
    if (!typed_kind.has_value()) {
        return std::nullopt;
    }

    nlohmann::json payload = nlohmann::json::object();
    if (!envelope.payload.empty()) {
        payload = nlohmann::json::parse(envelope.payload, nullptr, false);
        if (payload.is_discarded()) {
            return std::nullopt;
        }
    }

    return v3::proto::TypedEnvelope{
        .meta = to_meta(envelope),
        .message_kind = *typed_kind,
        .payload = std::move(payload),
    };
}

BackendEnvelope to_backend_envelope(const v3::proto::TypedEnvelope& envelope, MessageKind kind) {
    return BackendEnvelope{
        .correlation_id = envelope.meta.correlation_id,
        .source_service = service_from_meta(envelope.meta.source_service),
        .target_service = service_from_meta(envelope.meta.target_service),
        .kind = kind,
        .timeout_ms = envelope.meta.timeout_ms,
        .error_code = envelope.meta.error_code,
        .payload = envelope.payload.dump(),
        .message_type = backend_type_from_message_kind(envelope.message_kind),
        .trace_id = envelope.meta.trace_id,
        .span_id = envelope.meta.span_id,
    };
}

std::optional<DecodedHandlerPayload> decode_handler_payload(const BackendEnvelope& envelope) {
    DecodedHandlerPayload decoded;
    decoded.typed_request = v3::proto::decode_typed_envelope(envelope.payload);
    if (decoded.typed_request.has_value()) {
        decoded.payload = decoded.typed_request->payload;
        decoded.encoding = HandlerPayloadEncoding::kTypedEnvelope;
        return decoded;
    }

    decoded.payload = nlohmann::json::parse(envelope.payload, nullptr, false);
    if (decoded.payload.is_discarded()) {
        return std::nullopt;
    }
    decoded.encoding = HandlerPayloadEncoding::kLegacyRawJson;
    return decoded;
}

BackendEnvelope wrap_typed_response_if_needed(
    const std::optional<v3::proto::TypedEnvelope>& request_envelope,
    BackendEnvelope response,
    v3::proto::EnvelopeMessageKind response_kind) {
    if (!request_envelope.has_value()) {
        return response;
    }

    auto payload = nlohmann::json::parse(response.payload, nullptr, false);
    response.payload = v3::proto::maybe_wrap_typed_response(
        request_envelope,
        response_kind,
        payload.is_discarded() ? nlohmann::json::object() : payload,
        response.error_code);
    return response;
}

}  // namespace v2::service
