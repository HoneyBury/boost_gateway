#pragma once
// v3.0.0 D4: Simplified Raft consensus for leader election.
// v3.2.0: Real inter-node RPC with JSON serialization.
// v3.4.0: In-memory log replication for singleton services.

#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <optional>
#include <random>
#include <string>
#include <thread>
#include <filesystem>
#include <unordered_map>
#include <vector>

namespace v3::cluster {

enum class RaftState : std::uint8_t {
    kFollower = 0,
    kCandidate = 1,
    kLeader = 2,
};

struct RaftNodeId {
    std::string id;
    std::string host;
    std::uint16_t port = 0;

    bool operator==(const RaftNodeId& o) const { return id == o.id; }
    bool operator<(const RaftNodeId& o) const { return id < o.id; }
};

struct LogEntry {
    std::uint64_t term = 0;
    std::string command;
};

struct RequestVoteArgs {
    std::uint64_t term = 0;
    std::string candidate_id;
    std::uint64_t last_log_term = 0;
    std::uint64_t last_log_index = 0;
};

struct RequestVoteReply {
    std::uint64_t term = 0;
    bool vote_granted = false;
};

struct AppendEntriesArgs {
    std::uint64_t term = 0;
    std::string leader_id;
    std::uint64_t prev_log_index = 0;
    std::uint64_t prev_log_term = 0;
    std::vector<LogEntry> entries;
    std::uint64_t leader_commit = 0;
};

struct AppendEntriesReply {
    std::uint64_t term = 0;
    bool success = false;
    std::uint64_t match_index = 0;
};

struct RaftConfig {
    std::string node_id;
    std::string storage_dir;
    std::chrono::milliseconds election_timeout_min{150};
    std::chrono::milliseconds election_timeout_max{300};
    std::chrono::milliseconds heartbeat_interval{50};
    std::vector<RaftNodeId> peers;
};

inline std::string serialize_request_vote(const RequestVoteArgs& args) {
    return nlohmann::json{
        {"type", "request_vote"},
        {"term", args.term},
        {"candidate_id", args.candidate_id},
        {"last_log_term", args.last_log_term},
        {"last_log_index", args.last_log_index},
    }.dump();
}

inline RequestVoteArgs parse_request_vote(const std::string& data) {
    auto j = nlohmann::json::parse(data);
    return {
        j.value("term", std::uint64_t{0}),
        j.value("candidate_id", std::string{}),
        j.value("last_log_term", std::uint64_t{0}),
        j.value("last_log_index", std::uint64_t{0}),
    };
}

inline std::string serialize_request_vote_reply(const RequestVoteReply& r) {
    return nlohmann::json{
        {"type", "request_vote_reply"},
        {"term", r.term},
        {"vote_granted", r.vote_granted},
    }.dump();
}

inline RequestVoteReply parse_request_vote_reply(const std::string& data) {
    auto j = nlohmann::json::parse(data);
    return {
        j.value("term", std::uint64_t{0}),
        j.value("vote_granted", false),
    };
}

inline std::string serialize_append_entries(const AppendEntriesArgs& args) {
    nlohmann::json entries = nlohmann::json::array();
    for (const auto& entry : args.entries) {
        entries.push_back({
            {"term", entry.term},
            {"command", entry.command},
        });
    }
    return nlohmann::json{
        {"type", "append_entries"},
        {"term", args.term},
        {"leader_id", args.leader_id},
        {"prev_log_index", args.prev_log_index},
        {"prev_log_term", args.prev_log_term},
        {"entries", std::move(entries)},
        {"leader_commit", args.leader_commit},
    }.dump();
}

inline AppendEntriesArgs parse_append_entries(const std::string& data) {
    auto j = nlohmann::json::parse(data);
    AppendEntriesArgs args;
    args.term = j.value("term", std::uint64_t{0});
    args.leader_id = j.value("leader_id", std::string{});
    args.prev_log_index = j.value("prev_log_index", std::uint64_t{0});
    args.prev_log_term = j.value("prev_log_term", std::uint64_t{0});
    args.leader_commit = j.value("leader_commit", std::uint64_t{0});
    if (j.contains("entries") && j["entries"].is_array()) {
        for (const auto& item : j["entries"]) {
            args.entries.push_back(LogEntry{
                .term = item.value("term", std::uint64_t{0}),
                .command = item.value("command", std::string{}),
            });
        }
    }
    return args;
}

inline std::string serialize_append_entries_reply(const AppendEntriesReply& r) {
    return nlohmann::json{
        {"type", "append_entries_reply"},
        {"term", r.term},
        {"success", r.success},
        {"match_index", r.match_index},
    }.dump();
}

inline AppendEntriesReply parse_append_entries_reply(const std::string& data) {
    auto j = nlohmann::json::parse(data);
    return {
        j.value("term", std::uint64_t{0}),
        j.value("success", false),
        j.value("match_index", std::uint64_t{0}),
    };
}

RequestVoteReply handle_request_vote_internal(
    const RaftNodeId& peer, const RequestVoteArgs& args);

class RaftNode {
public:
    using LeaderCallback = std::function<void()>;
    using StepDownCallback = std::function<void()>;
    using ApplyCallback = std::function<void(std::uint64_t index,
                                             const LogEntry& entry)>;
    using RpcSender = std::function<std::string(const RaftNodeId& target,
                                                const std::string& rpc_data)>;

    explicit RaftNode(RaftConfig config);
    ~RaftNode();

    RaftNode(const RaftNode&) = delete;
    RaftNode& operator=(const RaftNode&) = delete;

    void start();
    void stop();

    [[nodiscard]] RaftState state() const;
    [[nodiscard]] bool is_leader() const { return state() == RaftState::kLeader; }
    [[nodiscard]] std::uint64_t current_term() const;
    [[nodiscard]] std::string leader_id() const;
    [[nodiscard]] std::uint64_t last_log_index() const;
    [[nodiscard]] std::uint64_t last_log_term() const;
    [[nodiscard]] std::uint64_t commit_index() const;
    [[nodiscard]] std::uint64_t last_applied() const;
    [[nodiscard]] std::size_t log_size() const;
    [[nodiscard]] std::string log_command(std::uint64_t index) const;

    void on_become_leader(LeaderCallback cb) { leader_cb_ = std::move(cb); }
    void on_step_down(StepDownCallback cb) { step_down_cb_ = std::move(cb); }
    void on_apply(ApplyCallback cb);

    RequestVoteReply handle_request_vote(const RequestVoteArgs& args);
    AppendEntriesReply handle_append_entries(const AppendEntriesArgs& args);
    void set_rpc_sender(RpcSender sender) { rpc_sender_ = std::move(sender); }

    [[nodiscard]] bool append_command(const std::string& command);

    [[nodiscard]] std::size_t quorum_size() const { return (peers_.size() / 2) + 1; }
    [[nodiscard]] std::size_t peer_count() const { return peers_.size(); }

private:
    void run();
    void reset_election_timeout();
    void start_election();
    void send_heartbeat();
    void become_follower(std::uint64_t term);
    void become_leader();
    [[nodiscard]] bool is_log_up_to_date(std::uint64_t last_log_term,
                                         std::uint64_t last_log_index) const;
    [[nodiscard]] AppendEntriesArgs make_append_entries_for(
        const std::string& peer_id) const;
    void update_commit_index_locked();
    void drain_committed_entries_locked(
        std::vector<std::pair<std::uint64_t, LogEntry>>& applied);
    void deliver_applied_entries(
        const std::vector<std::pair<std::uint64_t, LogEntry>>& applied) const;
    void load_persistent_state();
    void persist_state_locked() const;
    [[nodiscard]] std::filesystem::path state_path() const;

    RaftConfig config_;
    std::vector<RaftNodeId> peers_;
    std::atomic<bool> running_{false};
    std::thread thread_;

    mutable std::mutex mutex_;
    std::uint64_t current_term_ = 0;
    std::optional<std::string> voted_for_;
    RaftState state_ = RaftState::kFollower;
    std::string leader_id_;
    std::vector<LogEntry> log_;

    std::chrono::steady_clock::time_point election_deadline_;
    std::mt19937 rng_;
    std::uint64_t votes_received_ = 0;
    std::uint64_t commit_index_ = 0;
    std::uint64_t last_applied_ = 0;
    std::unordered_map<std::string, std::uint64_t> next_index_;
    std::unordered_map<std::string, std::uint64_t> match_index_;

    RpcSender rpc_sender_;
    LeaderCallback leader_cb_;
    StepDownCallback step_down_cb_;
    ApplyCallback apply_cb_;
};

}  // namespace v3::cluster
