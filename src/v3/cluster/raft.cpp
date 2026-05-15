// v3.0.0 D4: RaftNode implementation.
// v3.4.0: Adds in-memory log replication and commit tracking.

#include "v3/cluster/raft.h"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <thread>

namespace v3::cluster {

namespace {

std::uint64_t last_log_term_of(const std::vector<LogEntry>& log) {
    return log.empty() ? 0 : log.back().term;
}

}  // namespace

RaftNode::RaftNode(RaftConfig config)
    : config_(std::move(config)),
      peers_(config_.peers),
      rng_(std::random_device{}()) {
    if (peers_.empty()) {
        peers_.push_back(RaftNodeId{config_.node_id, "", 0});
    }
    load_persistent_state();
    reset_election_timeout();
}

RaftNode::~RaftNode() { stop(); }

void RaftNode::start() {
    running_ = true;
    thread_ = std::thread([this]() { run(); });
}

void RaftNode::stop() {
    running_ = false;
    if (thread_.joinable()) {
        thread_.join();
    }
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

std::uint64_t RaftNode::last_log_index() const {
    std::lock_guard lock(mutex_);
    return log_.size();
}

std::uint64_t RaftNode::last_log_term() const {
    std::lock_guard lock(mutex_);
    return last_log_term_of(log_);
}

std::uint64_t RaftNode::commit_index() const {
    std::lock_guard lock(mutex_);
    return commit_index_;
}

std::uint64_t RaftNode::last_applied() const {
    std::lock_guard lock(mutex_);
    return last_applied_;
}

std::size_t RaftNode::log_size() const {
    std::lock_guard lock(mutex_);
    return log_.size();
}

std::string RaftNode::log_command(std::uint64_t index) const {
    std::lock_guard lock(mutex_);
    if (index == 0 || index > log_.size()) {
        return {};
    }
    return log_[static_cast<std::size_t>(index - 1)].command;
}

void RaftNode::run() {
    while (running_) {
        const auto now = std::chrono::steady_clock::now();
        bool should_elect = false;
        bool should_send_heartbeat = false;
        {
            std::lock_guard lock(mutex_);
            if (state_ == RaftState::kLeader) {
                should_send_heartbeat = true;
            } else if (now >= election_deadline_) {
                should_elect = true;
            }
        }

        if (should_send_heartbeat) {
            send_heartbeat();
            std::this_thread::sleep_for(config_.heartbeat_interval);
            continue;
        }
        if (should_elect) {
            start_election();
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
}

void RaftNode::reset_election_timeout() {
    std::uniform_int_distribution<std::uint64_t> dist(
        config_.election_timeout_min.count(),
        config_.election_timeout_max.count());
    election_deadline_ =
        std::chrono::steady_clock::now() + std::chrono::milliseconds(dist(rng_));
}

void RaftNode::start_election() {
    RequestVoteArgs args;
    {
        std::lock_guard lock(mutex_);
        current_term_++;
        voted_for_ = config_.node_id;
        votes_received_ = 1;
        state_ = RaftState::kCandidate;
        leader_id_.clear();
        reset_election_timeout();
        args.term = current_term_;
        args.candidate_id = config_.node_id;
        args.last_log_index = log_.size();
        args.last_log_term = last_log_term_of(log_);
        persist_state_locked();
    }

    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }

        RequestVoteReply reply;
        if (rpc_sender_) {
            auto raw = rpc_sender_(peer, serialize_request_vote(args));
            if (!raw.empty()) {
                reply = parse_request_vote_reply(raw);
            }
        } else {
            reply = handle_request_vote_internal(peer, args);
        }

        std::lock_guard lock(mutex_);
        if (state_ != RaftState::kCandidate || current_term_ != args.term) {
            return;
        }
        if (reply.term > current_term_) {
            become_follower(reply.term);
            return;
        }
        if (reply.vote_granted) {
            ++votes_received_;
        }
    }

    std::lock_guard lock(mutex_);
    if (state_ == RaftState::kCandidate && votes_received_ >= quorum_size()) {
        become_leader();
    }
}

void RaftNode::send_heartbeat() {
    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }

        AppendEntriesArgs args;
        {
            std::lock_guard lock(mutex_);
            if (state_ != RaftState::kLeader) {
                return;
            }
            args = make_append_entries_for(peer.id);
        }

        if (!rpc_sender_) {
            continue;
        }

        auto raw = rpc_sender_(peer, serialize_append_entries(args));
        if (raw.empty()) {
            continue;
        }
        auto reply = parse_append_entries_reply(raw);

        std::lock_guard lock(mutex_);
        if (reply.term > current_term_) {
            become_follower(reply.term);
            return;
        }
        if (state_ != RaftState::kLeader) {
            return;
        }
        if (reply.success) {
            match_index_[peer.id] = reply.match_index;
            next_index_[peer.id] = reply.match_index + 1;
            update_commit_index_locked();
            persist_state_locked();
        } else {
            auto& next = next_index_[peer.id];
            next = next > 1 ? next - 1 : 1;
        }
    }
}

void RaftNode::become_follower(std::uint64_t term) {
    const bool was_leader = state_ == RaftState::kLeader;
    state_ = RaftState::kFollower;
    current_term_ = term;
    voted_for_.reset();
    votes_received_ = 0;
    next_index_.clear();
    match_index_.clear();
    reset_election_timeout();
    leader_id_.clear();
    persist_state_locked();
    if (was_leader && step_down_cb_) {
        step_down_cb_();
    }
}

void RaftNode::become_leader() {
    state_ = RaftState::kLeader;
    leader_id_ = config_.node_id;
    next_index_.clear();
    match_index_.clear();
    const auto next = static_cast<std::uint64_t>(log_.size()) + 1;
    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }
        next_index_[peer.id] = next;
        match_index_[peer.id] = 0;
    }
    persist_state_locked();
    if (leader_cb_) {
        leader_cb_();
    }
}

bool RaftNode::is_log_up_to_date(std::uint64_t candidate_last_log_term,
                                 std::uint64_t candidate_last_log_index) const {
    const auto local_last_term = last_log_term_of(log_);
    const auto local_last_index = static_cast<std::uint64_t>(log_.size());
    if (candidate_last_log_term != local_last_term) {
        return candidate_last_log_term > local_last_term;
    }
    return candidate_last_log_index >= local_last_index;
}

AppendEntriesArgs RaftNode::make_append_entries_for(const std::string& peer_id) const {
    AppendEntriesArgs args;
    args.term = current_term_;
    args.leader_id = config_.node_id;
    args.leader_commit = commit_index_;

    auto next = static_cast<std::uint64_t>(log_.size()) + 1;
    if (auto it = next_index_.find(peer_id); it != next_index_.end()) {
        next = it->second;
    }

    if (next > 1 && next - 1 <= log_.size()) {
        args.prev_log_index = next - 1;
        args.prev_log_term = log_[static_cast<std::size_t>(next - 2)].term;
    }
    if (next >= 1 && next <= log_.size()) {
        args.entries.assign(log_.begin() + static_cast<std::ptrdiff_t>(next - 1),
                            log_.end());
    }
    return args;
}

void RaftNode::update_commit_index_locked() {
    std::vector<std::uint64_t> replicated;
    replicated.reserve(match_index_.size() + 1);
    replicated.push_back(static_cast<std::uint64_t>(log_.size()));
    for (const auto& [peer_id, index] : match_index_) {
        (void)peer_id;
        replicated.push_back(index);
    }
    std::sort(replicated.begin(), replicated.end());
    const auto candidate =
        replicated[replicated.size() - quorum_size()];
    if (candidate > commit_index_ &&
        candidate <= log_.size() &&
        log_[static_cast<std::size_t>(candidate - 1)].term == current_term_) {
        commit_index_ = candidate;
        last_applied_ = std::max(last_applied_, commit_index_);
    }
}

RequestVoteReply RaftNode::handle_request_vote(const RequestVoteArgs& args) {
    std::lock_guard lock(mutex_);
    RequestVoteReply reply{.term = current_term_, .vote_granted = false};

    if (args.term < current_term_) {
        return reply;
    }
    if (args.term > current_term_) {
        become_follower(args.term);
    }

    reply.term = current_term_;
    const bool can_vote =
        !voted_for_.has_value() || *voted_for_ == args.candidate_id;
    if (can_vote && is_log_up_to_date(args.last_log_term, args.last_log_index)) {
        voted_for_ = args.candidate_id;
        reply.vote_granted = true;
        reset_election_timeout();
        persist_state_locked();
    }
    return reply;
}

AppendEntriesReply RaftNode::handle_append_entries(const AppendEntriesArgs& args) {
    std::lock_guard lock(mutex_);
    AppendEntriesReply reply{
        .term = current_term_,
        .success = false,
        .match_index = static_cast<std::uint64_t>(log_.size()),
    };

    if (args.term < current_term_) {
        return reply;
    }
    if (args.term > current_term_) {
        become_follower(args.term);
    } else if (state_ == RaftState::kCandidate) {
        become_follower(args.term);
    }

    leader_id_ = args.leader_id;
    reset_election_timeout();

    if (args.prev_log_index > log_.size()) {
        reply.term = current_term_;
        return reply;
    }
    if (args.prev_log_index > 0) {
        const auto local_prev_term =
            log_[static_cast<std::size_t>(args.prev_log_index - 1)].term;
        if (local_prev_term != args.prev_log_term) {
            log_.resize(static_cast<std::size_t>(args.prev_log_index - 1));
            if (commit_index_ > log_.size()) {
                commit_index_ = log_.size();
            }
            if (last_applied_ > commit_index_) {
                last_applied_ = commit_index_;
            }
            reply.term = current_term_;
            reply.match_index = static_cast<std::uint64_t>(log_.size());
            persist_state_locked();
            return reply;
        }
    }

    std::size_t local_index = static_cast<std::size_t>(args.prev_log_index);
    for (const auto& entry : args.entries) {
        if (local_index < log_.size()) {
            if (log_[local_index].term != entry.term ||
                log_[local_index].command != entry.command) {
                log_.resize(local_index);
            }
        }
        if (local_index >= log_.size()) {
            log_.push_back(entry);
        }
        ++local_index;
    }

    if (args.leader_commit > commit_index_) {
        commit_index_ = std::min<std::uint64_t>(args.leader_commit, log_.size());
        last_applied_ = std::max(last_applied_, commit_index_);
    }

    persist_state_locked();
    reply.term = current_term_;
    reply.success = true;
    reply.match_index = static_cast<std::uint64_t>(log_.size());
    return reply;
}

bool RaftNode::append_command(const std::string& command) {
    std::vector<RaftNodeId> targets;
    {
        std::lock_guard lock(mutex_);
        if (state_ != RaftState::kLeader) {
            return false;
        }
        log_.push_back(LogEntry{.term = current_term_, .command = command});
        match_index_[config_.node_id] = static_cast<std::uint64_t>(log_.size());
        persist_state_locked();
        for (const auto& peer : peers_) {
            if (peer.id != config_.node_id) {
                targets.push_back(peer);
            }
        }
    }

    std::size_t successes = 1;
    for (const auto& peer : targets) {
        AppendEntriesArgs args;
        {
            std::lock_guard lock(mutex_);
            if (state_ != RaftState::kLeader) {
                return false;
            }
            args = make_append_entries_for(peer.id);
        }

        AppendEntriesReply reply;
        if (rpc_sender_) {
            auto raw = rpc_sender_(peer, serialize_append_entries(args));
            if (!raw.empty()) {
                reply = parse_append_entries_reply(raw);
            }
        } else {
            reply = AppendEntriesReply{
                .term = args.term,
                .success = true,
                .match_index = args.prev_log_index +
                    static_cast<std::uint64_t>(args.entries.size()),
            };
        }

        std::lock_guard lock(mutex_);
        if (reply.term > current_term_) {
            become_follower(reply.term);
            return false;
        }
        if (!reply.success) {
            auto& next = next_index_[peer.id];
            next = next > 1 ? next - 1 : 1;
            continue;
        }
        match_index_[peer.id] = reply.match_index;
        next_index_[peer.id] = reply.match_index + 1;
        ++successes;
    }

    {
        std::lock_guard lock(mutex_);
        if (successes < quorum_size()) {
            return false;
        }
        update_commit_index_locked();
        persist_state_locked();
    }

    send_heartbeat();
    return true;
}

std::filesystem::path RaftNode::state_path() const {
    if (config_.storage_dir.empty()) {
        return {};
    }
    return std::filesystem::path(config_.storage_dir) /
           (config_.node_id + ".raft.json");
}

void RaftNode::load_persistent_state() {
    const auto path = state_path();
    if (path.empty()) {
        return;
    }

    std::ifstream input(path);
    if (!input.is_open()) {
        return;
    }

    nlohmann::json doc;
    try {
        input >> doc;
    } catch (const std::exception&) {
        return;
    }

    current_term_ = doc.value("current_term", std::uint64_t{0});
    if (doc.contains("voted_for") && doc["voted_for"].is_string()) {
        voted_for_ = doc["voted_for"].get<std::string>();
    } else {
        voted_for_.reset();
    }
    leader_id_ = doc.value("leader_id", std::string{});
    commit_index_ = doc.value("commit_index", std::uint64_t{0});
    last_applied_ = doc.value("last_applied", std::uint64_t{0});

    log_.clear();
    if (doc.contains("log") && doc["log"].is_array()) {
        for (const auto& entry : doc["log"]) {
            log_.push_back(LogEntry{
                .term = entry.value("term", std::uint64_t{0}),
                .command = entry.value("command", std::string{}),
            });
        }
    }

    if (commit_index_ > log_.size()) {
        commit_index_ = static_cast<std::uint64_t>(log_.size());
    }
    if (last_applied_ > commit_index_) {
        last_applied_ = commit_index_;
    }
    state_ = RaftState::kFollower;
}

void RaftNode::persist_state_locked() const {
    const auto path = state_path();
    if (path.empty()) {
        return;
    }

    std::error_code ec;
    std::filesystem::create_directories(path.parent_path(), ec);

    nlohmann::json doc{
        {"current_term", current_term_},
        {"voted_for", voted_for_.has_value() ? nlohmann::json(*voted_for_) : nlohmann::json(nullptr)},
        {"leader_id", leader_id_},
        {"commit_index", commit_index_},
        {"last_applied", last_applied_},
    };

    nlohmann::json log_entries = nlohmann::json::array();
    for (const auto& entry : log_) {
        log_entries.push_back({
            {"term", entry.term},
            {"command", entry.command},
        });
    }
    doc["log"] = std::move(log_entries);

    const auto temp_path = path.string() + ".tmp";
    {
        std::ofstream output(temp_path, std::ios::trunc);
        if (!output.is_open()) {
            return;
        }
        output << doc.dump(2);
    }

    std::filesystem::rename(temp_path, path, ec);
    if (ec) {
        std::filesystem::remove(path, ec);
        ec.clear();
        std::filesystem::rename(temp_path, path, ec);
        if (ec) {
            std::filesystem::remove(temp_path, ec);
        }
    }
}

RequestVoteReply handle_request_vote_internal(
    const RaftNodeId& /*peer*/, const RequestVoteArgs& args) {
    return RequestVoteReply{.term = args.term, .vote_granted = true};
}

}  // namespace v3::cluster
