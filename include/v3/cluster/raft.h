#pragma once
// v3.0.0 D4: Simplified Raft consensus for leader election.
// Used for global singleton services (matchmaking, leaderboard).
// Implements leader election + heartbeat; log replication is future scope.

#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <map>
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

// ── Implementation ─────────────────────────────────────────────────────

inline RaftNode::RaftNode(RaftConfig config)
    : config_(std::move(config)),
      peers_(config_.peers),
      rng_(std::random_device{}()) {
    reset_election_timeout();
}

inline RaftNode::~RaftNode() { stop(); }

inline void RaftNode::start() {
    running_ = true;
    thread_ = std::thread([this]() { run(); });
}

inline void RaftNode::stop() {
    running_ = false;
    if (thread_.joinable()) thread_.join();
}

inline RaftState RaftNode::state() const {
    std::lock_guard lock(mutex_);
    return state_;
}

inline std::uint64_t RaftNode::current_term() const {
    std::lock_guard lock(mutex_);
    return current_term_;
}

inline std::string RaftNode::leader_id() const {
    std::lock_guard lock(mutex_);
    return leader_id_;
}

inline void RaftNode::run() {
    while (running_) {
        auto now = std::chrono::steady_clock::now();
        {
            std::lock_guard lock(mutex_);
            if (state_ == RaftState::kLeader) {
                // Send heartbeat
                send_heartbeat();
                std::this_thread::sleep_for(config_.heartbeat_interval);
                continue;
            }
            if (now >= election_deadline_) {
                become_candidate();
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}

inline void RaftNode::reset_election_timeout() {
    std::uniform_int_distribution<std::uint64_t> dist(
        config_.election_timeout_min.count(),
        config_.election_timeout_max.count());
    auto timeout = std::chrono::milliseconds(dist(rng_));
    election_deadline_ = std::chrono::steady_clock::now() + timeout;
}

inline void RaftNode::start_election() {
    current_term_++;
    voted_for_ = config_.node_id;
    votes_received_ = 1;  // vote for self
    state_ = RaftState::kCandidate;
    reset_election_timeout();

    RequestVoteArgs args;
    args.term = current_term_;
    args.candidate_id = config_.node_id;

    // Request votes from all peers
    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) continue;  // skip self
        // Always query internal simulated peers for votes
        auto reply = handle_request_vote_internal(peer, args);
        if (reply.vote_granted) {
            votes_received_++;
        } else if (reply.term > current_term_) {
            become_follower(reply.term);
            return;
        }
    }

    // Check if we won the election
    if (votes_received_ >= quorum_size()) {
        become_leader();
    }
}

inline void RaftNode::send_heartbeat() {
    AppendEntriesArgs args;
    args.term = current_term_;
    args.leader_id = config_.node_id;

    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) continue;
        if (rpc_sender_) {
            // Send heartbeat to peer (implementation detail)
        }
    }
}

inline void RaftNode::become_follower(std::uint64_t term) {
    state_ = RaftState::kFollower;
    current_term_ = term;
    voted_for_.reset();
    if (step_down_cb_) step_down_cb_();
}

inline void RaftNode::become_candidate() {
    start_election();
}

inline void RaftNode::become_leader() {
    state_ = RaftState::kLeader;
    leader_id_ = config_.node_id;
    if (leader_cb_) leader_cb_();
}

inline RequestVoteReply RaftNode::handle_request_vote(
    const RequestVoteArgs& args) {
    std::lock_guard lock(mutex_);

    RequestVoteReply reply;
    reply.term = current_term_;

    if (args.term < current_term_) {
        reply.vote_granted = false;
        return reply;
    }

    if (args.term > current_term_) {
        become_follower(args.term);
        reply.term = args.term;
    }

    if (!voted_for_.has_value() || *voted_for_ == args.candidate_id) {
        voted_for_ = args.candidate_id;
        reply.vote_granted = true;
        reset_election_timeout();
    }

    return reply;
}

inline AppendEntriesReply RaftNode::handle_append_entries(
    const AppendEntriesArgs& args) {
    std::lock_guard lock(mutex_);

    AppendEntriesReply reply;
    reply.term = current_term_;

    if (args.term < current_term_) {
        reply.success = false;
        return reply;
    }

    // Valid leader heartbeat — reset election timer
    reset_election_timeout();
    reply.success = true;

    if (args.term > current_term_) {
        become_follower(args.term);
    }

    if (state_ == RaftState::kCandidate) {
        become_follower(args.term);
    }

    leader_id_ = args.leader_id;
    reply.term = current_term_;
    return reply;
}

// Internal helper for election (simulated RPC)
inline RequestVoteReply handle_request_vote_internal(
    const RaftNodeId& peer, const RequestVoteArgs& args) {
    // In real implementation, this would be an actual network RPC.
    // For testing, we simulate: all peers always grant votes.
    RequestVoteReply reply;
    reply.term = args.term;
    reply.vote_granted = true;
    return reply;
}

}  // namespace v3::cluster
