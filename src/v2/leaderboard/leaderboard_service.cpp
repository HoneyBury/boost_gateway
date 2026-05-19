// v2.3.0 G2: In-memory sorted-set leaderboard backend service.
// v3.2.0: Optional Redis backend via RedisLeaderboard.
// Supports score submission, Top-K queries, and rank lookup.

#include "v2/leaderboard/leaderboard_service.h"
#include "v2/service/backend_connection.h"
#include "v2/service/error_codes.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"
#include "v3/cluster/raft.h"
#include "v3/persistence/redis_leaderboard.h"

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <vector>

namespace v2::leaderboard {

namespace {

// Custom comparator: higher score first, then by user_id for stability.
struct ScoreCompare {
    bool operator()(const std::pair<std::int64_t, std::string>& a,
                    const std::pair<std::int64_t, std::string>& b) const {
        if (a.first != b.first) return a.first > b.first;  // descending score
        return a.second < b.second;  // ascending user_id for tiebreak
    }
};

// ── SortedSet ───────────────────────────────────────────────────────────

class SortedSet {
public:
    void submit(const std::string& user_id, const std::string& display_name,
                std::int64_t score) {
        std::lock_guard lock(mutex_);
        // Remove old entry if exists
        auto old = user_scores_.find(user_id);
        if (old != user_scores_.end()) {
            sorted_.erase({old->second, user_id});
        }
        user_scores_[user_id] = score;
        sorted_.insert({score, user_id});
        if (!display_name.empty()) {
            display_names_[user_id] = display_name;
        }
    }

    std::vector<LeaderboardEntry> top_k(std::size_t k) const {
        std::lock_guard lock(mutex_);
        std::vector<LeaderboardEntry> result;
        std::int64_t rank = 1;
        for (const auto& [score, user_id] : sorted_) {
            if (result.size() >= k) break;
            LeaderboardEntry entry;
            entry.user_id = user_id;
            entry.score = score;
            entry.rank = rank++;
            auto name_it = display_names_.find(user_id);
            if (name_it != display_names_.end()) entry.display_name = name_it->second;
            result.push_back(entry);
        }
        return result;
    }

    std::optional<LeaderboardEntry> rank_of(const std::string& user_id) const {
        std::lock_guard lock(mutex_);
        auto score_it = user_scores_.find(user_id);
        if (score_it == user_scores_.end()) return std::nullopt;

        LeaderboardEntry entry;
        entry.user_id = user_id;
        entry.score = score_it->second;

        std::int64_t rank = 1;
        for (const auto& [s, uid] : sorted_) {
            if (uid == user_id) { entry.rank = rank; break; }
            ++rank;
        }

        auto name_it = display_names_.find(user_id);
        if (name_it != display_names_.end()) entry.display_name = name_it->second;
        return entry;
    }

    std::size_t size() const {
        std::lock_guard lock(mutex_);
        return sorted_.size();
    }

private:
    mutable std::mutex mutex_;
    std::multiset<std::pair<std::int64_t, std::string>, ScoreCompare> sorted_;
    std::map<std::string, std::int64_t> user_scores_;
    std::map<std::string, std::string> display_names_;
};

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

std::string make_submit_command(const std::string& user_id,
                                const std::string& display_name,
                                std::int64_t score) {
    return nlohmann::json{
        {"v", 1},
        {"op", "leaderboard_submit"},
        {"user_id", user_id},
        {"display_name", display_name},
        {"score", score},
    }.dump();
}

}  // namespace

// ── Implementation ──────────────────────────────────────────────────────

class LeaderboardService::Impl {
public:
    explicit Impl(std::uint16_t port) : port_(port) {}

    void start() {
        v2::service::BackendServer::HandlerMap handlers;
        handlers["leaderboard_submit"] = [this](const auto& req) {
            return handle_submit(req);
        };
        handlers["leaderboard_top"] = [this](const auto& req) {
            return handle_top(req);
        };
        handlers["leaderboard_rank"] = [this](const auto& req) {
            return handle_rank(req);
        };
        handlers["raft_request_vote"] = [this](const auto& req) {
            return handle_raft_request_vote(req);
        };
        handlers["raft_append_entries"] = [this](const auto& req) {
            return handle_raft_append_entries(req);
        };

        server_ = std::make_unique<v2::service::BackendServer>(
            v2::service::BackendServerOptions{.port = port_, .tls_config = tls_config_},
            std::move(handlers));
        server_->start();

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
    }

    void stop() {
        if (raft_node_) raft_node_->stop();
        if (server_) server_->stop();
    }
    std::uint16_t local_port() const { return server_ ? server_->local_port() : port_; }

    void set_redis_leaderboard(
        std::shared_ptr<v3::persistence::RedisLeaderboard> redis_lb) {
        redis_lb_ = std::move(redis_lb);
    }

    void set_raft_config(v3::cluster::RaftConfig config) {
        raft_config_ = std::move(config);
    }

    [[nodiscard]] bool is_raft_leader() const {
        return raft_node_ && raft_node_->is_leader();
    }

    void set_tls_config(std::optional<v3::cluster::TlsSessionConfig> tls_config) {
        tls_config_ = std::move(tls_config);
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    std::optional<v3::cluster::TlsSessionConfig> tls_config_;
    SortedSet leaderboard_;
    std::shared_ptr<v3::persistence::RedisLeaderboard> redis_lb_;
    v3::cluster::RaftConfig raft_config_;
    std::unique_ptr<v3::cluster::RaftNode> raft_node_;
    std::atomic<bool> leader_{false};
    std::mutex idempotency_mutex_;
    std::set<std::string> applied_idempotency_keys_;

    v2::service::BackendEnvelope make_response(nlohmann::json body) {
        v2::service::BackendEnvelope resp;
        resp.kind = v2::service::MessageKind::kResponse;
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

    v2::service::BackendEnvelope handle_submit(
        const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;

        const std::string user_id = doc.value("user_id", "");
        const std::string display_name = doc.value("display_name", "");
        const std::int64_t score = doc.value("score", 0);
        const std::string idempotency_key = doc.value("idempotency_key", "");

        if (user_id.empty()) return make_error(-1004, "empty_user_id");

        if (!idempotency_key.empty()) {
            std::lock_guard lock(idempotency_mutex_);
            const auto [_, inserted] = applied_idempotency_keys_.insert(idempotency_key);
            if (!inserted) {
                nlohmann::json body{{"status", "ok"},
                                    {"user_id", user_id},
                                    {"idempotent", true}};
                if (auto current_rank = rank_of(user_id); current_rank.has_value()) {
                    body["rank"] = *current_rank;
                }
                auto resp = make_response(body);
                return v2::service::wrap_typed_response_if_needed(
                    decoded->typed_request,
                    std::move(resp),
                    v3::proto::EnvelopeMessageKind::kLeaderboardSubmitResponse);
            }
        }

        if (raft_node_) {
            if (!raft_node_->is_leader()) {
                return make_error(-1003, "not_raft_leader");
            }
            if (!raft_node_->append_command(
                    make_submit_command(user_id, display_name, score))) {
                return make_error(-1005, "raft_commit_failed");
            }
        } else {
            apply_submit(user_id, display_name, score);
        }

        nlohmann::json body{{"status", "ok"}, {"user_id", user_id}};
        if (auto new_rank = rank_of(user_id); new_rank.has_value()) {
            body["rank"] = *new_rank;
        }
        auto resp = make_response(body);
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kLeaderboardSubmitResponse);
    }

    v2::service::BackendEnvelope handle_top(
        const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;
        std::size_t k = doc.value("k", 10);
        if (k > 100) k = 100;  // cap

        nlohmann::json arr = nlohmann::json::array();

        if (redis_lb_ && redis_lb_->available()) {
            auto entries = redis_lb_->top_k(k);
            if (entries.empty() && leaderboard_.size() > 0) {
                auto fallback_entries = leaderboard_.top_k(k);
                for (const auto& e : fallback_entries) {
                    arr.push_back({
                        {"rank", e.rank},
                        {"user_id", e.user_id},
                        {"display_name", e.display_name},
                        {"score", e.score},
                    });
                }
            } else {
                for (const auto& e : entries) {
                    arr.push_back({
                        {"rank", e.rank},
                        {"user_id", e.user_id},
                        {"display_name", e.display_name},
                        {"score", e.score},
                    });
                }
            }
        } else {
            auto entries = leaderboard_.top_k(k);
            for (const auto& e : entries) {
                arr.push_back({
                    {"rank", e.rank},
                    {"user_id", e.user_id},
                    {"display_name", e.display_name},
                    {"score", e.score},
                });
            }
        }
        auto body = nlohmann::json{{"status", "ok"}, {"entries", std::move(arr)}};
        auto resp = make_response(body);
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kLeaderboardTopResponse);
    }

    v2::service::BackendEnvelope handle_rank(
        const v2::service::BackendEnvelope& req) {
        auto decoded = v2::service::decode_handler_payload(req);
        if (!decoded.has_value() || !decoded->payload.is_object()) {
            return make_error(-1004, "invalid_json");
        }
        const auto& doc = decoded->payload;
        std::string user_id = doc.value("user_id", "");
        if (user_id.empty()) return make_error(-1004, "empty_user_id");

        std::optional<v3::persistence::LeaderboardEntry> entry;

        if (redis_lb_ && redis_lb_->available()) {
            entry = redis_lb_->rank_of(user_id);
        }
        if (!entry.has_value()) {
            auto mem_entry = leaderboard_.rank_of(user_id);
            if (mem_entry.has_value()) {
                entry = v3::persistence::LeaderboardEntry{
                    .user_id = mem_entry->user_id,
                    .display_name = mem_entry->display_name,
                    .score = mem_entry->score,
                    .rank = mem_entry->rank,
                };
            }
        }

        if (!entry.has_value()) {
            return make_error(
                static_cast<std::int32_t>(v2::service::ServiceErrorCode::kRejected),
                "user_not_found");
        }
        auto body = nlohmann::json{
            {"status", "ok"},
            {"user_id", entry->user_id},
            {"rank", entry->rank},
            {"score", entry->score},
        };
        auto resp = make_response(body);
        return v2::service::wrap_typed_response_if_needed(
            decoded->typed_request,
            std::move(resp),
            v3::proto::EnvelopeMessageKind::kLeaderboardRankResponse);
    }

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

    void apply_submit(const std::string& user_id,
                      const std::string& display_name,
                      std::int64_t score) {
        leaderboard_.submit(user_id, display_name, score);
        if (redis_lb_ && redis_lb_->available()) {
            redis_lb_->submit(user_id, display_name, score);
        }
    }

    std::optional<std::int64_t> rank_of(const std::string& user_id) const {
        if (redis_lb_ && redis_lb_->available()) {
            if (auto entry = redis_lb_->rank_of(user_id); entry.has_value()) {
                return entry->rank;
            }
        }
        if (auto entry = leaderboard_.rank_of(user_id); entry.has_value()) {
            return entry->rank;
        }
        return std::nullopt;
    }

    void apply_raft_entry(const std::string& command) {
        auto doc = nlohmann::json::parse(command, nullptr, false);
        if (doc.is_discarded()) {
            return;
        }
        if (doc.value("v", 0) != 1) {
            return;
        }
        if (doc.value("op", "") != "leaderboard_submit") {
            return;
        }
        const auto user_id = doc.value("user_id", "");
        if (user_id.empty()) {
            return;
        }
        apply_submit(user_id,
                     doc.value("display_name", ""),
                     doc.value("score", std::int64_t{0}));
    }
};

LeaderboardService::LeaderboardService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}
LeaderboardService::~LeaderboardService() = default;
void LeaderboardService::start() { impl_->start(); }
void LeaderboardService::stop() { impl_->stop(); }
std::uint16_t LeaderboardService::local_port() const { return impl_->local_port(); }

void LeaderboardService::set_redis_leaderboard(
    std::shared_ptr<v3::persistence::RedisLeaderboard> redis_lb) {
    impl_->set_redis_leaderboard(std::move(redis_lb));
}

void LeaderboardService::set_raft_config(v3::cluster::RaftConfig config) {
    impl_->set_raft_config(std::move(config));
}

bool LeaderboardService::is_raft_leader() const {
    return impl_->is_raft_leader();
}

void LeaderboardService::set_tls_config(
    std::optional<v3::cluster::TlsSessionConfig> tls_config) {
    impl_->set_tls_config(std::move(tls_config));
}

}  // namespace v2::leaderboard
