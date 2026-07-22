#include "v3/cluster/raft_command_codec.h"

#include "v3/cluster/raft_state_codec.h"

#include <nlohmann/json.hpp>

#ifdef BOOST_BUILD_RAFT_PROTOBUF
#include "raft.pb.h"
#include <google/protobuf/io/coded_stream.h>
#include <google/protobuf/io/zero_copy_stream_impl_lite.h>
#endif

#include <algorithm>
#include <array>
#include <cctype>
#include <limits>
#include <stdexcept>
#include <string_view>

namespace v3::cluster {
namespace {

using Json = nlohmann::json;
constexpr std::string_view kCommandMagic{"BGRC", 4};
constexpr std::uint32_t kCommandVersion = 1;
constexpr std::size_t kMaxIdentityBytes = 1024;
constexpr std::size_t kMaxDisplayNameBytes = 4096;
constexpr std::size_t kMaxCommandUsers = 256;

[[noreturn]] void command_error(const std::string& detail) {
    throw std::invalid_argument("invalid Raft command: " + detail);
}

void validate_string(const std::string& value, std::size_t maximum,
                     std::string_view field, bool allow_empty = false) {
    if ((!allow_empty && value.empty()) || value.size() > maximum ||
        value.find('\0') != std::string::npos) {
        command_error(std::string(field) + " is empty, oversized, or contains NUL");
    }
}

void validate_mode(RaftMatchMode mode) {
    if (mode != RaftMatchMode::kOneVsOne && mode != RaftMatchMode::kTwoVsTwo &&
        mode != RaftMatchMode::kFourVsFour) {
        command_error("match mode is unsupported");
    }
}

void validate_users(const std::vector<std::string>& users) {
    if (users.empty() || users.size() > kMaxCommandUsers) {
        command_error("user list is empty or oversized");
    }
    for (const auto& user : users) {
        validate_string(user, kMaxIdentityBytes, "user ID");
    }
}

void validate_command(const RaftCommand& command) {
    switch (command.kind) {
        case RaftCommandKind::kMatchJoin:
            validate_string(command.user_id, kMaxIdentityBytes, "user_id");
            validate_mode(command.mode);
            break;
        case RaftCommandKind::kMatchLeave:
            validate_string(command.user_id, kMaxIdentityBytes, "user_id");
            validate_mode(command.mode);
            break;
        case RaftCommandKind::kMatchFound:
            validate_string(command.match_id, kMaxIdentityBytes, "match_id");
            validate_mode(command.mode);
            validate_users(command.user_ids);
            break;
        case RaftCommandKind::kMatchPurge:
            validate_mode(command.mode);
            validate_users(command.user_ids);
            break;
        case RaftCommandKind::kLeaderboardSubmit:
            validate_string(command.user_id, kMaxIdentityBytes, "user_id");
            validate_string(command.display_name, kMaxDisplayNameBytes, "display_name", true);
            break;
    }
}

std::string mode_string(RaftMatchMode mode) {
    validate_mode(mode);
    switch (mode) {
        case RaftMatchMode::kOneVsOne:
            return "1v1";
        case RaftMatchMode::kTwoVsTwo:
            return "2v2";
        case RaftMatchMode::kFourVsFour:
            return "4v4";
    }
    command_error("match mode is unsupported");
}

RaftMatchMode parse_mode_string(const std::string& mode) {
    if (mode == "1v1")
        return RaftMatchMode::kOneVsOne;
    if (mode == "2v2")
        return RaftMatchMode::kTwoVsTwo;
    if (mode == "4v4")
        return RaftMatchMode::kFourVsFour;
    command_error("match mode is unsupported");
}

template <std::size_t N>
void require_exact_keys(const Json& value, const std::array<std::string_view, N>& keys) {
    if (!value.is_object() || value.size() != keys.size()) {
        command_error("legacy JSON contains missing or unknown fields");
    }
    for (const auto key : keys) {
        if (!value.contains(std::string(key))) {
            command_error("legacy JSON is missing " + std::string(key));
        }
    }
}

std::string require_string(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_string()) {
        command_error(std::string(key) + " must be a string");
    }
    return field.get<std::string>();
}

std::int64_t require_integer(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_number_integer()) {
        command_error(std::string(key) + " must be an integer");
    }
    try {
        return field.get<std::int64_t>();
    } catch (const Json::exception&) {
        command_error(std::string(key) + " is outside int64 range");
    }
}

std::uint64_t require_unsigned(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_number_unsigned()) {
        command_error(std::string(key) + " must be an unsigned integer");
    }
    return field.get<std::uint64_t>();
}

std::vector<std::string> require_users(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_array()) {
        command_error(std::string(key) + " must be an array");
    }
    std::vector<std::string> users;
    users.reserve(field.size());
    for (const auto& item : field) {
        if (!item.is_string()) {
            command_error(std::string(key) + " entries must be strings");
        }
        users.push_back(item.get<std::string>());
    }
    return users;
}

RaftCommand parse_legacy(const std::string& data) {
    Json document;
    try {
        document = Json::parse(data);
    } catch (const Json::exception& error) {
        command_error("malformed legacy JSON: " + std::string(error.what()));
    }
    if (!document.is_object() || !document.contains("v") ||
        !document.at("v").is_number_unsigned() || document.at("v").get<std::uint64_t>() != 1) {
        command_error("unsupported legacy command version");
    }
    const auto op = require_string(document, "op");
    RaftCommand result;
    if (op == "match_join") {
        require_exact_keys(document,
                           std::array<std::string_view, 6>{"v", "op", "user_id", "mmr",
                                                                   "queued_at_ms", "mode"});
        result.kind = RaftCommandKind::kMatchJoin;
        result.user_id = require_string(document, "user_id");
        result.mmr = require_integer(document, "mmr");
        result.queued_at_ms = require_unsigned(document, "queued_at_ms");
        result.mode = parse_mode_string(require_string(document, "mode"));
    } else if (op == "match_leave") {
        require_exact_keys(document,
                           std::array<std::string_view, 4>{"v", "op", "user_id", "mode"});
        result.kind = RaftCommandKind::kMatchLeave;
        result.user_id = require_string(document, "user_id");
        result.mode = parse_mode_string(require_string(document, "mode"));
    } else if (op == "match_found") {
        require_exact_keys(document,
                           std::array<std::string_view, 6>{"v", "op", "match_id", "mode",
                                                                   "player_ids", "avg_mmr"});
        result.kind = RaftCommandKind::kMatchFound;
        result.match_id = require_string(document, "match_id");
        result.mode = parse_mode_string(require_string(document, "mode"));
        result.user_ids = require_users(document, "player_ids");
        result.avg_mmr = require_integer(document, "avg_mmr");
    } else if (op == "match_purge") {
        require_exact_keys(document,
                           std::array<std::string_view, 4>{"v", "op", "mode", "user_ids"});
        result.kind = RaftCommandKind::kMatchPurge;
        result.mode = parse_mode_string(require_string(document, "mode"));
        result.user_ids = require_users(document, "user_ids");
    } else if (op == "leaderboard_submit") {
        require_exact_keys(document,
                           std::array<std::string_view, 5>{"v", "op", "user_id",
                                                                   "display_name", "score"});
        result.kind = RaftCommandKind::kLeaderboardSubmit;
        result.user_id = require_string(document, "user_id");
        result.display_name = require_string(document, "display_name");
        result.score = require_integer(document, "score");
    } else {
        command_error("unknown legacy command operation");
    }
    validate_command(result);
    return result;
}

#ifdef BOOST_BUILD_RAFT_PROTOBUF
namespace command_proto = ::boost::gateway::v3::raft;

command_proto::MatchMode encode_mode(RaftMatchMode mode) {
    validate_mode(mode);
    switch (mode) {
        case RaftMatchMode::kOneVsOne:
            return command_proto::MATCH_MODE_ONE_V_ONE;
        case RaftMatchMode::kTwoVsTwo:
            return command_proto::MATCH_MODE_TWO_V_TWO;
        case RaftMatchMode::kFourVsFour:
            return command_proto::MATCH_MODE_FOUR_V_FOUR;
    }
    command_error("match mode is unsupported");
}

RaftMatchMode decode_mode(command_proto::MatchMode mode) {
    switch (mode) {
        case command_proto::MATCH_MODE_ONE_V_ONE:
            return RaftMatchMode::kOneVsOne;
        case command_proto::MATCH_MODE_TWO_V_TWO:
            return RaftMatchMode::kTwoVsTwo;
        case command_proto::MATCH_MODE_FOUR_V_FOUR:
            return RaftMatchMode::kFourVsFour;
        case command_proto::MATCH_MODE_UNSPECIFIED:
            break;
        default:
            break;
    }
    command_error("protobuf match mode is unsupported");
}

std::string encode_protobuf(const RaftCommand& command) {
    command_proto::CommandEnvelope envelope;
    envelope.set_command_version(kCommandVersion);
    switch (command.kind) {
        case RaftCommandKind::kMatchJoin: {
            auto* payload = envelope.mutable_match_join();
            payload->set_user_id(command.user_id);
            payload->set_mmr(command.mmr);
            payload->set_queued_at_ms(command.queued_at_ms);
            payload->set_mode(encode_mode(command.mode));
            break;
        }
        case RaftCommandKind::kMatchLeave: {
            auto* payload = envelope.mutable_match_leave();
            payload->set_user_id(command.user_id);
            payload->set_mode(encode_mode(command.mode));
            break;
        }
        case RaftCommandKind::kMatchFound: {
            auto* payload = envelope.mutable_match_found();
            payload->set_match_id(command.match_id);
            payload->set_mode(encode_mode(command.mode));
            payload->set_avg_mmr(command.avg_mmr);
            for (const auto& user : command.user_ids)
                payload->add_player_ids(user);
            break;
        }
        case RaftCommandKind::kMatchPurge: {
            auto* payload = envelope.mutable_match_purge();
            payload->set_mode(encode_mode(command.mode));
            for (const auto& user : command.user_ids)
                payload->add_user_ids(user);
            break;
        }
        case RaftCommandKind::kLeaderboardSubmit: {
            auto* payload = envelope.mutable_leaderboard_submit();
            payload->set_user_id(command.user_id);
            payload->set_display_name(command.display_name);
            payload->set_score(command.score);
            break;
        }
    }

    const auto size = envelope.ByteSizeLong();
    const RaftStateCodecLimits limits;
    if (size == 0 || size > limits.max_command_bytes - kCommandMagic.size() ||
        size > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        command_error("encoded protobuf is oversized");
    }
    std::string bytes(size, '\0');
    google::protobuf::io::ArrayOutputStream array_stream(bytes.data(), static_cast<int>(size));
    google::protobuf::io::CodedOutputStream coded_stream(&array_stream);
    coded_stream.SetSerializationDeterministic(true);
    if (!envelope.SerializeToCodedStream(&coded_stream) || coded_stream.HadError()) {
        command_error("protobuf serialization failed");
    }
    return std::string(kCommandMagic) + bytes;
}

RaftCommand parse_protobuf(const std::string& data) {
    command_proto::CommandEnvelope envelope;
    const auto payload_size = data.size() - kCommandMagic.size();
    if (payload_size > static_cast<std::size_t>(std::numeric_limits<int>::max()) ||
        !envelope.ParseFromArray(data.data() + kCommandMagic.size(),
                                 static_cast<int>(payload_size))) {
        command_error("malformed or truncated protobuf");
    }
    if (envelope.command_version() != kCommandVersion) {
        command_error("unsupported protobuf command version");
    }

    RaftCommand result;
    switch (envelope.payload_case()) {
        case command_proto::CommandEnvelope::kMatchJoin:
            result.kind = RaftCommandKind::kMatchJoin;
            result.user_id = envelope.match_join().user_id();
            result.mmr = envelope.match_join().mmr();
            result.queued_at_ms = envelope.match_join().queued_at_ms();
            result.mode = decode_mode(envelope.match_join().mode());
            break;
        case command_proto::CommandEnvelope::kMatchLeave:
            result.kind = RaftCommandKind::kMatchLeave;
            result.user_id = envelope.match_leave().user_id();
            result.mode = decode_mode(envelope.match_leave().mode());
            break;
        case command_proto::CommandEnvelope::kMatchFound:
            result.kind = RaftCommandKind::kMatchFound;
            result.match_id = envelope.match_found().match_id();
            result.mode = decode_mode(envelope.match_found().mode());
            result.avg_mmr = envelope.match_found().avg_mmr();
            result.user_ids.assign(envelope.match_found().player_ids().begin(),
                                   envelope.match_found().player_ids().end());
            break;
        case command_proto::CommandEnvelope::kMatchPurge:
            result.kind = RaftCommandKind::kMatchPurge;
            result.mode = decode_mode(envelope.match_purge().mode());
            result.user_ids.assign(envelope.match_purge().user_ids().begin(),
                                   envelope.match_purge().user_ids().end());
            break;
        case command_proto::CommandEnvelope::kLeaderboardSubmit:
            result.kind = RaftCommandKind::kLeaderboardSubmit;
            result.user_id = envelope.leaderboard_submit().user_id();
            result.display_name = envelope.leaderboard_submit().display_name();
            result.score = envelope.leaderboard_submit().score();
            break;
        case command_proto::CommandEnvelope::PAYLOAD_NOT_SET:
            command_error("protobuf envelope has no payload");
    }
    validate_command(result);
    return result;
}
#endif

} // namespace

RaftWireFormat detect_raft_command_format(const std::string& data) {
    const RaftStateCodecLimits limits;
    if (data.empty() || data.size() > limits.max_command_bytes) {
        command_error("payload size is outside the supported range");
    }
    if (data.size() > kCommandMagic.size() &&
        std::string_view(data.data(), kCommandMagic.size()) == kCommandMagic) {
        return RaftWireFormat::kProtobufV1;
    }
    const auto first = std::find_if_not(data.begin(), data.end(), [](unsigned char value) {
        return std::isspace(value) != 0;
    });
    if (first != data.end() && *first == '{') {
        return RaftWireFormat::kLegacyJson;
    }
    command_error("unknown command format");
}

std::string serialize_raft_command(const RaftCommand& command, RaftWireFormat format) {
    validate_command(command);
    if (format == RaftWireFormat::kProtobufV1) {
#ifdef BOOST_BUILD_RAFT_PROTOBUF
        return encode_protobuf(command);
#else
        command_error("protobuf support is not available");
#endif
    }

    Json document{{"v", 1}};
    switch (command.kind) {
        case RaftCommandKind::kMatchJoin:
            document.update({{"op", "match_join"},
                             {"user_id", command.user_id},
                             {"mmr", command.mmr},
                             {"queued_at_ms", command.queued_at_ms},
                             {"mode", mode_string(command.mode)}});
            break;
        case RaftCommandKind::kMatchLeave:
            document.update({{"op", "match_leave"},
                             {"user_id", command.user_id},
                             {"mode", mode_string(command.mode)}});
            break;
        case RaftCommandKind::kMatchFound:
            document.update({{"op", "match_found"},
                             {"match_id", command.match_id},
                             {"mode", mode_string(command.mode)},
                             {"player_ids", command.user_ids},
                             {"avg_mmr", command.avg_mmr}});
            break;
        case RaftCommandKind::kMatchPurge:
            document.update({{"op", "match_purge"},
                             {"mode", mode_string(command.mode)},
                             {"user_ids", command.user_ids}});
            break;
        case RaftCommandKind::kLeaderboardSubmit:
            document.update({{"op", "leaderboard_submit"},
                             {"user_id", command.user_id},
                             {"display_name", command.display_name},
                             {"score", command.score}});
            break;
    }
    return document.dump();
}

RaftCommand parse_raft_command(const std::string& data) {
    if (detect_raft_command_format(data) == RaftWireFormat::kLegacyJson) {
        return parse_legacy(data);
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    return parse_protobuf(data);
#else
    command_error("protobuf support is not available");
#endif
}

} // namespace v3::cluster
