// v3.0.0 D4: RaftNode implementation.
// v3.4.0: Adds in-memory log replication and commit tracking.

#include "v3/cluster/raft.h"

#include "v3/cluster/raft_state_codec.h"

#include "app/audit_log.h"

#include <algorithm>
#include <chrono>
#include <filesystem>
#include <stdexcept>
#include <thread>

namespace v3::cluster {

namespace {

std::uint64_t last_log_term_of(const std::vector<LogEntry>& log) {
    return log.empty() ? 0 : log.back().term;
}

bool is_valid_peer_id(const std::string& node_id) {
    const RaftStateCodecLimits limits;
    return !node_id.empty() && node_id.size() <= limits.max_node_id_bytes &&
           node_id.find('\0') == std::string::npos;
}

bool is_valid_request_vote(const RequestVoteArgs& args) {
    if (args.term == 0 || !is_valid_peer_id(args.candidate_id) || args.last_log_term > args.term) {
        return false;
    }
    return (args.last_log_index == 0) == (args.last_log_term == 0);
}

bool is_valid_append_entries(const AppendEntriesArgs& args) {
    const RaftStateCodecLimits limits;
    if (args.term == 0 || !is_valid_peer_id(args.leader_id) ||
        (args.prev_log_index == 0) != (args.prev_log_term == 0) || args.prev_log_term > args.term ||
        args.prev_log_index > limits.max_log_entries ||
        args.entries.size() > limits.max_log_entries - args.prev_log_index) {
        return false;
    }
    return std::all_of(args.entries.begin(), args.entries.end(), [&](const LogEntry& entry) {
        return entry.term > 0 && entry.term <= args.term &&
               entry.command.size() <= limits.max_command_bytes;
    });
}

} // namespace

RaftNode::RaftNode(RaftConfig config)
    : config_(std::move(config)), peers_(config_.peers), rng_(std::random_device{}()) {
    if (peers_.empty()) {
        peers_.push_back(RaftNodeId{config_.node_id, "", 0});
    }
    load_persistent_state();
    reset_election_timeout();
}

RaftNode::~RaftNode() {
    stop();
}

void RaftNode::start() {
    {
        std::lock_guard lock(mutex_);
        if (running_.load() || thread_.joinable()) {
            throw std::logic_error("RaftNode is already running");
        }
        if (!healthy_) {
            throw std::runtime_error("RaftNode is unhealthy: " + last_error_);
        }
        if (peers_.size() == 1 && peers_.front().id == config_.node_id) {
            if (current_term_ == 0) {
                current_term_ = 1;
            }
            if (!become_leader()) {
                throw std::runtime_error("RaftNode failed initial persistence: " + last_error_);
            }
        } else if (!persist_state_locked()) {
            throw std::runtime_error("RaftNode failed initial persistence: " + last_error_);
        }
        running_ = true;
    }
    try {
        thread_ = std::thread([this]() { run(); });
    } catch (...) {
        std::lock_guard lock(mutex_);
        running_ = false;
        mark_unhealthy_locked("failed to start Raft worker thread");
        throw;
    }
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

bool RaftNode::healthy() const {
    std::lock_guard lock(mutex_);
    return healthy_;
}

std::string RaftNode::last_error() const {
    std::lock_guard lock(mutex_);
    return last_error_;
}

std::optional<std::uint64_t> RaftNode::failed_apply_index() const {
    std::lock_guard lock(mutex_);
    return failed_apply_index_;
}

void RaftNode::on_apply(ApplyCallback cb) {
    std::vector<std::pair<std::uint64_t, LogEntry>> replayed;
    {
        std::lock_guard lock(mutex_);
        apply_cb_ = std::move(cb);
        if (!apply_cb_) {
            return;
        }
        for (std::uint64_t index = 1; index <= last_applied_ && index <= log_.size(); ++index) {
            replayed.push_back({
                index,
                log_[static_cast<std::size_t>(index - 1)],
            });
        }
    }
    ApplyCallback callback;
    {
        std::lock_guard lock(mutex_);
        callback = apply_cb_;
    }
    for (const auto& [index, entry] : replayed) {
        try {
            if (callback && !callback(index, entry)) {
                std::lock_guard lock(mutex_);
                failed_apply_index_ = index;
                mark_unhealthy_locked("state machine rejected committed entry at index " +
                                      std::to_string(index));
                return;
            }
        } catch (const std::exception& error) {
            std::lock_guard lock(mutex_);
            failed_apply_index_ = index;
            mark_unhealthy_locked("state machine replay failed at index " +
                                  std::to_string(index) + ": " + error.what());
            return;
        } catch (...) {
            std::lock_guard lock(mutex_);
            failed_apply_index_ = index;
            mark_unhealthy_locked("state machine replay failed at index " +
                                  std::to_string(index));
            return;
        }
    }

    std::vector<std::pair<std::uint64_t, LogEntry>> pending;
    {
        std::lock_guard lock(mutex_);
        if (!healthy_) {
            return;
        }
        drain_committed_entries_locked(pending);
    }
    (void)deliver_applied_entries(pending);
}

void RaftNode::set_state_machine(StateMachine* sm) {
    state_machine_ = sm;
    if (sm) {
        on_apply([this](std::uint64_t index, const LogEntry& entry) {
            return state_machine_->apply(index, entry);
        });
    } else {
        on_apply(nullptr);
    }
}

void RaftNode::run() {
    try {
        while (running_) {
            const auto now = std::chrono::steady_clock::now();
            bool should_elect = false;
            bool should_send_heartbeat = false;
            {
                std::lock_guard lock(mutex_);
                if (!healthy_) {
                    return;
                }
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
    } catch (const std::exception& error) {
        std::lock_guard lock(mutex_);
        mark_unhealthy_locked(std::string("Raft worker failure: ") + error.what());
    } catch (...) {
        std::lock_guard lock(mutex_);
        mark_unhealthy_locked("Raft worker failure: unknown exception");
    }
}

void RaftNode::reset_election_timeout() {
    std::uniform_int_distribution<std::uint64_t> dist(config_.election_timeout_min.count(),
                                                      config_.election_timeout_max.count());
    election_deadline_ = std::chrono::steady_clock::now() + std::chrono::milliseconds(dist(rng_));
}

void RaftNode::start_election() {
    RequestVoteArgs args;
    RaftWireFormat writer_format = RaftWireFormat::kLegacyJson;
    {
        std::lock_guard lock(mutex_);
        if (!healthy_) {
            return;
        }
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
        writer_format = active_writer_format_locked();
        if (!persist_state_locked()) {
            return;
        }
    }

    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }

        RequestVoteReply reply;
        if (rpc_sender_) {
            auto raw = rpc_sender_(peer, serialize_request_vote(args, writer_format));
            if (!raw.empty()) {
                reply = parse_request_vote_reply(raw);
            }
        } else {
            reply = handle_request_vote_internal(peer, args);
        }

        std::lock_guard lock(mutex_);
        if (!healthy_ || state_ != RaftState::kCandidate || current_term_ != args.term) {
            return;
        }
        if (reply.term > current_term_) {
            (void)become_follower(reply.term);
            return;
        }
        if (reply.vote_granted) {
            ++votes_received_;
        }
    }

    std::lock_guard lock(mutex_);
    if (state_ == RaftState::kCandidate && votes_received_ >= quorum_size()) {
        (void)become_leader();
    }
}

void RaftNode::send_heartbeat() {
    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }

        AppendEntriesArgs args;
        RaftWireFormat writer_format = RaftWireFormat::kLegacyJson;
        {
            std::lock_guard lock(mutex_);
            if (!healthy_ || state_ != RaftState::kLeader) {
                return;
            }
            args = make_append_entries_for(peer.id);
            writer_format = active_writer_format_locked();
        }

        if (!rpc_sender_) {
            continue;
        }

        auto raw = rpc_sender_(peer, serialize_append_entries(args, writer_format));
        if (raw.empty()) {
            continue;
        }
        auto reply = parse_append_entries_reply(raw);

        std::lock_guard lock(mutex_);
        if (!healthy_ || state_ != RaftState::kLeader || args.term != current_term_) {
            return;
        }
        if (reply.term > current_term_) {
            (void)become_follower(reply.term);
            return;
        }
        if (reply.success) {
            match_index_[peer.id] = reply.match_index;
            next_index_[peer.id] = reply.match_index + 1;
            update_commit_index_locked();
            if (!persist_state_locked()) {
                return;
            }
        } else {
            auto& next = next_index_[peer.id];
            next = next > 1 ? next - 1 : 1;
        }
    }
}

bool RaftNode::become_follower(std::uint64_t term) {
    const bool was_leader = state_ == RaftState::kLeader;
    state_ = RaftState::kFollower;
    current_term_ = term;
    voted_for_.reset();
    votes_received_ = 0;
    next_index_.clear();
    match_index_.clear();
    reset_election_timeout();
    leader_id_.clear();
    if (!persist_state_locked()) {
        return false;
    }
    if (was_leader) {
        AUDIT_LOG("raft_leader_step_down",
                  "node_id=" + config_.node_id + " term=" + std::to_string(current_term_));
        if (step_down_cb_) {
            step_down_cb_();
        }
    }
    return true;
}

bool RaftNode::become_leader() {
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
    if (!persist_state_locked()) {
        return false;
    }
    AUDIT_LOG("raft_leader_elected",
              "node_id=" + config_.node_id + " term=" + std::to_string(current_term_));
    if (leader_cb_) {
        leader_cb_();
    }
    return true;
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
        args.entries.assign(log_.begin() + static_cast<std::ptrdiff_t>(next - 1), log_.end());
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
    const auto candidate = replicated[replicated.size() - quorum_size()];
    if (candidate > commit_index_ && candidate <= log_.size() &&
        log_[static_cast<std::size_t>(candidate - 1)].term == current_term_) {
        commit_index_ = candidate;
    }
}

void RaftNode::drain_committed_entries_locked(
    std::vector<std::pair<std::uint64_t, LogEntry>>& applied) {
    auto next = last_applied_;
    while (next < commit_index_ && next < log_.size()) {
        ++next;
        applied.push_back({
            next,
            log_[static_cast<std::size_t>(next - 1)],
        });
    }
}

bool RaftNode::deliver_applied_entries(
    const std::vector<std::pair<std::uint64_t, LogEntry>>& applied) {
    for (const auto& [index, entry] : applied) {
        ApplyCallback callback;
        {
            std::lock_guard lock(mutex_);
            if (!healthy_ || index != last_applied_ + 1) {
                return false;
            }
            callback = apply_cb_;
        }

        try {
            if (callback && !callback(index, entry)) {
                std::lock_guard lock(mutex_);
                failed_apply_index_ = index;
                mark_unhealthy_locked("state machine rejected committed entry at index " +
                                      std::to_string(index));
                return false;
            }
        } catch (const std::exception& error) {
            std::lock_guard lock(mutex_);
            failed_apply_index_ = index;
            mark_unhealthy_locked("state machine apply failed at index " +
                                  std::to_string(index) + ": " + error.what());
            return false;
        } catch (...) {
            std::lock_guard lock(mutex_);
            failed_apply_index_ = index;
            mark_unhealthy_locked("state machine apply failed at index " +
                                  std::to_string(index));
            return false;
        }

        std::lock_guard lock(mutex_);
        if (!healthy_ || index != last_applied_ + 1) {
            return false;
        }
        const auto previous = last_applied_;
        last_applied_ = index;
        if (!persist_state_locked()) {
            last_applied_ = previous;
            return false;
        }
    }
    return true;
}

RequestVoteReply RaftNode::handle_request_vote(const RequestVoteArgs& args) {
    std::lock_guard lock(mutex_);
    RequestVoteReply reply{.term = current_term_, .vote_granted = false};

    if (!healthy_ || !is_valid_request_vote(args)) {
        return reply;
    }
    if (args.term < current_term_) {
        return reply;
    }
    if (args.term > current_term_) {
        if (!become_follower(args.term)) {
            reply.term = current_term_;
            return reply;
        }
    }

    reply.term = current_term_;
    const bool can_vote = !voted_for_.has_value() || *voted_for_ == args.candidate_id;
    if (can_vote && is_log_up_to_date(args.last_log_term, args.last_log_index)) {
        voted_for_ = args.candidate_id;
        reset_election_timeout();
        reply.vote_granted = persist_state_locked();
    }
    return reply;
}

AppendEntriesReply RaftNode::handle_append_entries(const AppendEntriesArgs& args) {
    std::lock_guard pipeline_lock(apply_pipeline_mutex_);
    std::vector<std::pair<std::uint64_t, LogEntry>> applied;
    AppendEntriesReply reply{};
    {
        std::lock_guard lock(mutex_);
        reply = AppendEntriesReply{
            .term = current_term_,
            .success = false,
            .match_index = static_cast<std::uint64_t>(log_.size()),
        };

        if (!healthy_ || !is_valid_append_entries(args)) {
            return reply;
        }
        if (args.term < current_term_) {
            return reply;
        }
        if (args.term > current_term_) {
            if (!become_follower(args.term)) {
                reply.term = current_term_;
                return reply;
            }
        } else if (state_ == RaftState::kCandidate) {
            if (!become_follower(args.term)) {
                reply.term = current_term_;
                return reply;
            }
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
                (void)persist_state_locked();
                return reply;
            }
        }

        std::size_t local_index = static_cast<std::size_t>(args.prev_log_index);
        bool caught_up = false;
        for (const auto& entry : args.entries) {
            if (local_index < log_.size()) {
                if (log_[local_index].term != entry.term ||
                    log_[local_index].command != entry.command) {
                    log_.resize(local_index);
                }
            }
            if (local_index >= log_.size()) {
                log_.push_back(entry);
                caught_up = true;
            }
            ++local_index;
        }
        if (caught_up) {
            AUDIT_LOG("raft_follower_catch_up",
                      "node_id=" + config_.node_id + " term=" + std::to_string(current_term_));
        }

        if (args.leader_commit > commit_index_) {
            commit_index_ = std::min<std::uint64_t>(args.leader_commit, log_.size());
        }
        drain_committed_entries_locked(applied);

        if (!persist_state_locked()) {
            reply.term = current_term_;
            return reply;
        }
        reply.term = current_term_;
        reply.success = true;
        reply.match_index = static_cast<std::uint64_t>(log_.size());
    }
    if (!deliver_applied_entries(applied)) {
        reply.success = false;
    }
    return reply;
}

RaftCapabilityReply RaftNode::handle_capability_request(const RaftCapabilityRequest& request) {
    const bool supports_v1 = std::find(request.supported_protocol_versions.begin(),
                                       request.supported_protocol_versions.end(),
                                       1U) != request.supported_protocol_versions.end();
    std::lock_guard lock(mutex_);
    const auto configured_peer =
        std::find_if(peers_.begin(), peers_.end(),
                     [&](const RaftNodeId& peer) { return peer.id == request.node_id; });
    const bool accepted =
        configured_peer != peers_.end() && request.node_id != config_.node_id && supports_v1;
    if (accepted) {
        peer_protocol_versions_[request.node_id] = 1U;
    }
    return RaftCapabilityReply{
        .node_id = config_.node_id,
        .selected_protocol_version = accepted ? 1U : 0U,
        .protobuf_supported = accepted,
    };
}

void RaftNode::refresh_peer_capabilities() {
    RpcSender sender;
    std::vector<RaftNodeId> targets;
    {
        std::lock_guard lock(mutex_);
        sender = rpc_sender_;
        for (const auto& peer : peers_) {
            if (peer.id != config_.node_id) {
                targets.push_back(peer);
            }
        }
    }
    if (!sender) {
        return;
    }

    const auto request = serialize_raft_capability_request(
        RaftCapabilityRequest{.node_id = config_.node_id, .supported_protocol_versions = {1U}});
    for (const auto& peer : targets) {
        {
            std::lock_guard lock(mutex_);
            peer_protocol_versions_.erase(peer.id);
        }
        try {
            const auto raw_reply = sender(peer, request);
            if (raw_reply.empty()) {
                continue;
            }
            const auto reply = parse_raft_capability_reply(raw_reply);
            if (reply.node_id != peer.id) {
                continue;
            }
            std::lock_guard lock(mutex_);
            if (reply.protobuf_supported && reply.selected_protocol_version == 1U) {
                peer_protocol_versions_[peer.id] = 1U;
            }
        } catch (const std::exception&) {
            // A missing, malformed, or legacy-only response leaves the peer unadvertised.
        }
    }
}

bool RaftNode::peer_supports_protobuf(const std::string& peer_id) const {
    if (peer_id == config_.node_id) {
        return true;
    }
    std::lock_guard lock(mutex_);
    const auto found = peer_protocol_versions_.find(peer_id);
    return found != peer_protocol_versions_.end() && found->second == 1U;
}

bool RaftNode::all_voting_peers_support_protobuf() const {
    std::lock_guard lock(mutex_);
    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }
        const auto found = peer_protocol_versions_.find(peer.id);
        if (found == peer_protocol_versions_.end() || found->second != 1U) {
            return false;
        }
    }
    return true;
}

RaftWireFormat RaftNode::active_writer_format_locked() const {
    if (!config_.protobuf_writer_enabled || !healthy_) {
        return RaftWireFormat::kLegacyJson;
    }
    for (const auto& peer : peers_) {
        if (peer.id == config_.node_id) {
            continue;
        }
        const auto found = peer_protocol_versions_.find(peer.id);
        if (found == peer_protocol_versions_.end() || found->second != 1U) {
            return RaftWireFormat::kLegacyJson;
        }
    }
    return RaftWireFormat::kProtobufV1;
}

RaftWireFormat RaftNode::active_writer_format() const {
    std::lock_guard lock(mutex_);
    return active_writer_format_locked();
}

bool RaftNode::append_command(const std::string& command) {
    std::lock_guard pipeline_lock(apply_pipeline_mutex_);
    std::vector<RaftNodeId> targets;
    {
        std::lock_guard lock(mutex_);
        const RaftStateCodecLimits limits;
        if (!healthy_ || state_ != RaftState::kLeader ||
            command.size() > limits.max_command_bytes || log_.size() >= limits.max_log_entries) {
            return false;
        }
        log_.push_back(LogEntry{.term = current_term_, .command = command});
        match_index_[config_.node_id] = static_cast<std::uint64_t>(log_.size());
        if (!persist_state_locked()) {
            return false;
        }
        for (const auto& peer : peers_) {
            if (peer.id != config_.node_id) {
                targets.push_back(peer);
            }
        }
    }

    std::size_t successes = 1;
    for (const auto& peer : targets) {
        AppendEntriesArgs args;
        RaftWireFormat writer_format = RaftWireFormat::kLegacyJson;
        {
            std::lock_guard lock(mutex_);
            if (!healthy_ || state_ != RaftState::kLeader) {
                return false;
            }
            args = make_append_entries_for(peer.id);
            writer_format = active_writer_format_locked();
        }

        AppendEntriesReply reply;
        if (rpc_sender_) {
            auto raw = rpc_sender_(peer, serialize_append_entries(args, writer_format));
            if (!raw.empty()) {
                reply = parse_append_entries_reply(raw);
            }
        } else {
            reply = AppendEntriesReply{
                .term = args.term,
                .success = true,
                .match_index =
                    args.prev_log_index + static_cast<std::uint64_t>(args.entries.size()),
            };
        }

        std::lock_guard lock(mutex_);
        if (!healthy_ || state_ != RaftState::kLeader || args.term != current_term_) {
            return false;
        }
        if (reply.term > current_term_) {
            (void)become_follower(reply.term);
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

    std::vector<std::pair<std::uint64_t, LogEntry>> applied;
    {
        std::lock_guard lock(mutex_);
        if (!healthy_ || state_ != RaftState::kLeader || successes < quorum_size()) {
            return false;
        }
        update_commit_index_locked();
        drain_committed_entries_locked(applied);
        if (!persist_state_locked()) {
            return false;
        }
    }

    if (!deliver_applied_entries(applied)) {
        return false;
    }
    send_heartbeat();
    return true;
}

std::filesystem::path RaftNode::state_path() const {
    if (config_.storage_dir.empty()) {
        return {};
    }
    return std::filesystem::path(config_.storage_dir) / (config_.node_id + ".raft.json");
}

void RaftNode::load_persistent_state() {
    const auto path = state_path();
    if (path.empty()) {
        return;
    }

    const RaftStateStore store(path, config_.node_id);
    const auto loaded = store.load_or_migrate();
    if (!loaded.has_value()) {
        return;
    }

    current_term_ = loaded->state.current_term;
    voted_for_ = loaded->state.voted_for;
    leader_id_.clear();
    commit_index_ = loaded->state.commit_index;
    last_applied_ = loaded->state.last_applied;
    log_.clear();
    log_.reserve(loaded->state.log.size());
    for (const auto& entry : loaded->state.log) {
        log_.push_back(LogEntry{.term = entry.term, .command = entry.command});
    }
    state_ = RaftState::kFollower;
}

bool RaftNode::persist_state_locked() {
    if (!healthy_) {
        return false;
    }
    const auto path = state_path();
    if (path.empty()) {
        return true;
    }

    RaftPersistentState persisted{
        .current_term = current_term_,
        .voted_for = voted_for_,
        .log = {},
        .commit_index = commit_index_,
        .last_applied = last_applied_,
    };
    persisted.log.reserve(log_.size());
    for (const auto& entry : log_) {
        persisted.log.push_back(
            RaftPersistedLogEntry{.term = entry.term, .command = entry.command});
    }

    try {
        RaftStateStore(path, config_.node_id).save(persisted);
        return true;
    } catch (const RaftStateException& error) {
        mark_unhealthy_locked(error.what());
    } catch (const std::exception& error) {
        mark_unhealthy_locked(std::string("unexpected Raft state error: ") + error.what());
    }
    return false;
}

void RaftNode::mark_unhealthy_locked(std::string reason) {
    if (!healthy_) {
        return;
    }
    const bool was_leader = state_ == RaftState::kLeader;
    healthy_ = false;
    last_error_ = std::move(reason);
    running_ = false;
    state_ = RaftState::kFollower;
    leader_id_.clear();
    votes_received_ = 0;
    next_index_.clear();
    match_index_.clear();
    AUDIT_LOG("raft_node_unhealthy", "node_id=" + config_.node_id + " reason=" + last_error_);
    if (was_leader && step_down_cb_) {
        step_down_cb_();
    }
}

RequestVoteReply handle_request_vote_internal(const RaftNodeId& /*peer*/,
                                              const RequestVoteArgs& args) {
    return RequestVoteReply{.term = args.term, .vote_granted = true};
}

} // namespace v3::cluster
