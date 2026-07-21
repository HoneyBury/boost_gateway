#include "v3/cluster/raft.h"

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

constexpr std::string_view kProtobufMagic{"BGRT", 4};
constexpr std::uint32_t kProtocolVersion = 1;
constexpr std::size_t kMaxWireBytes = 64U * 1024U * 1024U;

[[noreturn]] void wire_error(const std::string& detail) {
    throw std::invalid_argument("invalid Raft wire payload: " + detail);
}

void validate_wire_size(std::size_t size) {
    if (size == 0 || size > kMaxWireBytes) {
        wire_error("payload size is outside the supported range");
    }
}

void validate_node_id(const std::string& node_id) {
    const RaftStateCodecLimits limits;
    if (node_id.empty() || node_id.size() > limits.max_node_id_bytes ||
        node_id.find('\0') != std::string::npos) {
        wire_error("node ID is empty or oversized");
    }
}

void validate_request_vote_fields(const RequestVoteArgs& args) {
    validate_node_id(args.candidate_id);
    if (args.term == 0 || args.last_log_term > args.term ||
        ((args.last_log_index == 0) != (args.last_log_term == 0))) {
        wire_error("RequestVote term or log position is invalid");
    }
}

void validate_append_entries_fields(const AppendEntriesArgs& args) {
    const RaftStateCodecLimits limits;
    validate_node_id(args.leader_id);
    if (args.term == 0 || args.prev_log_term > args.term ||
        ((args.prev_log_index == 0) != (args.prev_log_term == 0)) ||
        args.prev_log_index > limits.max_log_entries ||
        args.entries.size() > limits.max_log_entries - args.prev_log_index) {
        wire_error("AppendEntries term, log position, or entry count is invalid");
    }
    for (const auto& entry : args.entries) {
        if (entry.term == 0 || entry.term > args.term ||
            entry.command.size() > limits.max_command_bytes) {
            wire_error("AppendEntries contains an invalid log entry");
        }
    }
}

template <std::size_t N>
void require_exact_keys(const Json& value, const std::array<std::string_view, N>& keys,
                        std::string_view message_name) {
    if (!value.is_object() || value.size() != keys.size()) {
        wire_error(std::string(message_name) + " contains missing or unknown fields");
    }
    for (const auto key : keys) {
        if (!value.contains(std::string(key))) {
            wire_error(std::string(message_name) + " is missing " + std::string(key));
        }
    }
}

std::uint64_t require_unsigned(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_number_unsigned()) {
        wire_error(std::string(key) + " must be an unsigned integer");
    }
    return field.get<std::uint64_t>();
}

std::string require_string(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_string()) {
        wire_error(std::string(key) + " must be a string");
    }
    return field.get<std::string>();
}

bool require_boolean(const Json& value, std::string_view key) {
    const auto& field = value.at(std::string(key));
    if (!field.is_boolean()) {
        wire_error(std::string(key) + " must be a boolean");
    }
    return field.get<bool>();
}

Json parse_legacy_json(const std::string& data) {
    validate_wire_size(data.size());
    try {
        auto document = Json::parse(data);
        if (!document.is_object()) {
            wire_error("legacy JSON root must be an object");
        }
        return document;
    } catch (const Json::exception& error) {
        wire_error("malformed legacy JSON: " + std::string(error.what()));
    }
}

void require_legacy_type(const Json& document, std::string_view expected) {
    if (require_string(document, "type") != expected) {
        wire_error("legacy JSON message type does not match the requested decoder");
    }
}

#ifdef BOOST_BUILD_RAFT_PROTOBUF

namespace wire_proto = ::boost::gateway::v3::raft;

std::string encode_protobuf(const wire_proto::WireEnvelope& envelope) {
    const auto encoded_size = envelope.ByteSizeLong();
    if (encoded_size == 0 || encoded_size > kMaxWireBytes - kProtobufMagic.size() ||
        encoded_size > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        wire_error("encoded protobuf size is outside the supported range");
    }

    std::string protobuf_bytes(encoded_size, '\0');
    google::protobuf::io::ArrayOutputStream array_stream(protobuf_bytes.data(),
                                                         static_cast<int>(encoded_size));
    google::protobuf::io::CodedOutputStream coded_stream(&array_stream);
    coded_stream.SetSerializationDeterministic(true);
    if (!envelope.SerializeToCodedStream(&coded_stream) || coded_stream.HadError()) {
        wire_error("protobuf serialization failed");
    }
    std::string framed(kProtobufMagic);
    framed += protobuf_bytes;
    return framed;
}

wire_proto::WireEnvelope parse_protobuf_envelope(const std::string& data) {
    validate_wire_size(data.size());
    if (data.size() <= kProtobufMagic.size() ||
        std::string_view(data.data(), kProtobufMagic.size()) != kProtobufMagic) {
        wire_error("protobuf framing magic is missing");
    }
    const auto payload_size = data.size() - kProtobufMagic.size();
    if (payload_size > static_cast<std::size_t>(std::numeric_limits<int>::max())) {
        wire_error("protobuf payload is too large");
    }

    wire_proto::WireEnvelope envelope;
    if (!envelope.ParseFromArray(data.data() + kProtobufMagic.size(),
                                 static_cast<int>(payload_size))) {
        wire_error("malformed or truncated protobuf payload");
    }
    if (envelope.protocol_version() != kProtocolVersion) {
        wire_error("unsupported protobuf protocol version");
    }
    if (envelope.payload_case() == wire_proto::WireEnvelope::PAYLOAD_NOT_SET) {
        wire_error("protobuf envelope has no payload");
    }
    return envelope;
}

template <typename Message>
void fill_request_vote(Message* output, const RequestVoteArgs& args) {
    output->set_term(args.term);
    output->set_candidate_id(args.candidate_id);
    output->set_last_log_term(args.last_log_term);
    output->set_last_log_index(args.last_log_index);
}

template <typename Message>
void fill_append_entries(Message* output, const AppendEntriesArgs& args) {
    output->set_term(args.term);
    output->set_leader_id(args.leader_id);
    output->set_prev_log_index(args.prev_log_index);
    output->set_prev_log_term(args.prev_log_term);
    output->set_leader_commit(args.leader_commit);
    for (const auto& entry : args.entries) {
        auto* encoded_entry = output->add_entries();
        encoded_entry->set_term(entry.term);
        encoded_entry->set_command(entry.command);
    }
}

#endif

} // namespace

RaftWireFormat detect_raft_wire_format(const std::string& data) {
    validate_wire_size(data.size());
    if (data.size() >= kProtobufMagic.size() &&
        std::string_view(data.data(), kProtobufMagic.size()) == kProtobufMagic) {
        return RaftWireFormat::kProtobufV1;
    }
    const auto first = std::find_if_not(data.begin(), data.end(), [](unsigned char value) {
        return std::isspace(value) != 0;
    });
    if (first != data.end() && *first == '{') {
        return RaftWireFormat::kLegacyJson;
    }
    wire_error("unknown wire format");
}

RaftRpcKind detect_raft_rpc_kind(const std::string& data) {
    if (detect_raft_wire_format(data) == RaftWireFormat::kLegacyJson) {
        const auto type = require_string(parse_legacy_json(data), "type");
        if (type == "request_vote")
            return RaftRpcKind::kRequestVote;
        if (type == "request_vote_reply")
            return RaftRpcKind::kRequestVoteReply;
        if (type == "append_entries")
            return RaftRpcKind::kAppendEntries;
        if (type == "append_entries_reply")
            return RaftRpcKind::kAppendEntriesReply;
        wire_error("unknown legacy JSON message type");
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    const auto envelope = parse_protobuf_envelope(data);
    switch (envelope.payload_case()) {
        case wire_proto::WireEnvelope::kRequestVote:
            return RaftRpcKind::kRequestVote;
        case wire_proto::WireEnvelope::kRequestVoteReply:
            return RaftRpcKind::kRequestVoteReply;
        case wire_proto::WireEnvelope::kAppendEntries:
            return RaftRpcKind::kAppendEntries;
        case wire_proto::WireEnvelope::kAppendEntriesReply:
            return RaftRpcKind::kAppendEntriesReply;
        case wire_proto::WireEnvelope::kCapabilityRequest:
            return RaftRpcKind::kCapabilityRequest;
        case wire_proto::WireEnvelope::kCapabilityReply:
            return RaftRpcKind::kCapabilityReply;
        case wire_proto::WireEnvelope::PAYLOAD_NOT_SET:
            break;
    }
#endif
    wire_error("protobuf support is not available");
}

const char* raft_rpc_message_type(RaftRpcKind kind) {
    switch (kind) {
        case RaftRpcKind::kRequestVote:
            return "raft_request_vote";
        case RaftRpcKind::kAppendEntries:
            return "raft_append_entries";
        case RaftRpcKind::kCapabilityRequest:
            return "raft_capabilities";
        case RaftRpcKind::kRequestVoteReply:
        case RaftRpcKind::kAppendEntriesReply:
        case RaftRpcKind::kCapabilityReply:
            break;
    }
    wire_error("only Raft request payloads have backend message types");
}

std::string serialize_request_vote(const RequestVoteArgs& args, RaftWireFormat format) {
    validate_request_vote_fields(args);
    if (format == RaftWireFormat::kLegacyJson) {
        return Json{{"type", "request_vote"},
                    {"term", args.term},
                    {"candidate_id", args.candidate_id},
                    {"last_log_term", args.last_log_term},
                    {"last_log_index", args.last_log_index}}
            .dump();
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    wire_proto::WireEnvelope envelope;
    envelope.set_protocol_version(kProtocolVersion);
    fill_request_vote(envelope.mutable_request_vote(), args);
    return encode_protobuf(envelope);
#else
    wire_error("protobuf support is not available");
#endif
}

RequestVoteArgs parse_request_vote(const std::string& data) {
    RequestVoteArgs args;
    if (detect_raft_wire_format(data) == RaftWireFormat::kLegacyJson) {
        const auto document = parse_legacy_json(data);
        constexpr std::array<std::string_view, 5> keys{
            "type", "term", "candidate_id", "last_log_term", "last_log_index"};
        require_exact_keys(document, keys, "RequestVote");
        require_legacy_type(document, "request_vote");
        args = RequestVoteArgs{.term = require_unsigned(document, "term"),
                               .candidate_id = require_string(document, "candidate_id"),
                               .last_log_term = require_unsigned(document, "last_log_term"),
                               .last_log_index = require_unsigned(document, "last_log_index")};
    } else {
#ifdef BOOST_BUILD_RAFT_PROTOBUF
        const auto envelope = parse_protobuf_envelope(data);
        if (envelope.payload_case() != wire_proto::WireEnvelope::kRequestVote) {
            wire_error("protobuf payload is not RequestVote");
        }
        const auto& input = envelope.request_vote();
        args = RequestVoteArgs{.term = input.term(),
                               .candidate_id = input.candidate_id(),
                               .last_log_term = input.last_log_term(),
                               .last_log_index = input.last_log_index()};
#else
        wire_error("protobuf support is not available");
#endif
    }
    validate_request_vote_fields(args);
    return args;
}

std::string serialize_request_vote_reply(const RequestVoteReply& reply, RaftWireFormat format) {
    if (format == RaftWireFormat::kLegacyJson) {
        return Json{{"type", "request_vote_reply"},
                    {"term", reply.term},
                    {"vote_granted", reply.vote_granted}}
            .dump();
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    wire_proto::WireEnvelope envelope;
    envelope.set_protocol_version(kProtocolVersion);
    auto* output = envelope.mutable_request_vote_reply();
    output->set_term(reply.term);
    output->set_vote_granted(reply.vote_granted);
    return encode_protobuf(envelope);
#else
    wire_error("protobuf support is not available");
#endif
}

RequestVoteReply parse_request_vote_reply(const std::string& data) {
    if (detect_raft_wire_format(data) == RaftWireFormat::kLegacyJson) {
        const auto document = parse_legacy_json(data);
        constexpr std::array<std::string_view, 3> keys{"type", "term", "vote_granted"};
        require_exact_keys(document, keys, "RequestVoteReply");
        require_legacy_type(document, "request_vote_reply");
        return RequestVoteReply{.term = require_unsigned(document, "term"),
                                .vote_granted = require_boolean(document, "vote_granted")};
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    const auto envelope = parse_protobuf_envelope(data);
    if (envelope.payload_case() != wire_proto::WireEnvelope::kRequestVoteReply) {
        wire_error("protobuf payload is not RequestVoteReply");
    }
    return RequestVoteReply{.term = envelope.request_vote_reply().term(),
                            .vote_granted = envelope.request_vote_reply().vote_granted()};
#else
    wire_error("protobuf support is not available");
#endif
}

std::string serialize_append_entries(const AppendEntriesArgs& args, RaftWireFormat format) {
    validate_append_entries_fields(args);
    if (format == RaftWireFormat::kLegacyJson) {
        Json entries = Json::array();
        for (const auto& entry : args.entries) {
            entries.push_back(Json{{"term", entry.term}, {"command", entry.command}});
        }
        return Json{{"type", "append_entries"},
                    {"term", args.term},
                    {"leader_id", args.leader_id},
                    {"prev_log_index", args.prev_log_index},
                    {"prev_log_term", args.prev_log_term},
                    {"entries", std::move(entries)},
                    {"leader_commit", args.leader_commit}}
            .dump();
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    wire_proto::WireEnvelope envelope;
    envelope.set_protocol_version(kProtocolVersion);
    fill_append_entries(envelope.mutable_append_entries(), args);
    return encode_protobuf(envelope);
#else
    wire_error("protobuf support is not available");
#endif
}

AppendEntriesArgs parse_append_entries(const std::string& data) {
    AppendEntriesArgs args;
    if (detect_raft_wire_format(data) == RaftWireFormat::kLegacyJson) {
        const auto document = parse_legacy_json(data);
        constexpr std::array<std::string_view, 7> keys{
            "type", "term", "leader_id", "prev_log_index", "prev_log_term", "entries",
            "leader_commit"};
        require_exact_keys(document, keys, "AppendEntries");
        require_legacy_type(document, "append_entries");
        const auto& entries = document.at("entries");
        if (!entries.is_array()) {
            wire_error("entries must be an array");
        }
        args.term = require_unsigned(document, "term");
        args.leader_id = require_string(document, "leader_id");
        args.prev_log_index = require_unsigned(document, "prev_log_index");
        args.prev_log_term = require_unsigned(document, "prev_log_term");
        args.leader_commit = require_unsigned(document, "leader_commit");
        constexpr std::array<std::string_view, 2> entry_keys{"term", "command"};
        args.entries.reserve(entries.size());
        for (const auto& entry : entries) {
            require_exact_keys(entry, entry_keys, "LogEntry");
            args.entries.push_back(LogEntry{.term = require_unsigned(entry, "term"),
                                            .command = require_string(entry, "command")});
        }
    } else {
#ifdef BOOST_BUILD_RAFT_PROTOBUF
        const auto envelope = parse_protobuf_envelope(data);
        if (envelope.payload_case() != wire_proto::WireEnvelope::kAppendEntries) {
            wire_error("protobuf payload is not AppendEntries");
        }
        const auto& input = envelope.append_entries();
        args.term = input.term();
        args.leader_id = input.leader_id();
        args.prev_log_index = input.prev_log_index();
        args.prev_log_term = input.prev_log_term();
        args.leader_commit = input.leader_commit();
        args.entries.reserve(static_cast<std::size_t>(input.entries_size()));
        for (const auto& entry : input.entries()) {
            args.entries.push_back(LogEntry{.term = entry.term(), .command = entry.command()});
        }
#else
        wire_error("protobuf support is not available");
#endif
    }
    validate_append_entries_fields(args);
    return args;
}

std::string serialize_append_entries_reply(const AppendEntriesReply& reply,
                                           RaftWireFormat format) {
    if (format == RaftWireFormat::kLegacyJson) {
        return Json{{"type", "append_entries_reply"},
                    {"term", reply.term},
                    {"success", reply.success},
                    {"match_index", reply.match_index}}
            .dump();
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    wire_proto::WireEnvelope envelope;
    envelope.set_protocol_version(kProtocolVersion);
    auto* output = envelope.mutable_append_entries_reply();
    output->set_term(reply.term);
    output->set_success(reply.success);
    output->set_match_index(reply.match_index);
    return encode_protobuf(envelope);
#else
    wire_error("protobuf support is not available");
#endif
}

AppendEntriesReply parse_append_entries_reply(const std::string& data) {
    if (detect_raft_wire_format(data) == RaftWireFormat::kLegacyJson) {
        const auto document = parse_legacy_json(data);
        constexpr std::array<std::string_view, 4> keys{"type", "term", "success", "match_index"};
        require_exact_keys(document, keys, "AppendEntriesReply");
        require_legacy_type(document, "append_entries_reply");
        return AppendEntriesReply{.term = require_unsigned(document, "term"),
                                  .success = require_boolean(document, "success"),
                                  .match_index = require_unsigned(document, "match_index")};
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    const auto envelope = parse_protobuf_envelope(data);
    if (envelope.payload_case() != wire_proto::WireEnvelope::kAppendEntriesReply) {
        wire_error("protobuf payload is not AppendEntriesReply");
    }
    const auto& input = envelope.append_entries_reply();
    return AppendEntriesReply{.term = input.term(),
                              .success = input.success(),
                              .match_index = input.match_index()};
#else
    wire_error("protobuf support is not available");
#endif
}

std::string serialize_raft_capability_request(const RaftCapabilityRequest& request) {
    validate_node_id(request.node_id);
    if (std::find(request.supported_protocol_versions.begin(),
                  request.supported_protocol_versions.end(),
                  kProtocolVersion) == request.supported_protocol_versions.end()) {
        wire_error("capability request must advertise protobuf protocol version 1");
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    wire_proto::WireEnvelope envelope;
    envelope.set_protocol_version(kProtocolVersion);
    auto* output = envelope.mutable_capability_request();
    output->set_node_id(request.node_id);
    for (const auto version : request.supported_protocol_versions) {
        output->add_supported_protocol_versions(version);
    }
    return encode_protobuf(envelope);
#else
    wire_error("protobuf support is not available");
#endif
}

RaftCapabilityRequest parse_raft_capability_request(const std::string& data) {
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    const auto envelope = parse_protobuf_envelope(data);
    if (envelope.payload_case() != wire_proto::WireEnvelope::kCapabilityRequest) {
        wire_error("protobuf payload is not CapabilityRequest");
    }
    RaftCapabilityRequest request;
    request.node_id = envelope.capability_request().node_id();
    request.supported_protocol_versions.assign(
        envelope.capability_request().supported_protocol_versions().begin(),
        envelope.capability_request().supported_protocol_versions().end());
    validate_node_id(request.node_id);
    if (request.supported_protocol_versions.empty()) {
        wire_error("capability request has no supported protocol versions");
    }
    return request;
#else
    (void)data;
    wire_error("protobuf support is not available");
#endif
}

std::string serialize_raft_capability_reply(const RaftCapabilityReply& reply) {
    validate_node_id(reply.node_id);
    if (reply.protobuf_supported && reply.selected_protocol_version != kProtocolVersion) {
        wire_error("capability reply selected an unsupported protocol version");
    }
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    wire_proto::WireEnvelope envelope;
    envelope.set_protocol_version(kProtocolVersion);
    auto* output = envelope.mutable_capability_reply();
    output->set_node_id(reply.node_id);
    output->set_selected_protocol_version(reply.selected_protocol_version);
    output->set_protobuf_supported(reply.protobuf_supported);
    return encode_protobuf(envelope);
#else
    wire_error("protobuf support is not available");
#endif
}

RaftCapabilityReply parse_raft_capability_reply(const std::string& data) {
#ifdef BOOST_BUILD_RAFT_PROTOBUF
    const auto envelope = parse_protobuf_envelope(data);
    if (envelope.payload_case() != wire_proto::WireEnvelope::kCapabilityReply) {
        wire_error("protobuf payload is not CapabilityReply");
    }
    RaftCapabilityReply reply{.node_id = envelope.capability_reply().node_id(),
                              .selected_protocol_version =
                                  envelope.capability_reply().selected_protocol_version(),
                              .protobuf_supported =
                                  envelope.capability_reply().protobuf_supported()};
    validate_node_id(reply.node_id);
    if ((reply.protobuf_supported && reply.selected_protocol_version != kProtocolVersion) ||
        (!reply.protobuf_supported && reply.selected_protocol_version != 0)) {
        wire_error("capability reply is internally inconsistent");
    }
    return reply;
#else
    (void)data;
    wire_error("protobuf support is not available");
#endif
}

} // namespace v3::cluster
