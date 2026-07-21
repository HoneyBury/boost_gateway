#pragma once

#include "v3/cluster/raft.h"

#include <cstdint>
#include <string>
#include <vector>

namespace v3::cluster {

enum class RaftCommandKind : std::uint8_t {
    kMatchJoin = 0,
    kMatchLeave = 1,
    kMatchFound = 2,
    kMatchPurge = 3,
    kLeaderboardSubmit = 4,
};

enum class RaftMatchMode : std::uint8_t {
    kOneVsOne = 1,
    kTwoVsTwo = 2,
    kFourVsFour = 3,
};

struct RaftCommand {
    RaftCommandKind kind = RaftCommandKind::kMatchJoin;
    std::string user_id;
    std::string display_name;
    std::string match_id;
    RaftMatchMode mode = RaftMatchMode::kOneVsOne;
    std::int64_t mmr = 0;
    std::uint64_t queued_at_ms = 0;
    std::int64_t avg_mmr = 0;
    std::int64_t score = 0;
    std::vector<std::string> user_ids;
};

[[nodiscard]] std::string serialize_raft_command(
    const RaftCommand& command,
    RaftWireFormat format = RaftWireFormat::kLegacyJson);
[[nodiscard]] RaftCommand parse_raft_command(const std::string& data);
[[nodiscard]] RaftWireFormat detect_raft_command_format(const std::string& data);

} // namespace v3::cluster
