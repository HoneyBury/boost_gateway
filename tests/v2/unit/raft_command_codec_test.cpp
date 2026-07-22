#include <gtest/gtest.h>

#include "v3/cluster/raft_command_codec.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

using namespace v3::cluster;

namespace {

std::string read_hex_fixture(const std::string& name) {
    const auto path = std::filesystem::path(PROJECT_SOURCE_DIR) / "tests" / "fixtures" /
                      "raft" / name;
    std::ifstream input(path);
    std::string hex{std::istreambuf_iterator<char>(input), std::istreambuf_iterator<char>()};
    hex.erase(std::remove_if(hex.begin(), hex.end(), [](unsigned char value) {
                  return std::isspace(value) != 0;
              }),
              hex.end());
    if (!input || hex.size() % 2 != 0) {
        throw std::runtime_error("invalid Raft command fixture: " + path.string());
    }
    std::string decoded;
    decoded.reserve(hex.size() / 2);
    for (std::size_t offset = 0; offset < hex.size(); offset += 2) {
        decoded.push_back(static_cast<char>(std::stoi(hex.substr(offset, 2), nullptr, 16)));
    }
    return decoded;
}

RaftCommand sample(RaftCommandKind kind) {
    RaftCommand command;
    command.kind = kind;
    command.user_id = "user-1";
    command.display_name = "Alice";
    command.match_id = "match-1";
    command.mode = RaftMatchMode::kTwoVsTwo;
    command.mmr = 1234;
    command.queued_at_ms = 9876;
    command.avg_mmr = 1200;
    command.score = 42;
    command.user_ids = {"user-1", "user-2", "user-3", "user-4"};
    return command;
}

void expect_equal(const RaftCommand& actual, const RaftCommand& expected) {
    EXPECT_EQ(actual.kind, expected.kind);
    switch (expected.kind) {
        case RaftCommandKind::kMatchJoin:
            EXPECT_EQ(actual.user_id, expected.user_id);
            EXPECT_EQ(actual.mode, expected.mode);
            EXPECT_EQ(actual.mmr, expected.mmr);
            EXPECT_EQ(actual.queued_at_ms, expected.queued_at_ms);
            break;
        case RaftCommandKind::kMatchLeave:
            EXPECT_EQ(actual.user_id, expected.user_id);
            EXPECT_EQ(actual.mode, expected.mode);
            break;
        case RaftCommandKind::kMatchFound:
            EXPECT_EQ(actual.match_id, expected.match_id);
            EXPECT_EQ(actual.mode, expected.mode);
            EXPECT_EQ(actual.user_ids, expected.user_ids);
            EXPECT_EQ(actual.avg_mmr, expected.avg_mmr);
            break;
        case RaftCommandKind::kMatchPurge:
            EXPECT_EQ(actual.mode, expected.mode);
            EXPECT_EQ(actual.user_ids, expected.user_ids);
            break;
        case RaftCommandKind::kLeaderboardSubmit:
            EXPECT_EQ(actual.user_id, expected.user_id);
            EXPECT_EQ(actual.display_name, expected.display_name);
            EXPECT_EQ(actual.score, expected.score);
            break;
    }
}

} // namespace

TEST(RaftCommandCodecTest, LegacyAndProtobufRoundTripEveryCommandKind) {
    constexpr std::array kinds{
        RaftCommandKind::kMatchJoin,
        RaftCommandKind::kMatchLeave,
        RaftCommandKind::kMatchFound,
        RaftCommandKind::kMatchPurge,
        RaftCommandKind::kLeaderboardSubmit,
    };
    for (const auto kind : kinds) {
        const auto command = sample(kind);
        for (const auto format : {RaftWireFormat::kLegacyJson, RaftWireFormat::kProtobufV1}) {
            const auto encoded = serialize_raft_command(command, format);
            EXPECT_EQ(detect_raft_command_format(encoded), format);
            expect_equal(parse_raft_command(encoded), command);
        }
    }
}

TEST(RaftCommandCodecTest, ProtobufEncodingIsDeterministic) {
    auto command = sample(RaftCommandKind::kLeaderboardSubmit);
    const auto first = serialize_raft_command(command, RaftWireFormat::kProtobufV1);
    const auto second = serialize_raft_command(command, RaftWireFormat::kProtobufV1);
    EXPECT_EQ(first, second);
    EXPECT_EQ(first, read_hex_fixture("leaderboard_submit_command_v1.pb.hex"));
}

TEST(RaftCommandCodecTest, StrictLegacyReaderRejectsUnknownVersionFieldsAndOperations) {
    EXPECT_THROW(static_cast<void>(parse_raft_command(
                     R"({"v":2,"op":"leaderboard_submit","user_id":"u","display_name":"","score":1})")),
                 std::invalid_argument);
    EXPECT_THROW(static_cast<void>(parse_raft_command(
                     R"({"v":1,"op":"leaderboard_submit","user_id":"u","display_name":"","score":1,"extra":true})")),
                 std::invalid_argument);
    EXPECT_THROW(static_cast<void>(parse_raft_command(R"({"v":1,"op":"unknown"})")),
                 std::invalid_argument);
}

TEST(RaftCommandCodecTest, ProtobufReaderRejectsFutureVersionAndTruncation) {
    auto encoded = serialize_raft_command(sample(RaftCommandKind::kLeaderboardSubmit),
                                          RaftWireFormat::kProtobufV1);
    ASSERT_GT(encoded.size(), 5U);
    encoded[5] = '\x02';
    EXPECT_THROW(static_cast<void>(parse_raft_command(encoded)), std::invalid_argument);

    encoded = serialize_raft_command(sample(RaftCommandKind::kMatchFound),
                                     RaftWireFormat::kProtobufV1);
    encoded.pop_back();
    EXPECT_THROW(static_cast<void>(parse_raft_command(encoded)), std::invalid_argument);
}

TEST(RaftCommandCodecTest, RejectsInvalidModesAndEmptyIdentityLists) {
    auto command = sample(RaftCommandKind::kMatchPurge);
    command.user_ids.clear();
    EXPECT_THROW(static_cast<void>(serialize_raft_command(command)), std::invalid_argument);
    command.user_ids = {"user-1"};
    command.mode = static_cast<RaftMatchMode>(99);
    EXPECT_THROW(static_cast<void>(serialize_raft_command(command)), std::invalid_argument);
}
