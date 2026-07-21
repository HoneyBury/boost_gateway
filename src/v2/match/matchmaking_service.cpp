// v2.3.0 G1: MMR-based matchmaking backend service.
// v3.0.0 B4: Integrated Raft consensus for leader election.
// Only the Raft leader performs matchmaking to prevent duplicate matches.

#include "v2/match/matchmaking_service.h"
#include "v2/service/backend_connection.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"
#include "v3/cluster/raft.h"
#include "v3/cluster/raft_command_codec.h"

#include <spdlog/spdlog.h>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace v2::match {

namespace {

std::uint64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

std::atomic<std::uint64_t> g_next_match_id{1};

auto make_raft_rpc_sender() {
    return [](const v3::cluster::RaftNodeId& target, const std::string& data) -> std::string {
        if (target.host.empty() || target.port == 0) {
            return {};
        }

        std::string message_type;
        try {
            message_type = v3::cluster::raft_rpc_message_type(
                v3::cluster::detect_raft_rpc_kind(data));
        } catch (const std::exception&) {
            return {};
        }

        v2::service::BackendConnectionOptions options;
        options.host = target.host;
        options.port = target.port;
        options.timeout = std::chrono::milliseconds(1000);
        options.connect_timeout = std::chrono::milliseconds(500);
        v2::service::BackendConnection conn(std::move(options));
        if (!conn.connect()) {
            return {};
        }

        v2::service::BackendEnvelope request;
        request.target_service = v2::service::ServiceId::kGateway;
        request.kind = v2::service::MessageKind::kRequest;
        request.message_type = message_type;
        request.payload = data;

        auto response = conn.send_request(std::move(request));
        if (!response || response->kind != v2::service::MessageKind::kResponse) {
            return {};
        }
        return response->payload;
    };
}

MatchMode parse_mode(const std::string& mode_str) {
    if (mode_str == "2v2") {
        return MatchMode::k2v2;
    }
    if (mode_str == "4v4") {
        return MatchMode::k4v4;
    }
    return MatchMode::k1v1;
}

v3::cluster::RaftMatchMode to_raft_mode(MatchMode mode) {
    switch (mode) {
        case MatchMode::k1v1:
            return v3::cluster::RaftMatchMode::kOneVsOne;
        case MatchMode::k2v2:
            return v3::cluster::RaftMatchMode::kTwoVsTwo;
        case MatchMode::k4v4:
            return v3::cluster::RaftMatchMode::kFourVsFour;
    }
    throw std::invalid_argument("unsupported matchmaking mode");
}

MatchMode from_raft_mode(v3::cluster::RaftMatchMode mode) {
    switch (mode) {
        case v3::cluster::RaftMatchMode::kOneVsOne:
            return MatchMode::k1v1;
        case v3::cluster::RaftMatchMode::kTwoVsTwo:
            return MatchMode::k2v2;
        case v3::cluster::RaftMatchMode::kFourVsFour:
            return MatchMode::k4v4;
    }
    throw std::invalid_argument("unsupported Raft matchmaking mode");
}

std::string make_join_command(const MatchPlayer& player, v3::cluster::RaftWireFormat format) {
    return v3::cluster::serialize_raft_command(v3::cluster::RaftCommand{
        .kind = v3::cluster::RaftCommandKind::kMatchJoin,
        .user_id = player.user_id,
        .mode = to_raft_mode(player.mode),
        .mmr = player.mmr,
        .queued_at_ms = player.queued_at_ms,
    }, format);
}

std::string make_leave_command(const std::string& user_id, MatchMode mode,
                               v3::cluster::RaftWireFormat format) {
    return v3::cluster::serialize_raft_command(v3::cluster::RaftCommand{
        .kind = v3::cluster::RaftCommandKind::kMatchLeave,
        .user_id = user_id,
        .mode = to_raft_mode(mode),
    }, format);
}

std::string make_match_found_command(const MatchResult& result,
                                     v3::cluster::RaftWireFormat format) {
    return v3::cluster::serialize_raft_command(v3::cluster::RaftCommand{
        .kind = v3::cluster::RaftCommandKind::kMatchFound,
        .match_id = result.match_id,
        .mode = to_raft_mode(result.mode),
        .avg_mmr = result.avg_mmr,
        .user_ids = result.player_ids,
    }, format);
}

std::string make_purge_command(MatchMode mode, const std::vector<std::string>& user_ids,
                               v3::cluster::RaftWireFormat format) {
    return v3::cluster::serialize_raft_command(v3::cluster::RaftCommand{
        .kind = v3::cluster::RaftCommandKind::kMatchPurge,
        .mode = to_raft_mode(mode),
        .user_ids = user_ids,
    }, format);
}

// MatchQueue
class MatchQueue {
  public:
    void add(MatchPlayer player) {
        std::lock_guard lock(mutex_);
        // Remove existing entry for same user
        players_.erase(
            std::remove_if(players_.begin(), players_.end(),
                           [&](const MatchPlayer& p) { return p.user_id == player.user_id; }),
            players_.end());
        players_.push_back(std::move(player));
    }

    bool remove(const std::string& user_id) {
        std::lock_guard lock(mutex_);
        auto it = std::find_if(players_.begin(), players_.end(),
                               [&](const MatchPlayer& p) { return p.user_id == user_id; });
        if (it == players_.end())
            return false;
        players_.erase(it);
        return true;
    }

    // Get players eligible for matching within mmr_range, sorted by queue time
    std::vector<MatchPlayer> get_eligible(std::int64_t mmr_center, std::int64_t mmr_range) const {
        std::lock_guard lock(mutex_);
        std::vector<MatchPlayer> eligible;
        for (const auto& p : players_) {
            auto diff = p.mmr > mmr_center ? p.mmr - mmr_center : mmr_center - p.mmr;
            if (diff <= mmr_range) {
                eligible.push_back(p);
            }
        }
        std::sort(eligible.begin(), eligible.end(), [](const MatchPlayer& a, const MatchPlayer& b) {
            return a.queued_at_ms < b.queued_at_ms;
        });
        return eligible;
    }

    void remove_players(const std::vector<std::string>& user_ids) {
        std::lock_guard lock(mutex_);
        for (const auto& uid : user_ids) {
            players_.erase(std::remove_if(players_.begin(), players_.end(),
                                          [&](const MatchPlayer& p) { return p.user_id == uid; }),
                           players_.end());
        }
    }

    [[nodiscard]] std::size_t size() const {
        std::lock_guard lock(mutex_);
        return players_.size();
    }

    std::vector<std::string> expired_players(std::uint64_t max_wait_ms) const {
        std::lock_guard lock(mutex_);
        const auto now = now_ms();
        std::vector<std::string> expired;
        for (const auto& player : players_) {
            if ((now - player.queued_at_ms) > max_wait_ms) {
                expired.push_back(player.user_id);
            }
        }
        return expired;
    }

    std::vector<MatchPlayer> snapshot_by_queue_time() const {
        std::lock_guard lock(mutex_);
        auto players = players_;
        std::sort(players.begin(), players.end(), [](const MatchPlayer& a, const MatchPlayer& b) {
            return a.queued_at_ms < b.queued_at_ms;
        });
        return players;
    }

  private:
    mutable std::mutex mutex_;
    std::vector<MatchPlayer> players_;
};

// Matchmaker
class Matchmaker {
  public:
    explicit Matchmaker(MatchmakingConfig config) : config_(config), running_(false) {}

    void start() {
        running_ = true;
        thread_ = std::thread([this]() { run(); });
    }

    void stop() {
        running_ = false;
        if (thread_.joinable())
            thread_.join();
    }

    void join_queue(const MatchPlayer& player, bool match_immediately = true) {
        queues_[static_cast<int>(player.mode)].add(player);
        if (match_immediately) {
            try_match_mode(player.mode);
        }
    }

    bool leave_queue(MatchMode mode, const std::string& user_id) {
        return queues_[static_cast<int>(mode)].remove(user_id);
    }

    void commit_match(const MatchResult& result) {
        queues_[static_cast<int>(result.mode)].remove_players(result.player_ids);
    }

    void remove_players(MatchMode mode, const std::vector<std::string>& user_ids) {
        queues_[static_cast<int>(mode)].remove_players(user_ids);
    }

    [[nodiscard]] std::size_t queue_size(MatchMode mode) const {
        return queues_[static_cast<int>(mode)].size();
    }

    // Callback when a match is found: (MatchResult) -> void
    using MatchCallback = std::function<void(MatchResult)>;
    void set_match_callback(MatchCallback cb) {
        on_match_ = std::move(cb);
    }
    using PurgeCallback = std::function<void(MatchMode, std::vector<std::string>)>;
    void set_purge_callback(PurgeCallback cb) {
        on_purge_ = std::move(cb);
    }

  private:
    void try_match_mode(MatchMode mode) {
        auto& queue = queues_[static_cast<int>(mode)];
        const auto required = players_for_mode(mode);
        std::vector<MatchPlayer> eligible;
        for (const auto& anchor : queue.snapshot_by_queue_time()) {
            eligible = queue.get_eligible(anchor.mmr, config_.mmr_range_initial);
            if (eligible.size() >= static_cast<std::size_t>(required)) {
                break;
            }
            eligible.clear();
        }
        if (eligible.empty()) {
            return;
        }

        MatchResult result;
        result.match_id = "match_" + std::to_string(g_next_match_id++);
        result.mode = mode;
        std::int64_t total_mmr = 0;
        for (std::size_t i = 0; i < static_cast<std::size_t>(required); ++i) {
            result.player_ids.push_back(eligible[i].user_id);
            total_mmr += eligible[i].mmr;
        }
        result.avg_mmr = total_mmr / required;

        if (on_match_) {
            on_match_(std::move(result));
        }
    }

    void run() {
        while (running_) {
            std::this_thread::sleep_for(std::chrono::milliseconds(config_.match_check_interval_ms));

            for (int m = 0; m < 3; ++m) {
                auto mode = static_cast<MatchMode>(m);
                auto& queue = queues_[m];
                try_match_mode(mode);

                auto expired = queue.expired_players(config_.max_wait_ms);
                if (!expired.empty()) {
                    if (on_purge_) {
                        on_purge_(mode, std::move(expired));
                    } else {
                        queue.remove_players(expired);
                    }
                }

                if (queue.size() < static_cast<std::size_t>(players_for_mode(mode)))
                    continue;
            }
        }
    }

    MatchmakingConfig config_;
    std::atomic<bool> running_;
    std::thread thread_;
    MatchQueue queues_[3]; // indexed by MatchMode
    MatchCallback on_match_;
    PurgeCallback on_purge_;
};

} // namespace

// MatchmakingService
class MatchmakingService::Impl {
  public:
    explicit Impl(std::uint16_t port)
        : port_(port), matchmaker_(std::make_unique<Matchmaker>(matchmaker_config_)) {}

    void start() {
        matchmaker_->set_match_callback([this](MatchResult result) {
            if (raft_node_) {
                if (!raft_node_->is_leader()) {
                    return;
                }
                const auto appended = raft_node_->append_command(
                    make_match_found_command(result, raft_node_->active_writer_format()));
                (void)appended;
                return;
            }
            apply_match_found(result);
        });
        matchmaker_->set_purge_callback([this](MatchMode mode, std::vector<std::string> user_ids) {
            if (raft_node_) {
                if (!raft_node_->is_leader() || user_ids.empty()) {
                    return;
                }
                const auto appended =
                    raft_node_->append_command(
                        make_purge_command(mode, user_ids, raft_node_->active_writer_format()));
                (void)appended;
                return;
            }
            apply_match_purge(mode, user_ids);
        });

        v2::service::BackendServer::HandlerMap handlers;
        handlers["match_join"] = [this](const v2::service::BackendEnvelope& req) {
            return handle_match_join(req);
        };
        handlers["match_leave"] = [this](const v2::service::BackendEnvelope& req) {
            return handle_match_leave(req);
        };
        handlers["match_status"] = [this](const v2::service::BackendEnvelope& req) {
            return handle_match_status(req);
        };

        // v3.0.0: Raft RPC handlers for inter-node consensus.
        handlers["raft_request_vote"] = [this](const v2::service::BackendEnvelope& req) {
            return handle_raft_request_vote(req);
        };
        handlers["raft_append_entries"] = [this](const v2::service::BackendEnvelope& req) {
            return handle_raft_append_entries(req);
        };
        handlers["raft_capabilities"] = [this](const v2::service::BackendEnvelope& req) {
            return handle_raft_capabilities(req);
        };

        server_ = std::make_unique<v2::service::BackendServer>(
            v2::service::BackendServerOptions{.port = port_, .tls_config = tls_config_},
            std::move(handlers));

        try {
            if (!raft_config_.node_id.empty() && !raft_config_.peers.empty()) {
                raft_node_ = std::make_unique<v3::cluster::RaftNode>(raft_config_);
                raft_node_->set_rpc_sender(make_raft_rpc_sender());
                raft_node_->on_become_leader([this]() { leader_.store(true); });
                raft_node_->on_step_down([this]() { leader_.store(false); });
                raft_node_->on_apply(
                    [this](std::uint64_t /*index*/, const v3::cluster::LogEntry& entry) {
                        return apply_raft_entry(entry.command);
                    });
                raft_node_->start();
            }

            server_->start();
            if (raft_node_)
                raft_node_->refresh_peer_capabilities();
            matchmaker_->start();
        } catch (...) {
            matchmaker_->stop();
            if (raft_node_)
                raft_node_->stop();
            if (server_)
                server_->stop();
            throw;
        }
    }

    void stop() {
        matchmaker_->stop();
        if (raft_node_)
            raft_node_->stop();
        if (server_)
            server_->stop();
    }

    std::uint16_t local_port() const {
        return server_ ? server_->local_port() : port_;
    }

    // v3.0.0: Configure Raft consensus.
    void set_matchmaking_config(MatchmakingConfig config) {
        matchmaker_config_ = std::move(config);
        matchmaker_ = std::make_unique<Matchmaker>(matchmaker_config_);
    }

    void set_raft_config(v3::cluster::RaftConfig config) {
        raft_config_ = std::move(config);
    }

    [[nodiscard]] bool is_raft_leader() const {
        return raft_node_ && raft_node_->is_leader();
    }

    // v3.4.0: Non-leader redirect with leader hint.
    std::string get_leader_hint() const {
        if (!raft_node_)
            return {};
        auto lid = raft_node_->leader_id();
        if (lid.empty())
            return {};
        for (const auto& peer : raft_config_.peers) {
            if (peer.id == lid) {
                return "{\"leader_id\":\"" + lid + "\",\"leader_host\":\"" + peer.host +
                       "\",\"leader_port\":" + std::to_string(peer.port) + "}";
            }
        }
        return "{\"leader_id\":\"" + lid + "\"}";
    }

    void set_tls_config(std::optional<v3::cluster::TlsSessionConfig> tls_config) {
        tls_config_ = std::move(tls_config);
    }

    [[nodiscard]] const v3::cluster::RaftConfig& raft_config() const {
        return raft_config_;
    }

    void set_match_found_callback(MatchFoundCallback cb) {
        match_found_callback_ = std::move(cb);
    }

  private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    std::optional<v3::cluster::TlsSessionConfig> tls_config_;
    MatchmakingConfig matchmaker_config_{};
    std::unique_ptr<Matchmaker> matchmaker_;
    std::mutex matches_mutex_;
    std::unordered_map<std::string, MatchResult> pending_matches_;
    MatchFoundCallback match_found_callback_;

    // v3.0.0: Raft consensus members
    v3::cluster::RaftConfig raft_config_;
    std::unique_ptr<v3::cluster::RaftNode> raft_node_;
    std::atomic<bool> leader_{false};

    v2::service::BackendEnvelope make_ok(nlohmann::json extra = {}) {
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        nlohmann::json body{{"status", "ok"}};
        for (auto& [k, v] : extra.items())
            body[k] = std::move(v);
        resp.payload = body.dump();
        return resp;
    }

    v2::service::BackendEnvelope make_error(int code, const std::string& reason) {
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kError;
        resp.error_code = code;
        resp.payload = R"({"status":"error","reason":")" + reason + "\"}";
        return resp;
    }

    v2::service::BackendEnvelope
    wrap_error_if_needed(const std::optional<v3::proto::TypedEnvelope>& request_envelope, int code,
                         const std::string& reason, v3::proto::EnvelopeMessageKind response_kind) {
        auto resp = make_error(code, reason);
        return v2::service::wrap_typed_response_if_needed(request_envelope, std::move(resp),
                                                          response_kind);
    }

    // v3.0.0: Raft RPC handlers for inter-node consensus.
    v2::service::BackendEnvelope handle_raft_request_vote(const v2::service::BackendEnvelope& req) {
        if (!raft_node_) {
            return make_error(-1003, "raft_not_initialized");
        }

        v3::cluster::RequestVoteArgs args;
        try {
            args = v3::cluster::parse_request_vote(req.payload);
        } catch (const std::exception&) {
            return make_error(-1004, "invalid_json");
        }

        auto reply = raft_node_->handle_request_vote(args);
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = v3::cluster::serialize_request_vote_reply(
            reply, v3::cluster::detect_raft_wire_format(req.payload));
        return resp;
    }

    v2::service::BackendEnvelope
    handle_raft_append_entries(const v2::service::BackendEnvelope& req) {
        if (!raft_node_) {
            return make_error(-1003, "raft_not_initialized");
        }

        v3::cluster::AppendEntriesArgs args;
        try {
            args = v3::cluster::parse_append_entries(req.payload);
        } catch (const std::exception&) {
            return make_error(-1004, "invalid_json");
        }

        auto reply = raft_node_->handle_append_entries(args);
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        resp.payload = v3::cluster::serialize_append_entries_reply(
            reply, v3::cluster::detect_raft_wire_format(req.payload));
        return resp;
    }

    v2::service::BackendEnvelope handle_raft_capabilities(
        const v2::service::BackendEnvelope& req) {
        if (!raft_node_) {
            return make_error(-1003, "raft_not_initialized");
        }
        try {
            const auto reply = raft_node_->handle_capability_request(
                v3::cluster::parse_raft_capability_request(req.payload));
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = v3::cluster::serialize_raft_capability_reply(reply);
            return resp;
        } catch (const std::exception&) {
            return make_error(-1004, "invalid_raft_capability");
        }
    }

    v2::service::BackendEnvelope handle_match_join(const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        const std::string user_id = doc.value("user_id", "");
        const std::int64_t mmr = doc.value("mmr", 1000);
        const std::string mode_str = doc.value("mode", "1v1");
        if (user_id.empty()) {
            return make_error(-1004, "empty_user_id");
        }

        MatchPlayer player{
            .user_id = user_id,
            .mmr = mmr,
            .queued_at_ms = doc.value("queued_at_ms", now_ms()),
            .mode = parse_mode(mode_str),
        };

        if (raft_node_) {
            if (!raft_node_->is_leader()) {
                return wrap_error_if_needed(decoded->typed_request, -1003,
                                            "not_raft_leader:" + get_leader_hint(),
                                            v3::proto::EnvelopeMessageKind::kMatchJoinResponse);
            }
            if (!raft_node_->append_command(
                    make_join_command(player, raft_node_->active_writer_format()))) {
                return wrap_error_if_needed(decoded->typed_request, -1005, "raft_commit_failed",
                                            v3::proto::EnvelopeMessageKind::kMatchJoinResponse);
            }
        } else {
            apply_match_join(player);
        }

        v2::service::BackendEnvelope resp = make_ok({{"queued", true}, {"mode", mode_str}});
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request, std::move(resp),
            v3::proto::EnvelopeMessageKind::kMatchJoinResponse);
    }

    v2::service::BackendEnvelope handle_match_leave(const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        const std::string user_id = doc.value("user_id", "");
        const std::string mode_str = doc.value("mode", "1v1");
        const auto mode = parse_mode(mode_str);

        if (raft_node_) {
            if (!raft_node_->is_leader()) {
                return wrap_error_if_needed(decoded->typed_request, -1003,
                                            "not_raft_leader:" + get_leader_hint(),
                                            v3::proto::EnvelopeMessageKind::kMatchLeaveResponse);
            }
            if (!raft_node_->append_command(
                    make_leave_command(user_id, mode, raft_node_->active_writer_format()))) {
                return wrap_error_if_needed(decoded->typed_request, -1005, "raft_commit_failed",
                                            v3::proto::EnvelopeMessageKind::kMatchLeaveResponse);
            }
        } else {
            apply_match_leave(mode, user_id);
        }
        v2::service::BackendEnvelope resp = make_ok({{"left", true}});
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request, std::move(resp),
            v3::proto::EnvelopeMessageKind::kMatchLeaveResponse);
    }

    v2::service::BackendEnvelope handle_match_status(const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;
        std::string user_id = doc.value("user_id", "");
        std::string mode_str = doc.value("mode", "1v1");

        const auto mode = parse_mode(mode_str);

        // Check for pending match
        std::lock_guard lock(matches_mutex_);
        for (auto it = pending_matches_.begin(); it != pending_matches_.end(); ++it) {
            for (const auto& pid : it->second.player_ids) {
                if (pid == user_id) {
                    const auto result = it->second;
                    auto body = nlohmann::json{
                        {"matched", true},
                        {"match_id", result.match_id},
                        {"mode", to_string(result.mode)},
                        {"avg_mmr", result.avg_mmr},
                    };
                    auto resp = make_ok(body);
                    return v2::service::wrap_typed_response_if_needed(
                        decoded->typed_request, std::move(resp),
                        v3::proto::EnvelopeMessageKind::kMatchStatusResponse);
                }
            }
        }

        auto qsize = matchmaker_->queue_size(mode);
        auto body = nlohmann::json{
            {"matched", false},
            {"queue_size", qsize},
            {"mode", mode_str},
        };
        auto resp = make_ok(body);
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request, std::move(resp),
            v3::proto::EnvelopeMessageKind::kMatchStatusResponse);
    }

    void apply_match_join(const MatchPlayer& player, bool match_immediately = true) {
        matchmaker_->join_queue(player, match_immediately);
    }

    void apply_match_leave(MatchMode mode, const std::string& user_id) {
        matchmaker_->leave_queue(mode, user_id);
    }

    void apply_match_found(const MatchResult& result) {
        matchmaker_->commit_match(result);
        std::lock_guard lock(matches_mutex_);
        pending_matches_[result.match_id] = result;

        // Notify the upper-layer callback (e.g. Runtime or test harness)
        // that a match has been found. The callback receives a copy of the
        // result and can dispatch room creation / battle start on its own
        // thread or io_context.
        if (match_found_callback_) {
            try {
                match_found_callback_(result);
            } catch (const std::exception& e) {
                // Callback must not throw; log and continue.
                SPDLOG_ERROR("MatchFoundCallback threw: {}", e.what());
            }
        }
    }

    void apply_match_purge(MatchMode mode, const std::vector<std::string>& user_ids) {
        if (!user_ids.empty()) {
            matchmaker_->remove_players(mode, user_ids);
        }
    }

    bool apply_raft_entry(const std::string& encoded) {
        const auto command = v3::cluster::parse_raft_command(encoded);
        if (command.kind == v3::cluster::RaftCommandKind::kMatchJoin) {
            apply_match_join(MatchPlayer{
                .user_id = command.user_id,
                .mmr = command.mmr,
                .queued_at_ms = command.queued_at_ms,
                .mode = from_raft_mode(command.mode),
            }, false);
            return true;
        }
        if (command.kind == v3::cluster::RaftCommandKind::kMatchLeave) {
            apply_match_leave(from_raft_mode(command.mode), command.user_id);
            return true;
        }
        if (command.kind == v3::cluster::RaftCommandKind::kMatchFound) {
            MatchResult result;
            result.match_id = command.match_id;
            result.mode = from_raft_mode(command.mode);
            result.avg_mmr = command.avg_mmr;
            result.player_ids = command.user_ids;
            apply_match_found(result);
            return true;
        }
        if (command.kind == v3::cluster::RaftCommandKind::kMatchPurge) {
            apply_match_purge(from_raft_mode(command.mode), command.user_ids);
            return true;
        }
        throw std::invalid_argument("leaderboard command routed to matchmaking state machine");
    }
};

// PIMPL

MatchmakingService::MatchmakingService(std::uint16_t port) : impl_(std::make_unique<Impl>(port)) {}

MatchmakingService::~MatchmakingService() = default;

void MatchmakingService::start() {
    impl_->start();
}
void MatchmakingService::stop() {
    impl_->stop();
}
std::uint16_t MatchmakingService::local_port() const {
    return impl_->local_port();
}
void MatchmakingService::set_matchmaking_config(MatchmakingConfig config) {
    impl_->set_matchmaking_config(std::move(config));
}
void MatchmakingService::set_raft_config(v3::cluster::RaftConfig config) {
    impl_->set_raft_config(std::move(config));
}
bool MatchmakingService::is_raft_leader() const {
    return impl_->is_raft_leader();
}

void MatchmakingService::set_tls_config(std::optional<v3::cluster::TlsSessionConfig> tls_config) {
    impl_->set_tls_config(std::move(tls_config));
}

void MatchmakingService::set_match_found_callback(MatchFoundCallback cb) {
    impl_->set_match_found_callback(std::move(cb));
}

} // namespace v2::match
