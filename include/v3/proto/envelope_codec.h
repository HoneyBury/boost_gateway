#pragma once

#include <cstdint>
#include <optional>
#include <string>

#include <nlohmann/json.hpp>

namespace v3::proto {

struct EnvelopeMeta {
    std::uint64_t correlation_id = 0;
    std::string source_service;
    std::string target_service;
    std::uint32_t timeout_ms = 0;
    std::int32_t error_code = 0;
    std::uint64_t trace_id = 0;
    std::uint64_t span_id = 0;
};

enum class EnvelopeDomain : std::uint8_t {
    kUnknown = 0,
    kLogin,
    kRoom,
    kBattle,
    kMatch,
    kLeaderboard,
};

enum class EnvelopeMessageKind : std::uint16_t {
    kUnknown = 0,
    kLoginRequest,
    kLoginResponse,
    kRegisterAccountRequest,
    kRegisterAccountResponse,
    kGuestLoginRequest,
    kGuestLoginResponse,
    kTokenValidateRequest,
    kTokenValidateResponse,
    kSessionBindRequest,
    kSessionBindResponse,
    kSessionCloseRequest,
    kSessionCloseResponse,
    kTokenRefreshRequest,
    kTokenRefreshResponse,
    kRoomCreateRequest,
    kRoomCreateResponse,
    kRoomJoinRequest,
    kRoomJoinResponse,
    kRoomLeaveRequest,
    kRoomLeaveResponse,
    kRoomReadyRequest,
    kRoomReadyResponse,
    kRoomStartBattleRequest,
    kRoomStartBattleResponse,
    kRoomStatePush,
    kRoomListRequest,
    kRoomListResponse,
    kRoomDetailRequest,
    kRoomDetailResponse,
    kRoomKickRequest,
    kRoomKickResponse,
    kRoomTransferOwnerRequest,
    kRoomTransferOwnerResponse,
    kRoomBattleFinishedRequest,
    kRoomBattleFinishedResponse,
    kBattleCreateRequest,
    kBattleCreateResponse,
    kBattleInputRequest,
    kBattleInputResponse,
    kBattleStateRequest,
    kBattleStateResponse,
    kBattleStatePush,
    kBattleFinishRequest,
    kBattleFinishResponse,
    kReplayLoadRequest,
    kReplayLoadResponse,
    kMatchJoinRequest,
    kMatchJoinResponse,
    kMatchLeaveRequest,
    kMatchLeaveResponse,
    kMatchStatusRequest,
    kMatchStatusResponse,
    kMatchFoundPush,
    kMatchToRoomRequest,
    kMatchToRoomResponse,
    kLeaderboardSubmitRequest,
    kLeaderboardSubmitResponse,
    kLeaderboardTopRequest,
    kLeaderboardTopResponse,
    kLeaderboardRankRequest,
    kLeaderboardRankResponse,
};

struct DecodedEnvelope {
    EnvelopeMeta meta;
    EnvelopeDomain domain = EnvelopeDomain::kUnknown;
    EnvelopeMessageKind message_kind = EnvelopeMessageKind::kUnknown;
    nlohmann::json payload;
};

struct TypedEnvelope {
    EnvelopeMeta meta;
    EnvelopeMessageKind message_kind = EnvelopeMessageKind::kUnknown;
    nlohmann::json payload;
};

struct MatchJoinRequestPayload {
    std::string user_id;
    std::int64_t mmr = 1000;
    std::string mode = "1v1";
};

struct LeaderboardSubmitRequestPayload {
    std::string user_id;
    std::string display_name;
    std::int64_t score = 0;
};

inline std::string to_string(EnvelopeDomain domain) {
    switch (domain) {
        case EnvelopeDomain::kLogin: return "login";
        case EnvelopeDomain::kRoom: return "room";
        case EnvelopeDomain::kBattle: return "battle";
        case EnvelopeDomain::kMatch: return "match";
        case EnvelopeDomain::kLeaderboard: return "leaderboard";
        default: return "unknown";
    }
}

inline std::optional<EnvelopeDomain> domain_from_string(const std::string& domain) {
    if (domain == "login") return EnvelopeDomain::kLogin;
    if (domain == "room") return EnvelopeDomain::kRoom;
    if (domain == "battle") return EnvelopeDomain::kBattle;
    if (domain == "match") return EnvelopeDomain::kMatch;
    if (domain == "leaderboard") return EnvelopeDomain::kLeaderboard;
    return std::nullopt;
}

inline std::string to_string(EnvelopeMessageKind kind) {
    switch (kind) {
        case EnvelopeMessageKind::kLoginRequest: return "login_request";
        case EnvelopeMessageKind::kLoginResponse: return "login_response";
        case EnvelopeMessageKind::kRegisterAccountRequest: return "register_account";
        case EnvelopeMessageKind::kRegisterAccountResponse: return "register_account_response";
        case EnvelopeMessageKind::kGuestLoginRequest: return "guest_login";
        case EnvelopeMessageKind::kGuestLoginResponse: return "guest_login_response";
        case EnvelopeMessageKind::kTokenValidateRequest: return "token_validate";
        case EnvelopeMessageKind::kTokenValidateResponse: return "token_validate_response";
        case EnvelopeMessageKind::kSessionBindRequest: return "session_bind";
        case EnvelopeMessageKind::kSessionBindResponse: return "session_bind_response";
        case EnvelopeMessageKind::kSessionCloseRequest: return "session_close";
        case EnvelopeMessageKind::kSessionCloseResponse: return "session_close_response";
        case EnvelopeMessageKind::kTokenRefreshRequest: return "token_refresh";
        case EnvelopeMessageKind::kTokenRefreshResponse: return "token_refresh_response";
        case EnvelopeMessageKind::kRoomCreateRequest: return "room_create";
        case EnvelopeMessageKind::kRoomCreateResponse: return "room_create_response";
        case EnvelopeMessageKind::kRoomJoinRequest: return "room_join";
        case EnvelopeMessageKind::kRoomJoinResponse: return "room_join_response";
        case EnvelopeMessageKind::kRoomLeaveRequest: return "room_leave";
        case EnvelopeMessageKind::kRoomLeaveResponse: return "room_leave_response";
        case EnvelopeMessageKind::kRoomReadyRequest: return "room_ready";
        case EnvelopeMessageKind::kRoomReadyResponse: return "room_ready_response";
        case EnvelopeMessageKind::kRoomStartBattleRequest: return "room_start_battle";
        case EnvelopeMessageKind::kRoomStartBattleResponse: return "room_start_battle_response";
        case EnvelopeMessageKind::kRoomStatePush: return "room_state_push";
        case EnvelopeMessageKind::kRoomListRequest: return "room_list";
        case EnvelopeMessageKind::kRoomListResponse: return "room_list_response";
        case EnvelopeMessageKind::kRoomDetailRequest: return "room_detail";
        case EnvelopeMessageKind::kRoomDetailResponse: return "room_detail_response";
        case EnvelopeMessageKind::kRoomKickRequest: return "room_kick";
        case EnvelopeMessageKind::kRoomKickResponse: return "room_kick_response";
        case EnvelopeMessageKind::kRoomTransferOwnerRequest: return "room_transfer_owner";
        case EnvelopeMessageKind::kRoomTransferOwnerResponse: return "room_transfer_owner_response";
        case EnvelopeMessageKind::kRoomBattleFinishedRequest: return "room_battle_finished";
        case EnvelopeMessageKind::kRoomBattleFinishedResponse: return "room_battle_finished_response";
        case EnvelopeMessageKind::kBattleCreateRequest: return "battle_create";
        case EnvelopeMessageKind::kBattleCreateResponse: return "battle_create_response";
        case EnvelopeMessageKind::kBattleInputRequest: return "battle_input";
        case EnvelopeMessageKind::kBattleInputResponse: return "battle_input_response";
        case EnvelopeMessageKind::kBattleStateRequest: return "battle_state";
        case EnvelopeMessageKind::kBattleStateResponse: return "battle_state_response";
        case EnvelopeMessageKind::kBattleStatePush: return "battle_state_push";
        case EnvelopeMessageKind::kBattleFinishRequest: return "battle_finish";
        case EnvelopeMessageKind::kBattleFinishResponse: return "battle_finish_response";
        case EnvelopeMessageKind::kReplayLoadRequest: return "replay_load";
        case EnvelopeMessageKind::kReplayLoadResponse: return "replay_load_response";
        case EnvelopeMessageKind::kMatchJoinRequest: return "match_join";
        case EnvelopeMessageKind::kMatchJoinResponse: return "match_join_response";
        case EnvelopeMessageKind::kMatchLeaveRequest: return "match_leave";
        case EnvelopeMessageKind::kMatchLeaveResponse: return "match_leave_response";
        case EnvelopeMessageKind::kMatchStatusRequest: return "match_status";
        case EnvelopeMessageKind::kMatchStatusResponse: return "match_status_response";
        case EnvelopeMessageKind::kMatchFoundPush: return "match_found_push";
        case EnvelopeMessageKind::kMatchToRoomRequest: return "match_to_room";
        case EnvelopeMessageKind::kMatchToRoomResponse: return "match_to_room_response";
        case EnvelopeMessageKind::kLeaderboardSubmitRequest: return "submit";
        case EnvelopeMessageKind::kLeaderboardSubmitResponse: return "submit_response";
        case EnvelopeMessageKind::kLeaderboardTopRequest: return "top";
        case EnvelopeMessageKind::kLeaderboardTopResponse: return "top_response";
        case EnvelopeMessageKind::kLeaderboardRankRequest: return "rank";
        case EnvelopeMessageKind::kLeaderboardRankResponse: return "rank_response";
        default: return "unknown";
    }
}

inline std::optional<EnvelopeMessageKind> kind_from_string(const std::string& kind) {
    if (kind == "login_request") return EnvelopeMessageKind::kLoginRequest;
    if (kind == "login_response") return EnvelopeMessageKind::kLoginResponse;
    if (kind == "register_account") return EnvelopeMessageKind::kRegisterAccountRequest;
    if (kind == "register_account_response") return EnvelopeMessageKind::kRegisterAccountResponse;
    if (kind == "guest_login") return EnvelopeMessageKind::kGuestLoginRequest;
    if (kind == "guest_login_response") return EnvelopeMessageKind::kGuestLoginResponse;
    if (kind == "token_validate") return EnvelopeMessageKind::kTokenValidateRequest;
    if (kind == "token_validate_response") return EnvelopeMessageKind::kTokenValidateResponse;
    if (kind == "session_bind") return EnvelopeMessageKind::kSessionBindRequest;
    if (kind == "session_bind_response") return EnvelopeMessageKind::kSessionBindResponse;
    if (kind == "session_close") return EnvelopeMessageKind::kSessionCloseRequest;
    if (kind == "session_close_response") return EnvelopeMessageKind::kSessionCloseResponse;
    if (kind == "token_refresh") return EnvelopeMessageKind::kTokenRefreshRequest;
    if (kind == "token_refresh_response") return EnvelopeMessageKind::kTokenRefreshResponse;
    if (kind == "room_create") return EnvelopeMessageKind::kRoomCreateRequest;
    if (kind == "room_create_response") return EnvelopeMessageKind::kRoomCreateResponse;
    if (kind == "room_join") return EnvelopeMessageKind::kRoomJoinRequest;
    if (kind == "room_join_response") return EnvelopeMessageKind::kRoomJoinResponse;
    if (kind == "room_leave") return EnvelopeMessageKind::kRoomLeaveRequest;
    if (kind == "room_leave_response") return EnvelopeMessageKind::kRoomLeaveResponse;
    if (kind == "room_ready") return EnvelopeMessageKind::kRoomReadyRequest;
    if (kind == "room_ready_response") return EnvelopeMessageKind::kRoomReadyResponse;
    if (kind == "room_start_battle") return EnvelopeMessageKind::kRoomStartBattleRequest;
    if (kind == "room_start_battle_response") return EnvelopeMessageKind::kRoomStartBattleResponse;
    if (kind == "room_state_push") return EnvelopeMessageKind::kRoomStatePush;
    if (kind == "room_list") return EnvelopeMessageKind::kRoomListRequest;
    if (kind == "room_list_response") return EnvelopeMessageKind::kRoomListResponse;
    if (kind == "room_detail") return EnvelopeMessageKind::kRoomDetailRequest;
    if (kind == "room_detail_response") return EnvelopeMessageKind::kRoomDetailResponse;
    if (kind == "room_kick") return EnvelopeMessageKind::kRoomKickRequest;
    if (kind == "room_kick_response") return EnvelopeMessageKind::kRoomKickResponse;
    if (kind == "room_transfer_owner") return EnvelopeMessageKind::kRoomTransferOwnerRequest;
    if (kind == "room_transfer_owner_response") return EnvelopeMessageKind::kRoomTransferOwnerResponse;
    if (kind == "room_battle_finished") return EnvelopeMessageKind::kRoomBattleFinishedRequest;
    if (kind == "room_battle_finished_response") return EnvelopeMessageKind::kRoomBattleFinishedResponse;
    if (kind == "battle_create") return EnvelopeMessageKind::kBattleCreateRequest;
    if (kind == "battle_create_response") return EnvelopeMessageKind::kBattleCreateResponse;
    if (kind == "battle_input") return EnvelopeMessageKind::kBattleInputRequest;
    if (kind == "battle_input_response") return EnvelopeMessageKind::kBattleInputResponse;
    if (kind == "battle_state") return EnvelopeMessageKind::kBattleStateRequest;
    if (kind == "battle_state_response") return EnvelopeMessageKind::kBattleStateResponse;
    if (kind == "battle_state_push") return EnvelopeMessageKind::kBattleStatePush;
    if (kind == "battle_finish") return EnvelopeMessageKind::kBattleFinishRequest;
    if (kind == "battle_finish_response") return EnvelopeMessageKind::kBattleFinishResponse;
    if (kind == "replay_load") return EnvelopeMessageKind::kReplayLoadRequest;
    if (kind == "replay_load_response") return EnvelopeMessageKind::kReplayLoadResponse;
    if (kind == "match_join") return EnvelopeMessageKind::kMatchJoinRequest;
    if (kind == "match_join_response") return EnvelopeMessageKind::kMatchJoinResponse;
    if (kind == "match_leave") return EnvelopeMessageKind::kMatchLeaveRequest;
    if (kind == "match_leave_response") return EnvelopeMessageKind::kMatchLeaveResponse;
    if (kind == "match_status") return EnvelopeMessageKind::kMatchStatusRequest;
    if (kind == "match_status_response") return EnvelopeMessageKind::kMatchStatusResponse;
    if (kind == "match_found_push") return EnvelopeMessageKind::kMatchFoundPush;
    if (kind == "match_to_room") return EnvelopeMessageKind::kMatchToRoomRequest;
    if (kind == "match_to_room_response") return EnvelopeMessageKind::kMatchToRoomResponse;
    if (kind == "submit") return EnvelopeMessageKind::kLeaderboardSubmitRequest;
    if (kind == "submit_response") return EnvelopeMessageKind::kLeaderboardSubmitResponse;
    if (kind == "top") return EnvelopeMessageKind::kLeaderboardTopRequest;
    if (kind == "top_response") return EnvelopeMessageKind::kLeaderboardTopResponse;
    if (kind == "rank") return EnvelopeMessageKind::kLeaderboardRankRequest;
    if (kind == "rank_response") return EnvelopeMessageKind::kLeaderboardRankResponse;
    return std::nullopt;
}

inline std::optional<EnvelopeDomain> domain_for_kind(EnvelopeMessageKind kind) {
    switch (kind) {
        case EnvelopeMessageKind::kLoginRequest:
        case EnvelopeMessageKind::kLoginResponse:
        case EnvelopeMessageKind::kRegisterAccountRequest:
        case EnvelopeMessageKind::kRegisterAccountResponse:
        case EnvelopeMessageKind::kGuestLoginRequest:
        case EnvelopeMessageKind::kGuestLoginResponse:
        case EnvelopeMessageKind::kTokenValidateRequest:
        case EnvelopeMessageKind::kTokenValidateResponse:
        case EnvelopeMessageKind::kSessionBindRequest:
        case EnvelopeMessageKind::kSessionBindResponse:
        case EnvelopeMessageKind::kSessionCloseRequest:
        case EnvelopeMessageKind::kSessionCloseResponse:
        case EnvelopeMessageKind::kTokenRefreshRequest:
        case EnvelopeMessageKind::kTokenRefreshResponse:
            return EnvelopeDomain::kLogin;
        case EnvelopeMessageKind::kRoomCreateRequest:
        case EnvelopeMessageKind::kRoomCreateResponse:
        case EnvelopeMessageKind::kRoomJoinRequest:
        case EnvelopeMessageKind::kRoomJoinResponse:
        case EnvelopeMessageKind::kRoomLeaveRequest:
        case EnvelopeMessageKind::kRoomLeaveResponse:
        case EnvelopeMessageKind::kRoomReadyRequest:
        case EnvelopeMessageKind::kRoomReadyResponse:
        case EnvelopeMessageKind::kRoomStartBattleRequest:
        case EnvelopeMessageKind::kRoomStartBattleResponse:
        case EnvelopeMessageKind::kRoomStatePush:
        case EnvelopeMessageKind::kRoomListRequest:
        case EnvelopeMessageKind::kRoomListResponse:
        case EnvelopeMessageKind::kRoomDetailRequest:
        case EnvelopeMessageKind::kRoomDetailResponse:
        case EnvelopeMessageKind::kRoomKickRequest:
        case EnvelopeMessageKind::kRoomKickResponse:
        case EnvelopeMessageKind::kRoomTransferOwnerRequest:
        case EnvelopeMessageKind::kRoomTransferOwnerResponse:
        case EnvelopeMessageKind::kRoomBattleFinishedRequest:
        case EnvelopeMessageKind::kRoomBattleFinishedResponse:
            return EnvelopeDomain::kRoom;
        case EnvelopeMessageKind::kBattleCreateRequest:
        case EnvelopeMessageKind::kBattleCreateResponse:
        case EnvelopeMessageKind::kBattleInputRequest:
        case EnvelopeMessageKind::kBattleInputResponse:
        case EnvelopeMessageKind::kBattleStateRequest:
        case EnvelopeMessageKind::kBattleStateResponse:
        case EnvelopeMessageKind::kBattleStatePush:
        case EnvelopeMessageKind::kBattleFinishRequest:
        case EnvelopeMessageKind::kBattleFinishResponse:
        case EnvelopeMessageKind::kReplayLoadRequest:
        case EnvelopeMessageKind::kReplayLoadResponse:
            return EnvelopeDomain::kBattle;
        case EnvelopeMessageKind::kMatchJoinRequest:
        case EnvelopeMessageKind::kMatchJoinResponse:
        case EnvelopeMessageKind::kMatchLeaveRequest:
        case EnvelopeMessageKind::kMatchLeaveResponse:
        case EnvelopeMessageKind::kMatchStatusRequest:
        case EnvelopeMessageKind::kMatchStatusResponse:
        case EnvelopeMessageKind::kMatchFoundPush:
        case EnvelopeMessageKind::kMatchToRoomRequest:
        case EnvelopeMessageKind::kMatchToRoomResponse:
            return EnvelopeDomain::kMatch;
        case EnvelopeMessageKind::kLeaderboardSubmitRequest:
        case EnvelopeMessageKind::kLeaderboardSubmitResponse:
        case EnvelopeMessageKind::kLeaderboardTopRequest:
        case EnvelopeMessageKind::kLeaderboardTopResponse:
        case EnvelopeMessageKind::kLeaderboardRankRequest:
        case EnvelopeMessageKind::kLeaderboardRankResponse:
            return EnvelopeDomain::kLeaderboard;
        default:
            return std::nullopt;
    }
}

inline std::string encode_envelope(const EnvelopeMeta& meta,
                                   EnvelopeDomain domain,
                                   EnvelopeMessageKind kind,
                                   const nlohmann::json& payload) {
    nlohmann::json doc{
        {"correlation_id", meta.correlation_id},
        {"source_service", meta.source_service},
        {"target_service", meta.target_service},
        {"timeout_ms", meta.timeout_ms},
        {"error_code", meta.error_code},
        {"trace_id", meta.trace_id},
        {"span_id", meta.span_id},
        {"payload", nlohmann::json::object()},
    };
    const auto domain_key = to_string(domain);
    const auto kind_key = to_string(kind);
    doc["payload"][domain_key] = nlohmann::json::object();
    doc["payload"][domain_key][kind_key] = payload;
    return doc.dump();
}

inline std::optional<DecodedEnvelope> decode_envelope(const std::string& encoded) {
    auto doc = nlohmann::json::parse(encoded, nullptr, false);
    if (doc.is_discarded() || !doc.is_object() || !doc.contains("payload") ||
        !doc["payload"].is_object()) {
        return std::nullopt;
    }

    DecodedEnvelope decoded;
    decoded.meta.correlation_id = doc.value("correlation_id", std::uint64_t{0});
    decoded.meta.source_service = doc.value("source_service", std::string{});
    decoded.meta.target_service = doc.value("target_service", std::string{});
    decoded.meta.timeout_ms = doc.value("timeout_ms", std::uint32_t{0});
    decoded.meta.error_code = doc.value("error_code", std::int32_t{0});
    decoded.meta.trace_id = doc.value("trace_id", std::uint64_t{0});
    decoded.meta.span_id = doc.value("span_id", std::uint64_t{0});

    const auto& payloads = doc["payload"];
    if (payloads.size() != 1U) {
        return std::nullopt;
    }
    const auto domain_it = payloads.begin();
    auto domain = domain_from_string(domain_it.key());
    if (!domain.has_value()) {
        return std::nullopt;
    }
    if (!domain_it.value().is_object() || domain_it.value().size() != 1U) {
        return std::nullopt;
    }
    const auto message_it = domain_it.value().begin();
    auto kind = kind_from_string(message_it.key());
    if (!kind.has_value()) {
        return std::nullopt;
    }
    decoded.domain = *domain;
    decoded.message_kind = *kind;
    decoded.payload = message_it.value();
    return decoded;
}

inline std::optional<TypedEnvelope> decode_typed_envelope(const std::string& encoded) {
    auto decoded = decode_envelope(encoded);
    if (!decoded.has_value()) {
        return std::nullopt;
    }
    return TypedEnvelope{
        .meta = decoded->meta,
        .message_kind = decoded->message_kind,
        .payload = decoded->payload,
    };
}

inline bool is_envelope_payload(const std::string& encoded) {
    return decode_envelope(encoded).has_value();
}

inline std::string encode_typed_envelope(const EnvelopeMeta& meta,
                                         EnvelopeMessageKind kind,
                                         const nlohmann::json& payload) {
    auto domain = domain_for_kind(kind);
    if (!domain.has_value()) {
        return {};
    }
    return encode_envelope(meta, *domain, kind, payload);
}

inline std::string encode_match_join_request(const EnvelopeMeta& meta,
                                             const MatchJoinRequestPayload& payload) {
    return encode_typed_envelope(meta,
                                 EnvelopeMessageKind::kMatchJoinRequest,
                                 {
                                     {"user_id", payload.user_id},
                                     {"mmr", payload.mmr},
                                     {"mode", payload.mode},
                                 });
}

inline std::string encode_leaderboard_submit_request(
    const EnvelopeMeta& meta,
    const LeaderboardSubmitRequestPayload& payload) {
    return encode_typed_envelope(meta,
                                 EnvelopeMessageKind::kLeaderboardSubmitRequest,
                                 {
                                     {"user_id", payload.user_id},
                                     {"display_name", payload.display_name},
                                     {"score", payload.score},
                                 });
}

inline std::string maybe_wrap_response(const std::optional<DecodedEnvelope>& request_envelope,
                                       EnvelopeDomain domain,
                                       EnvelopeMessageKind kind,
                                       const nlohmann::json& payload,
                                       std::int32_t error_code = 0) {
    if (!request_envelope.has_value()) {
        return payload.dump();
    }
    auto meta = request_envelope->meta;
    meta.error_code = error_code;
    return encode_envelope(meta, domain, kind, payload);
}

inline std::string maybe_wrap_typed_response(const std::optional<TypedEnvelope>& request_envelope,
                                             EnvelopeMessageKind kind,
                                             const nlohmann::json& payload,
                                             std::int32_t error_code = 0) {
    if (!request_envelope.has_value()) {
        return payload.dump();
    }
    auto domain = domain_for_kind(kind);
    if (!domain.has_value()) {
        return payload.dump();
    }
    auto meta = request_envelope->meta;
    meta.error_code = error_code;
    return encode_envelope(meta, *domain, kind, payload);
}

}  // namespace v3::proto
