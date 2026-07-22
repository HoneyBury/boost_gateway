#pragma once
// v3.0.0 D4: Simplified Raft consensus for leader election.
// v3.2.0: Real inter-node RPC with a versioned compatibility codec.
// v3.4.0: In-memory log replication for singleton services.

#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <mutex>
#include <optional>
#include <random>
#include <string>
#include <thread>
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
    bool protobuf_writer_enabled = false;
    std::vector<RaftNodeId> peers;
};

enum class RaftWireFormat : std::uint8_t {
    kLegacyJson = 0,
    kProtobufV1 = 1,
};

enum class RaftRpcKind : std::uint8_t {
    kRequestVote = 0,
    kRequestVoteReply = 1,
    kAppendEntries = 2,
    kAppendEntriesReply = 3,
    kCapabilityRequest = 4,
    kCapabilityReply = 5,
};

struct RaftCapabilityRequest {
    std::string node_id;
    std::vector<std::uint32_t> supported_protocol_versions;
};

struct RaftCapabilityReply {
    std::string node_id;
    std::uint32_t selected_protocol_version = 0;
    bool protobuf_supported = false;
};

[[nodiscard]] std::string serialize_request_vote(
    const RequestVoteArgs& args, RaftWireFormat format = RaftWireFormat::kLegacyJson);
[[nodiscard]] RequestVoteArgs parse_request_vote(const std::string& data);
[[nodiscard]] std::string serialize_request_vote_reply(
    const RequestVoteReply& reply, RaftWireFormat format = RaftWireFormat::kLegacyJson);
[[nodiscard]] RequestVoteReply parse_request_vote_reply(const std::string& data);
[[nodiscard]] std::string serialize_append_entries(
    const AppendEntriesArgs& args, RaftWireFormat format = RaftWireFormat::kLegacyJson);
[[nodiscard]] AppendEntriesArgs parse_append_entries(const std::string& data);
[[nodiscard]] std::string serialize_append_entries_reply(
    const AppendEntriesReply& reply, RaftWireFormat format = RaftWireFormat::kLegacyJson);
[[nodiscard]] AppendEntriesReply parse_append_entries_reply(const std::string& data);
[[nodiscard]] std::string serialize_raft_capability_request(
    const RaftCapabilityRequest& request);
[[nodiscard]] RaftCapabilityRequest parse_raft_capability_request(const std::string& data);
[[nodiscard]] std::string serialize_raft_capability_reply(const RaftCapabilityReply& reply);
[[nodiscard]] RaftCapabilityReply parse_raft_capability_reply(const std::string& data);
[[nodiscard]] RaftWireFormat detect_raft_wire_format(const std::string& data);
[[nodiscard]] RaftRpcKind detect_raft_rpc_kind(const std::string& data);
[[nodiscard]] const char* raft_rpc_message_type(RaftRpcKind kind);

RequestVoteReply handle_request_vote_internal(
    const RaftNodeId& peer, const RequestVoteArgs& args);

class StateMachine {
public:
    virtual ~StateMachine() = default;
    virtual bool apply(std::uint64_t index, const LogEntry& entry) = 0;
};

class RaftNode {
public:
    using LeaderCallback = std::function<void()>;
    using StepDownCallback = std::function<void()>;
    using ApplyCallback = std::function<bool(std::uint64_t index,
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
    [[nodiscard]] bool healthy() const;
    [[nodiscard]] std::string last_error() const;
    [[nodiscard]] std::optional<std::uint64_t> failed_apply_index() const;

    void on_become_leader(LeaderCallback cb) { leader_cb_ = std::move(cb); }
    void on_step_down(StepDownCallback cb) { step_down_cb_ = std::move(cb); }
    void on_apply(ApplyCallback cb);
    void set_state_machine(StateMachine* sm);

    RequestVoteReply handle_request_vote(const RequestVoteArgs& args);
    AppendEntriesReply handle_append_entries(const AppendEntriesArgs& args);
    RaftCapabilityReply handle_capability_request(const RaftCapabilityRequest& request);
    void set_rpc_sender(RpcSender sender) { rpc_sender_ = std::move(sender); }
    void refresh_peer_capabilities();
    [[nodiscard]] bool peer_supports_protobuf(const std::string& peer_id) const;
    [[nodiscard]] bool all_voting_peers_support_protobuf() const;
    [[nodiscard]] RaftWireFormat active_writer_format() const;

    [[nodiscard]] bool append_command(const std::string& command);

    [[nodiscard]] std::size_t quorum_size() const { return (peers_.size() / 2) + 1; }
    [[nodiscard]] std::size_t peer_count() const { return peers_.size(); }

private:
    void run();
    void reset_election_timeout();
    void start_election();
    void send_heartbeat();
    [[nodiscard]] bool become_follower(std::uint64_t term);
    [[nodiscard]] bool become_leader();
    [[nodiscard]] bool is_log_up_to_date(std::uint64_t last_log_term,
                                         std::uint64_t last_log_index) const;
    [[nodiscard]] AppendEntriesArgs make_append_entries_for(
        const std::string& peer_id) const;
    void update_commit_index_locked();
    void drain_committed_entries_locked(
        std::vector<std::pair<std::uint64_t, LogEntry>>& applied);
    [[nodiscard]] bool deliver_applied_entries(
        const std::vector<std::pair<std::uint64_t, LogEntry>>& applied);
    [[nodiscard]] RaftWireFormat active_writer_format_locked() const;
    void load_persistent_state();
    [[nodiscard]] bool persist_state_locked();
    void mark_unhealthy_locked(std::string reason);
    [[nodiscard]] std::filesystem::path state_path() const;

    RaftConfig config_;
    std::vector<RaftNodeId> peers_;
    std::atomic<bool> running_{false};
    std::thread thread_;

    mutable std::mutex mutex_;
    std::mutex apply_pipeline_mutex_;
    std::uint64_t current_term_ = 0;
    std::optional<std::string> voted_for_;
    RaftState state_ = RaftState::kFollower;
    std::string leader_id_;
    std::vector<LogEntry> log_;
    bool healthy_ = true;
    std::string last_error_;
    std::optional<std::uint64_t> failed_apply_index_;

    std::chrono::steady_clock::time_point election_deadline_;
    std::mt19937 rng_;
    std::uint64_t votes_received_ = 0;
    std::uint64_t commit_index_ = 0;
    std::uint64_t last_applied_ = 0;
    std::unordered_map<std::string, std::uint64_t> next_index_;
    std::unordered_map<std::string, std::uint64_t> match_index_;
    std::unordered_map<std::string, std::uint32_t> peer_protocol_versions_;

    RpcSender rpc_sender_;
    LeaderCallback leader_cb_;
    StepDownCallback step_down_cb_;
    ApplyCallback apply_cb_;
    StateMachine* state_machine_ = nullptr;
};

}  // namespace v3::cluster
