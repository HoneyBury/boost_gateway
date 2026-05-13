#pragma once
// v3.0.0 D4: Simplified Raft consensus for leader election.
// Used for global singleton services (matchmaking, leaderboard).
// Implements leader election + heartbeat; log replication is future scope.

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <mutex>
#include <random>
#include <string>
#include <thread>
#include <vector>

namespace v3::cluster {

// ── Raft types ─────────────────────────────────────────────────────────

enum class RaftState : std::uint8_t {
    kFollower = 0,
    kCandidate = 1,
    kLeader = 2,
};

struct RaftNodeId {
    std::string id;    // unique node identifier
    std::string host;  // for RPC target
    std::uint16_t port = 0;

    bool operator==(const RaftNodeId& o) const { return id == o.id; }
    bool operator<(const RaftNodeId& o) const { return id < o.id; }
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
    // Log entries omitted (leader election only for v3.0.0)
};

struct AppendEntriesReply {
    std::uint64_t term = 0;
    bool success = false;
};

// ── Raft config ────────────────────────────────────────────────────────

struct RaftConfig {
    std::string node_id;
    std::chrono::milliseconds election_timeout_min{150};
    std::chrono::milliseconds election_timeout_max{300};
    std::chrono::milliseconds heartbeat_interval{50};
    std::vector<RaftNodeId> peers;  // all cluster members (including self)
};

// ── Forward declaration ───────────────────────────────────────────────

RequestVoteReply handle_request_vote_internal(
    const RaftNodeId& peer, const RequestVoteArgs& args);

// ── Raft Node ─────────────────────────────────────────────────────────

class RaftNode {
public:
    /// Called when this node becomes leader.
    using LeaderCallback = std::function<void()>;
    /// Called when this node steps down from leader.
    using StepDownCallback = std::function<void()>;
    /// Send RPC to a peer. Returns reply payload (caller parses).
    using RpcSender = std::function<std::string(const RaftNodeId& target,
                                                 const std::string& rpc_data)>;

    explicit RaftNode(RaftConfig config);
    ~RaftNode();

    RaftNode(const RaftNode&) = delete;
    RaftNode& operator=(const RaftNode&) = delete;

    // ── Lifecycle ────────────────────────────────────────────────────

    void start();
    void stop();

    // ── State ────────────────────────────────────────────────────────

    [[nodiscard]] RaftState state() const;
    [[nodiscard]] bool is_leader() const { return state() == RaftState::kLeader; }
    [[nodiscard]] std::uint64_t current_term() const;
    [[nodiscard]] std::string leader_id() const;

    // ── Callbacks ────────────────────────────────────────────────────

    void on_become_leader(LeaderCallback cb) { leader_cb_ = std::move(cb); }
    void on_step_down(StepDownCallback cb) { step_down_cb_ = std::move(cb); }

    // ── RPC ──────────────────────────────────────────────────────────

    /// Process an incoming RequestVote RPC.
    RequestVoteReply handle_request_vote(const RequestVoteArgs& args);

    /// Process an incoming AppendEntries (heartbeat) RPC.
    AppendEntriesReply handle_append_entries(const AppendEntriesArgs& args);

    /// Set the function used to send RPCs to peers.
    void set_rpc_sender(RpcSender sender) { rpc_sender_ = std::move(sender); }

    // ── Quorum ───────────────────────────────────────────────────────

    [[nodiscard]] std::size_t quorum_size() const { return (peers_.size() / 2) + 1; }
    [[nodiscard]] std::size_t peer_count() const { return peers_.size(); }

private:
    void run();
    void reset_election_timeout();
    void start_election();
    void send_heartbeat();
    void become_follower(std::uint64_t term);
    void become_candidate();
    void become_leader();

    RaftConfig config_;
    std::vector<RaftNodeId> peers_;
    std::atomic<bool> running_{false};
    std::thread thread_;

    // Persistent state
    mutable std::mutex mutex_;
    std::uint64_t current_term_ = 0;
    std::optional<std::string> voted_for_;
    RaftState state_ = RaftState::kFollower;
    std::string leader_id_;

    // Volatile state
    std::chrono::steady_clock::time_point election_deadline_;
    std::mt19937 rng_;
    std::uint64_t votes_received_ = 0;

    // RPC
    RpcSender rpc_sender_;
    LeaderCallback leader_cb_;
    StepDownCallback step_down_cb_;
};

}  // namespace v3::cluster
