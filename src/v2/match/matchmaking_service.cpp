// v2.3.0 G1: MMR-based matchmaking backend service.
// v3.0.0 B4: Integrated Raft consensus for leader election.
// Only the Raft leader performs matchmaking to prevent duplicate matches.

#include "v2/match/matchmaking_service.h"
#include "v2/service/backend_connection.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"
#include "v3/cluster/raft.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

namespace v2::match {

namespace {

std::uint64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

std::atomic<std::uint64_t> g_next_match_id{1};

auto make_raft_rpc_sender() {
    return [](const v3::cluster::RaftNodeId& target,
              const std::string& data) -> std::string {
        if (target.host.empty() || target.port == 0) {
            return {};
        }

        auto doc = nlohmann::json::parse(data, nullptr, false);
        if (doc.is_discarded()) {
            return {};
        }

        const auto rpc_type = doc.value("type", "");
        std::string message_type;
        if (rpc_type == "request_vote") {
            message_type = "raft_request_vote";
        } else if (rpc_type == "append_entries") {
            message_type = "raft_append_entries";
        } else {
            return {};
        }

        v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
            .host = target.host,
            .port = target.port,
            .timeout = std::chrono::milliseconds(1000),
            .connect_timeout = std::chrono::milliseconds(500),
        });
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

std::string make_join_command(const MatchPlayer& player) {
    return nlohmann::json{
        {"v", 1},
        {"op", "match_join"},
        {"user_id", player.user_id},
        {"mmr", player.mmr},
        {"queued_at_ms", player.queued_at_ms},
        {"mode", to_string(player.mode)},
    }.dump();
}

std::string make_leave_command(const std::string& user_id, MatchMode mode) {
    return nlohmann::json{
        {"v", 1},
        {"op", "match_leave"},
        {"user_id", user_id},
        {"mode", to_string(mode)},
    }.dump();
}

std::string make_match_found_command(const MatchResult& result) {
    return nlohmann::json{
        {"v", 1},
        {"op", "match_found"},
        {"match_id", result.match_id},
        {"mode", to_string(result.mode)},
        {"avg_mmr", result.avg_mmr},
        {"player_ids", result.player_ids},
    }.dump();
}

std::string make_purge_command(MatchMode mode,
                               const std::vector<std::string>& user_ids) {
    return nlohmann::json{
        {"v", 1},
        {"op", "match_purge"},
        {"mode", to_string(mode)},
        {"user_ids", user_ids},
    }.dump();
}

// ── MatchQueue ─────────────────────────────────────────────────────────

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
        if (it == players_.end()) return false;
        players_.erase(it);
        return true;
    }

    // Get players eligible for matching within mmr_range, sorted by queue time
    std::vector<MatchPlayer> get_eligible(std::int64_t mmr_center,
                                          std::int64_t mmr_range) const {
        std::lock_guard lock(mutex_);
        std::vector<MatchPlayer> eligible;
        for (const auto& p : players_) {
            auto diff = p.mmr > mmr_center ? p.mmr - mmr_center : mmr_center - p.mmr;
            if (diff <= mmr_range) {
                eligible.push_back(p);
            }
        }
        std::sort(eligible.begin(), eligible.end(),
                  [](const MatchPlayer& a, const MatchPlayer& b) {
                      return a.queued_at_ms < b.queued_at_ms;
                  });
        return eligible;
    }

    void remove_players(const std::vector<std::string>& user_ids) {
        std::lock_guard lock(mutex_);
        for (const auto& uid : user_ids) {
            players_.erase(
                std::remove_if(players_.begin(), players_.end(),
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

private:
    mutable std::mutex mutex_;
    std::vector<MatchPlayer> players_;
};

// ── Matchmaker ──────────────────────────────────────────────────────────

class Matchmaker {
public:
    explicit Matchmaker(MatchmakingConfig config)
        : config_(config), running_(false) {}

    void start() {
        running_ = true;
        thread_ = std::thread([this]() { run(); });
    }

    void stop() {
        running_ = false;
        if (thread_.joinable()) thread_.join();
    }

    void join_queue(const MatchPlayer& player) {
        queues_[static_cast<int>(player.mode)].add(player);
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
    void set_match_callback(MatchCallback cb) { on_match_ = std::move(cb); }
    using PurgeCallback = std::function<void(MatchMode, std::vector<std::string>)>;
    void set_purge_callback(PurgeCallback cb) { on_purge_ = std::move(cb); }

private:
    void run() {
        while (running_) {
            std::this_thread::sleep_for(
                std::chrono::milliseconds(config_.match_check_interval_ms));

            for (int m = 0; m < 3; ++m) {
                auto mode = static_cast<MatchMode>(m);
                auto& queue = queues_[m];
                auto required = players_for_mode(mode);


                // Try matching with expanding MMR range
                auto eligible = queue.get_eligible(1000, config_.mmr_range_initial);
                if (eligible.size() >= static_cast<std::size_t>(required)) {
                    // Found enough players — create match
                    MatchResult result;
                    result.match_id = "match_" + std::to_string(g_next_match_id++);
                    result.mode = mode;
                    std::int64_t total_mmr = 0;
                    for (std::size_t i = 0; i < static_cast<std::size_t>(required); ++i) {
                        result.player_ids.push_back(eligible[i].user_id);
                        total_mmr += eligible[i].mmr;
                    }
                    result.avg_mmr = total_mmr / required;

                    if (on_match_) on_match_(result);
                }

                auto expired = queue.expired_players(config_.max_wait_ms);
                if (!expired.empty()) {
                    if (on_purge_) {
                        on_purge_(mode, std::move(expired));
                    } else {
                        queue.remove_players(expired);
                    }
                }

                if (queue.size() < static_cast<std::size_t>(required)) continue;
            }
        }
    }

    MatchmakingConfig config_;
    std::atomic<bool> running_;
    std::thread thread_;
    MatchQueue queues_[3];  // indexed by MatchMode
    MatchCallback on_match_;
    PurgeCallback on_purge_;
};

}  // namespace

// ── MatchmakingService ──────────────────────────────────────────────────

class MatchmakingService::Impl {
public:
    explicit Impl(std::uint16_t port)
        : port_(port),
          matchmaker_(std::make_unique<Matchmaker>(matchmaker_config_)) {}

    void start() {
        // Initialize matchmaker for each mode.
        matchmaker_->set_match_callback([this](MatchResult result) {
            if (raft_node_) {
                if (!raft_node_->is_leader()) {
                    return;
                }
                const auto appended =
                    raft_node_->append_command(make_match_found_command(result));
                (void)appended;
                return;
            }
            apply_match_found(result);
        });
        matchmaker_->set_purge_callback([this](MatchMode mode,
                                               std::vector<std::string> user_ids) {
            if (raft_node_) {
                if (!raft_node_->is_leader() || user_ids.empty()) {
                    return;
                }
                const auto appended =
                    raft_node_->append_command(make_purge_command(mode, user_ids));
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

        server_ = std::make_unique<v2::service::BackendServer>(port_, std::move(handlers));
        server_->start();

        // v3.0.0: Start Raft consensus if configured.
        if (!raft_config_.node_id.empty()) {
            raft_node_ = std::make_unique<v3::cluster::RaftNode>(raft_config_);
            raft_node_->set_rpc_sender(make_raft_rpc_sender());
            raft_node_->on_become_leader([this]() {
                leader_.store(true);
            });
            raft_node_->on_step_down([this]() {
                leader_.store(false);
            });
            raft_node_->on_apply([this](std::uint64_t /*index*/,
                                        const v3::cluster::LogEntry& entry) {
                apply_raft_entry(entry.command);
            });
            raft_node_->start();
        }

        matchmaker_->start();
    }

    void stop() {
        matchmaker_->stop();
        if (raft_node_) raft_node_->stop();
        if (server_) server_->stop();
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

    [[nodiscard]] const v3::cluster::RaftConfig& raft_config() const {
        return raft_config_;
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    MatchmakingConfig matchmaker_config_{};
    std::unique_ptr<Matchmaker> matchmaker_;
    std::mutex matches_mutex_;
    std::unordered_map<std::string, MatchResult> pending_matches_;

    // v3.0.0: Raft consensus members
    v3::cluster::RaftConfig raft_config_;
    std::unique_ptr<v3::cluster::RaftNode> raft_node_;
    std::atomic<bool> leader_{false};

    v2::service::BackendEnvelope make_ok(nlohmann::json extra = {}) {
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
        nlohmann::json body{{"status", "ok"}};
        for (auto& [k, v] : extra.items()) body[k] = std::move(v);
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

    // v3.0.0: Raft RPC handlers for inter-node consensus.
    v2::service::BackendEnvelope handle_raft_request_vote(
        const v2::service::BackendEnvelope& req) {
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
        resp.payload = v3::cluster::serialize_request_vote_reply(reply);
        return resp;
    }

    v2::service::BackendEnvelope handle_raft_append_entries(
        const v2::service::BackendEnvelope& req) {
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
        resp.payload = v3::cluster::serialize_append_entries_reply(reply);
        return resp;
    }

    v2::service::BackendEnvelope handle_match_join(
        const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        const std::string user_id = doc.value("user_id", "");
        const std::int64_t mmr = doc.value("mmr", 1000);
        const std::string mode_str = doc.value("mode", "1v1");

        if (user_id.empty()) return make_error(-1004, "empty_user_id");

        MatchPlayer player{
            .user_id = user_id,
            .mmr = mmr,
            .queued_at_ms = doc.value("queued_at_ms", now_ms()),
            .mode = parse_mode(mode_str),
        };

        if (raft_node_) {
            if (!raft_node_->is_leader()) {
                return make_error(-1003, "not_raft_leader");
            }
            if (!raft_node_->append_command(make_join_command(player))) {
                return make_error(-1005, "raft_commit_failed");
            }
        } else {
            apply_match_join(player);
        }

        auto response_body = nlohmann::json{{"status", "ok"}, {"queued", true}, {"mode", mode_str}};
        v2::service::BackendEnvelope resp = make_ok({{"queued", true}, {"mode", mode_str}});
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kMatchJoinResponse);
    }

    v2::service::BackendEnvelope handle_match_leave(
        const v2::service::BackendEnvelope& req) {
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
                return make_error(-1003, "not_raft_leader");
            }
            if (!raft_node_->append_command(make_leave_command(user_id, mode))) {
                return make_error(-1005, "raft_commit_failed");
            }
        } else {
            apply_match_leave(mode, user_id);
        }
        auto response_body = nlohmann::json{{"status", "ok"}, {"left", true}};
        v2::service::BackendEnvelope resp = make_ok({{"left", true}});
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kMatchLeaveResponse);
    }

    v2::service::BackendEnvelope handle_match_status(
        const v2::service::BackendEnvelope& req) {
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
                        decoded->typed_request,
                        std::move(resp),
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
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kMatchStatusResponse);
    }

    void apply_match_join(const MatchPlayer& player) {
        matchmaker_->join_queue(player);
    }

    void apply_match_leave(MatchMode mode, const std::string& user_id) {
        matchmaker_->leave_queue(mode, user_id);
    }

    void apply_match_found(const MatchResult& result) {
        matchmaker_->commit_match(result);
        std::lock_guard lock(matches_mutex_);
        pending_matches_[result.match_id] = result;
    }

    void apply_match_purge(MatchMode mode, const std::vector<std::string>& user_ids) {
        if (!user_ids.empty()) {
            matchmaker_->remove_players(mode, user_ids);
        }
    }

    void apply_raft_entry(const std::string& command) {
        auto doc = nlohmann::json::parse(command, nullptr, false);
        if (doc.is_discarded()) {
            return;
        }
        if (doc.value("v", 0) != 1) {
            return;
        }

        const auto op = doc.value("op", "");
        if (op == "match_join") {
            const auto user_id = doc.value("user_id", "");
            if (user_id.empty()) {
                return;
            }
            apply_match_join(MatchPlayer{
                .user_id = user_id,
                .mmr = doc.value("mmr", std::int64_t{1000}),
                .queued_at_ms = doc.value("queued_at_ms", std::uint64_t{0}),
                .mode = parse_mode(doc.value("mode", "1v1")),
            });
            return;
        }
        if (op == "match_leave") {
            const auto user_id = doc.value("user_id", "");
            if (user_id.empty()) {
                return;
            }
            apply_match_leave(parse_mode(doc.value("mode", "1v1")), user_id);
            return;
        }
        if (op == "match_found") {
            MatchResult result;
            result.match_id = doc.value("match_id", "");
            result.mode = parse_mode(doc.value("mode", "1v1"));
            result.avg_mmr = doc.value("avg_mmr", std::int64_t{0});
            if (doc.contains("player_ids") && doc["player_ids"].is_array()) {
                result.player_ids = doc["player_ids"].get<std::vector<std::string>>();
            }
            if (!result.match_id.empty() && !result.player_ids.empty()) {
                apply_match_found(result);
            }
            return;
        }
        if (op == "match_purge") {
            if (!doc.contains("user_ids") || !doc["user_ids"].is_array()) {
                return;
            }
            const auto user_ids = doc["user_ids"].get<std::vector<std::string>>();
            apply_match_purge(parse_mode(doc.value("mode", "1v1")), user_ids);
        }
    }
};

// ── PIMPL ──────────────────────────────────────────────────────────────

MatchmakingService::MatchmakingService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}

MatchmakingService::~MatchmakingService() = default;

void MatchmakingService::start() { impl_->start(); }
void MatchmakingService::stop() { impl_->stop(); }
std::uint16_t MatchmakingService::local_port() const { return impl_->local_port(); }
void MatchmakingService::set_matchmaking_config(MatchmakingConfig config) { impl_->set_matchmaking_config(std::move(config)); }
void MatchmakingService::set_raft_config(v3::cluster::RaftConfig config) { impl_->set_raft_config(std::move(config)); }
bool MatchmakingService::is_raft_leader() const { return impl_->is_raft_leader(); }

}  // namespace v2::match
