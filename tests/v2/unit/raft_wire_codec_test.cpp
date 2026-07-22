#include "v3/cluster/raft.h"

#include <gtest/gtest.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>

namespace {

using namespace v3::cluster;

std::string read_fixture(std::string_view filename) {
    const auto path = std::filesystem::path(PROJECT_SOURCE_DIR) / "tests" / "fixtures" / "raft" /
                      std::string(filename);
    std::ifstream input(path, std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("failed to open Raft fixture: " + path.string());
    }
    return {std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
}

std::string decode_hex(std::string encoded) {
    encoded.erase(std::remove_if(encoded.begin(), encoded.end(),
                                 [](unsigned char value) { return std::isspace(value) != 0; }),
                  encoded.end());
    if (encoded.size() % 2 != 0) {
        throw std::runtime_error("invalid odd-length hex fixture");
    }
    std::string decoded;
    decoded.reserve(encoded.size() / 2);
    for (std::size_t offset = 0; offset < encoded.size(); offset += 2) {
        decoded.push_back(static_cast<char>(std::stoul(encoded.substr(offset, 2), nullptr, 16)));
    }
    return decoded;
}

RequestVoteArgs sample_vote() {
    return RequestVoteArgs{
        .term = 7,
        .candidate_id = "node-a",
        .last_log_term = 6,
        .last_log_index = 9,
    };
}

AppendEntriesArgs sample_append() {
    return AppendEntriesArgs{
        .term = 7,
        .leader_id = "node-a",
        .prev_log_index = 1,
        .prev_log_term = 6,
        .entries = {{.term = 7, .command = "set:x"}},
        .leader_commit = 2,
    };
}

TEST(RaftWireCodecTest, ReadsFrozenLegacyRequestVoteFixture) {
    const auto wire = read_fixture("request_vote_v0.json");
    EXPECT_EQ(detect_raft_wire_format(wire), RaftWireFormat::kLegacyJson);
    EXPECT_EQ(detect_raft_rpc_kind(wire), RaftRpcKind::kRequestVote);
    EXPECT_STREQ(raft_rpc_message_type(detect_raft_rpc_kind(wire)), "raft_request_vote");

    const auto decoded = parse_request_vote(wire);
    EXPECT_EQ(decoded.term, 7U);
    EXPECT_EQ(decoded.candidate_id, "node-a");
    EXPECT_EQ(decoded.last_log_term, 6U);
    EXPECT_EQ(decoded.last_log_index, 9U);
}

TEST(RaftWireCodecTest, ProtobufRequestVoteMatchesFrozenGoldenVector) {
    const auto golden = decode_hex(read_fixture("request_vote_v1.pb.hex"));
    const auto encoded = serialize_request_vote(sample_vote(), RaftWireFormat::kProtobufV1);
    EXPECT_EQ(encoded, golden);
    EXPECT_EQ(detect_raft_wire_format(encoded), RaftWireFormat::kProtobufV1);
    EXPECT_EQ(detect_raft_rpc_kind(encoded), RaftRpcKind::kRequestVote);

    const auto decoded = parse_request_vote(golden);
    EXPECT_EQ(decoded.term, 7U);
    EXPECT_EQ(decoded.candidate_id, "node-a");
    EXPECT_EQ(decoded.last_log_term, 6U);
    EXPECT_EQ(decoded.last_log_index, 9U);
}

TEST(RaftWireCodecTest, ProtobufAppendEntriesMatchesFrozenGoldenVector) {
    const auto golden = decode_hex(read_fixture("append_entries_v1.pb.hex"));
    const auto encoded = serialize_append_entries(sample_append(), RaftWireFormat::kProtobufV1);
    EXPECT_EQ(encoded, golden);
    EXPECT_EQ(detect_raft_rpc_kind(encoded), RaftRpcKind::kAppendEntries);

    const auto decoded = parse_append_entries(golden);
    EXPECT_EQ(decoded.term, 7U);
    EXPECT_EQ(decoded.leader_id, "node-a");
    ASSERT_EQ(decoded.entries.size(), 1U);
    EXPECT_EQ(decoded.entries.front().command, "set:x");
    EXPECT_EQ(decoded.leader_commit, 2U);
}

TEST(RaftWireCodecTest, RepliesRoundTripInBothFormats) {
    const RequestVoteReply vote_reply{.term = 8, .vote_granted = true};
    const AppendEntriesReply append_reply{.term = 8, .success = true, .match_index = 12};
    for (const auto format : {RaftWireFormat::kLegacyJson, RaftWireFormat::kProtobufV1}) {
        const auto decoded_vote =
            parse_request_vote_reply(serialize_request_vote_reply(vote_reply, format));
        EXPECT_EQ(decoded_vote.term, vote_reply.term);
        EXPECT_EQ(decoded_vote.vote_granted, vote_reply.vote_granted);

        const auto decoded_append =
            parse_append_entries_reply(serialize_append_entries_reply(append_reply, format));
        EXPECT_EQ(decoded_append.term, append_reply.term);
        EXPECT_EQ(decoded_append.success, append_reply.success);
        EXPECT_EQ(decoded_append.match_index, append_reply.match_index);
    }
}

TEST(RaftWireCodecTest, StrictLegacyReaderRejectsMissingUnknownAndWrongTypedFields) {
    EXPECT_THROW(
        static_cast<void>(parse_request_vote(
            R"({"type":"request_vote","term":7,"candidate_id":"node-a","last_log_term":6})")),
        std::invalid_argument);
    EXPECT_THROW(
        static_cast<void>(parse_request_vote(
            R"({"type":"request_vote","term":7,"candidate_id":"node-a","last_log_term":6,"last_log_index":9,"extra":true})")),
        std::invalid_argument);
    EXPECT_THROW(
        static_cast<void>(parse_request_vote(
            R"({"type":"request_vote","term":"7","candidate_id":"node-a","last_log_term":6,"last_log_index":9})")),
        std::invalid_argument);
}

TEST(RaftWireCodecTest, ProtobufReaderRejectsFutureTruncatedAndMissingIdentityPayloads) {
    auto future = decode_hex(read_fixture("request_vote_v1.pb.hex"));
    ASSERT_GT(future.size(), 5U);
    future[5] = '\x02';
    EXPECT_THROW(static_cast<void>(parse_request_vote(future)), std::invalid_argument);

    auto truncated = decode_hex(read_fixture("request_vote_v1.pb.hex"));
    truncated.pop_back();
    EXPECT_THROW(static_cast<void>(parse_request_vote(truncated)), std::invalid_argument);

    const auto missing_identity = decode_hex("4247525408015206080718062009");
    EXPECT_THROW(static_cast<void>(parse_request_vote(missing_identity)), std::invalid_argument);
}

TEST(RaftWireCodecTest, CapabilityRoundTripRequiresExplicitVersionSelection) {
    const RaftCapabilityRequest request{.node_id = "node-a", .supported_protocol_versions = {1U}};
    const auto encoded_request = serialize_raft_capability_request(request);
    EXPECT_EQ(detect_raft_rpc_kind(encoded_request), RaftRpcKind::kCapabilityRequest);
    EXPECT_STREQ(raft_rpc_message_type(detect_raft_rpc_kind(encoded_request)), "raft_capabilities");
    const auto decoded_request = parse_raft_capability_request(encoded_request);
    EXPECT_EQ(decoded_request.node_id, "node-a");
    EXPECT_EQ(decoded_request.supported_protocol_versions, std::vector<std::uint32_t>({1U}));

    const RaftCapabilityReply reply{
        .node_id = "node-b", .selected_protocol_version = 1U, .protobuf_supported = true};
    const auto decoded_reply = parse_raft_capability_reply(serialize_raft_capability_reply(reply));
    EXPECT_EQ(decoded_reply.node_id, "node-b");
    EXPECT_EQ(decoded_reply.selected_protocol_version, 1U);
    EXPECT_TRUE(decoded_reply.protobuf_supported);
}

TEST(RaftWireCodecTest, RuntimeWriterRemainsLegacyJsonByDefault) {
    const auto vote = serialize_request_vote(sample_vote());
    const auto append = serialize_append_entries(sample_append());
    EXPECT_EQ(detect_raft_wire_format(vote), RaftWireFormat::kLegacyJson);
    EXPECT_EQ(detect_raft_wire_format(append), RaftWireFormat::kLegacyJson);
}

TEST(RaftWireCodecTest, PeerCapabilityIsRecordedOnlyFromExplicitValidReply) {
    const std::vector<RaftNodeId> peers{{"node-a", "", 0}, {"node-b", "", 0}, {"node-c", "", 0}};
    RaftNode node(RaftConfig{.node_id = "node-a", .peers = peers});
    bool peer_c_replies = false;
    node.set_rpc_sender([&](const RaftNodeId& target, const std::string& payload) {
        EXPECT_EQ(detect_raft_rpc_kind(payload), RaftRpcKind::kCapabilityRequest);
        const auto request = parse_raft_capability_request(payload);
        EXPECT_EQ(request.node_id, "node-a");
        if (target.id == "node-c" && !peer_c_replies) {
            return std::string{};
        }
        return serialize_raft_capability_reply(RaftCapabilityReply{
            .node_id = target.id,
            .selected_protocol_version = 1U,
            .protobuf_supported = true,
        });
    });

    node.refresh_peer_capabilities();
    EXPECT_TRUE(node.peer_supports_protobuf("node-b"));
    EXPECT_FALSE(node.peer_supports_protobuf("node-c"));
    EXPECT_FALSE(node.all_voting_peers_support_protobuf());

    peer_c_replies = true;
    node.refresh_peer_capabilities();
    EXPECT_TRUE(node.peer_supports_protobuf("node-c"));
    EXPECT_TRUE(node.all_voting_peers_support_protobuf());

    peer_c_replies = false;
    node.refresh_peer_capabilities();
    EXPECT_FALSE(node.peer_supports_protobuf("node-c"));
    EXPECT_FALSE(node.all_voting_peers_support_protobuf());
}

TEST(RaftWireCodecTest, CapabilityRequestRejectsUnknownAndSelfNodes) {
    const std::vector<RaftNodeId> peers{{"node-a", "", 0}, {"node-b", "", 0}};
    RaftNode node(RaftConfig{.node_id = "node-a", .peers = peers});

    const auto unknown = node.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-x", .supported_protocol_versions = {1U}});
    EXPECT_EQ(unknown.selected_protocol_version, 0U);
    EXPECT_FALSE(unknown.protobuf_supported);
    EXPECT_FALSE(node.peer_supports_protobuf("node-x"));

    const auto self = node.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-a", .supported_protocol_versions = {1U}});
    EXPECT_EQ(self.selected_protocol_version, 0U);
    EXPECT_FALSE(self.protobuf_supported);

    const auto peer = node.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-b", .supported_protocol_versions = {1U}});
    EXPECT_EQ(peer.selected_protocol_version, 1U);
    EXPECT_TRUE(peer.protobuf_supported);
    EXPECT_TRUE(node.peer_supports_protobuf("node-b"));
}

TEST(RaftWireCodecTest, ProtobufWriterRequiresExplicitSwitchAndEveryPeerCapability) {
    const std::vector<RaftNodeId> peers{{"node-a", "", 0}, {"node-b", "", 0}, {"node-c", "", 0}};
    RaftNode disabled(RaftConfig{
        .node_id = "node-a",
        .protobuf_writer_enabled = false,
        .peers = peers,
    });
    EXPECT_EQ(disabled.active_writer_format(), RaftWireFormat::kLegacyJson);
    disabled.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-b", .supported_protocol_versions = {1U}});
    disabled.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-c", .supported_protocol_versions = {1U}});
    EXPECT_EQ(disabled.active_writer_format(), RaftWireFormat::kLegacyJson);

    RaftNode enabled(RaftConfig{
        .node_id = "node-a",
        .protobuf_writer_enabled = true,
        .peers = peers,
    });
    EXPECT_EQ(enabled.active_writer_format(), RaftWireFormat::kLegacyJson);
    enabled.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-b", .supported_protocol_versions = {1U}});
    EXPECT_EQ(enabled.active_writer_format(), RaftWireFormat::kLegacyJson);
    enabled.handle_capability_request(
        RaftCapabilityRequest{.node_id = "node-c", .supported_protocol_versions = {1U}});
    EXPECT_EQ(enabled.active_writer_format(), RaftWireFormat::kProtobufV1);
}

TEST(RaftWireCodecTest, ProtobufWriterFallsBackWhenCapabilityRefreshLosesPeer) {
    const std::vector<RaftNodeId> peers{{"node-a", "", 0}, {"node-b", "", 0}};
    RaftNode node(RaftConfig{
        .node_id = "node-a",
        .protobuf_writer_enabled = true,
        .peers = peers,
    });
    bool advertise = true;
    node.set_rpc_sender([&](const RaftNodeId& target, const std::string&) {
        if (!advertise)
            return std::string{};
        return serialize_raft_capability_reply(RaftCapabilityReply{
            .node_id = target.id,
            .selected_protocol_version = 1U,
            .protobuf_supported = true,
        });
    });
    node.refresh_peer_capabilities();
    EXPECT_EQ(node.active_writer_format(), RaftWireFormat::kProtobufV1);
    advertise = false;
    node.refresh_peer_capabilities();
    EXPECT_EQ(node.active_writer_format(), RaftWireFormat::kLegacyJson);
}

TEST(RaftWireCodecTest, EnabledClusterUsesProtobufForVoteAndAppendRpc) {
    const std::vector<RaftNodeId> peers{{"node-a", "", 0}, {"node-b", "", 0}, {"node-c", "", 0}};
    RaftNode node(RaftConfig{
        .node_id = "node-a",
        .election_timeout_min = std::chrono::milliseconds(20),
        .election_timeout_max = std::chrono::milliseconds(30),
        .heartbeat_interval = std::chrono::milliseconds(10),
        .protobuf_writer_enabled = true,
        .peers = peers,
    });
    std::atomic_bool saw_protobuf_vote{false};
    std::atomic_bool saw_protobuf_append{false};
    node.set_rpc_sender([&](const RaftNodeId& target, const std::string& payload) {
        switch (detect_raft_rpc_kind(payload)) {
            case RaftRpcKind::kCapabilityRequest:
                return serialize_raft_capability_reply(RaftCapabilityReply{
                    .node_id = target.id,
                    .selected_protocol_version = 1U,
                    .protobuf_supported = true,
                });
            case RaftRpcKind::kRequestVote: {
                const auto format = detect_raft_wire_format(payload);
                saw_protobuf_vote = format == RaftWireFormat::kProtobufV1;
                const auto request = parse_request_vote(payload);
                return serialize_request_vote_reply(
                    RequestVoteReply{.term = request.term, .vote_granted = true}, format);
            }
            case RaftRpcKind::kAppendEntries: {
                const auto format = detect_raft_wire_format(payload);
                saw_protobuf_append = format == RaftWireFormat::kProtobufV1;
                const auto request = parse_append_entries(payload);
                return serialize_append_entries_reply(
                    AppendEntriesReply{
                        .term = request.term,
                        .success = true,
                        .match_index = request.prev_log_index + request.entries.size(),
                    },
                    format);
            }
            case RaftRpcKind::kRequestVoteReply:
            case RaftRpcKind::kAppendEntriesReply:
            case RaftRpcKind::kCapabilityReply:
                return std::string{};
        }
        return std::string{};
    });

    node.refresh_peer_capabilities();
    ASSERT_EQ(node.active_writer_format(), RaftWireFormat::kProtobufV1);
    node.start();
    for (int attempt = 0; attempt < 50 && !node.is_leader(); ++attempt) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    ASSERT_TRUE(node.is_leader());
    EXPECT_TRUE(saw_protobuf_vote.load());
    EXPECT_TRUE(node.append_command("command-v1"));
    EXPECT_TRUE(saw_protobuf_append.load());
    node.stop();
}

} // namespace
