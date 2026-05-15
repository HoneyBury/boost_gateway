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
    kRoomCreateRequest,
    kRoomCreateResponse,
    kRoomJoinRequest,
    kRoomJoinResponse,
    kRoomReadyRequest,
    kRoomReadyResponse,
    kBattleInputRequest,
    kBattleInputResponse,
    kMatchJoinRequest,
    kMatchJoinResponse,
    kMatchLeaveRequest,
    kMatchLeaveResponse,
    kMatchStatusRequest,
    kMatchStatusResponse,
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
        case EnvelopeMessageKind::kRoomCreateRequest: return "room_create";
        case EnvelopeMessageKind::kRoomCreateResponse: return "room_create_response";
        case EnvelopeMessageKind::kRoomJoinRequest: return "room_join";
        case EnvelopeMessageKind::kRoomJoinResponse: return "room_join_response";
        case EnvelopeMessageKind::kRoomReadyRequest: return "room_ready";
        case EnvelopeMessageKind::kRoomReadyResponse: return "room_ready_response";
        case EnvelopeMessageKind::kBattleInputRequest: return "battle_input";
        case EnvelopeMessageKind::kBattleInputResponse: return "battle_input_response";
        case EnvelopeMessageKind::kMatchJoinRequest: return "match_join";
        case EnvelopeMessageKind::kMatchJoinResponse: return "match_join_response";
        case EnvelopeMessageKind::kMatchLeaveRequest: return "match_leave";
        case EnvelopeMessageKind::kMatchLeaveResponse: return "match_leave_response";
        case EnvelopeMessageKind::kMatchStatusRequest: return "match_status";
        case EnvelopeMessageKind::kMatchStatusResponse: return "match_status_response";
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
    if (kind == "room_create") return EnvelopeMessageKind::kRoomCreateRequest;
    if (kind == "room_create_response") return EnvelopeMessageKind::kRoomCreateResponse;
    if (kind == "room_join") return EnvelopeMessageKind::kRoomJoinRequest;
    if (kind == "room_join_response") return EnvelopeMessageKind::kRoomJoinResponse;
    if (kind == "room_ready") return EnvelopeMessageKind::kRoomReadyRequest;
    if (kind == "room_ready_response") return EnvelopeMessageKind::kRoomReadyResponse;
    if (kind == "battle_input") return EnvelopeMessageKind::kBattleInputRequest;
    if (kind == "battle_input_response") return EnvelopeMessageKind::kBattleInputResponse;
    if (kind == "match_join") return EnvelopeMessageKind::kMatchJoinRequest;
    if (kind == "match_join_response") return EnvelopeMessageKind::kMatchJoinResponse;
    if (kind == "match_leave") return EnvelopeMessageKind::kMatchLeaveRequest;
    if (kind == "match_leave_response") return EnvelopeMessageKind::kMatchLeaveResponse;
    if (kind == "match_status") return EnvelopeMessageKind::kMatchStatusRequest;
    if (kind == "match_status_response") return EnvelopeMessageKind::kMatchStatusResponse;
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
            return EnvelopeDomain::kLogin;
        case EnvelopeMessageKind::kRoomCreateRequest:
        case EnvelopeMessageKind::kRoomCreateResponse:
        case EnvelopeMessageKind::kRoomJoinRequest:
        case EnvelopeMessageKind::kRoomJoinResponse:
        case EnvelopeMessageKind::kRoomReadyRequest:
        case EnvelopeMessageKind::kRoomReadyResponse:
            return EnvelopeDomain::kRoom;
        case EnvelopeMessageKind::kBattleInputRequest:
        case EnvelopeMessageKind::kBattleInputResponse:
            return EnvelopeDomain::kBattle;
        case EnvelopeMessageKind::kMatchJoinRequest:
        case EnvelopeMessageKind::kMatchJoinResponse:
        case EnvelopeMessageKind::kMatchLeaveRequest:
        case EnvelopeMessageKind::kMatchLeaveResponse:
        case EnvelopeMessageKind::kMatchStatusRequest:
        case EnvelopeMessageKind::kMatchStatusResponse:
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
