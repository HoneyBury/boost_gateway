// v3.0.0 D4: RaftNode implementation — leader election with heartbeat.

#include "v3/cluster/raft.h"

#include <chrono>
#include <random>
#include <thread>

namespace v3::cluster {

RaftNode::RaftNode(RaftConfig config)
    : config_(std::move(config)),
      peers_(config_.peers),
      rng_(std::random_device{}()) {
    reset_election_timeout();
}

RaftNode::~RaftNode() { stop(); }

void RaftNode::start() {
    running_ = true;
    thread_ = std::thread([this]() { run(); });
}

void RaftNode::stop() {
    running_ = false;
    if (thread_.joinable()) thread_.join();
}

RaftState RaftNode::state() const {
    std::lock_guard lock(mutex_);
    return state_;
}

std::uint64_t RaftNode::current_term() const {
    std::lock_guard lock(mutex_);
    return current_term_;
}

std::string RaftNode::leader_id() const {
    std::lock_guard lock(mutex_);
    return leader_id_;
}

void RaftNode::run() {
    while (running_) {
        auto now = std::chrono::steady_clock::now();
        {
            std::lock_guard lock(mutex_);
            if (state_ == RaftState::kLeader) {
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

void RaftNode::reset_election_timeout() {
    std::uniform_int_distribution<std::uint64_t> dist(
        config_.election_timeout_min.count(),
        config_.election_timeout_max.count());
    auto timeout = std::chrono::milliseconds(dist(rng_));
    election_deadline_ = std::chrono::steady_clock::now() + timeout;
}

void RaftNode::start_election() {
    current_term_++;
    voted_for_ = config_.node_id;
    votes_received_ = 1;

    state_ = RaftState::kCandidate;
    reset_election_timeout();

    RequestVoteArgs args;
    args.term = current_term_;
    args.candidate_id = config_.node_id;

    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) continue;
        auto reply = handle_request_vote_internal(peer, args);
        if (reply.vote_granted) {
            votes_received_++;
        } else if (reply.term > current_term_) {
            become_follower(reply.term);
            return;
        }
    }

    if (votes_received_ >= quorum_size()) {
        become_leader();
    }
}

void RaftNode::send_heartbeat() {
    AppendEntriesArgs args;
    args.term = current_term_;
    args.leader_id = config_.node_id;

    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) continue;
        if (rpc_sender_) {
            // RPC heartbeat to peer (gRPC in full implementation)
        }
    }
}

void RaftNode::become_follower(std::uint64_t term) {
    state_ = RaftState::kFollower;
    current_term_ = term;
    voted_for_.reset();
    if (step_down_cb_) step_down_cb_();
}

void RaftNode::become_candidate() {
    start_election();
}

void RaftNode::become_leader() {
    state_ = RaftState::kLeader;
    leader_id_ = config_.node_id;
    if (leader_cb_) leader_cb_();
}

RequestVoteReply RaftNode::handle_request_vote(const RequestVoteArgs& args) {
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

AppendEntriesReply RaftNode::handle_append_entries(const AppendEntriesArgs& args) {
    std::lock_guard lock(mutex_);
    AppendEntriesReply reply;
    reply.term = current_term_;

    if (args.term < current_term_) {
        reply.success = false;
        return reply;
    }

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

RequestVoteReply handle_request_vote_internal(
    const RaftNodeId& /*peer*/, const RequestVoteArgs& args) {
    RequestVoteReply reply;
    reply.term = args.term;
    reply.vote_granted = true;
    return reply;
}

}  // namespace v3::cluster
