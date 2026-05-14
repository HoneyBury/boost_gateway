// v2.3.0 G2: In-memory sorted-set leaderboard backend service.
// v3.2.0: Optional Redis backend via RedisLeaderboard.
// Supports score submission, Top-K queries, and rank lookup.

#include "v2/leaderboard/leaderboard_service.h"
#include "v2/service/backend_server.h"
#include "v3/persistence/redis_leaderboard.h"

#include <nlohmann/json.hpp>

#include <algorithm>
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

        server_ = std::make_unique<v2::service::BackendServer>(port_, std::move(handlers));
        server_->start();
    }

    void stop() { if (server_) server_->stop(); }
    std::uint16_t local_port() const { return server_ ? server_->local_port() : port_; }

    void set_redis_leaderboard(
        std::shared_ptr<v3::persistence::RedisLeaderboard> redis_lb) {
        redis_lb_ = std::move(redis_lb);
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    SortedSet leaderboard_;
    std::shared_ptr<v3::persistence::RedisLeaderboard> redis_lb_;

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
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        if (doc.is_discarded()) return make_error(-1004, "invalid_json");

        std::string user_id = doc.value("user_id", "");
        std::string display_name = doc.value("display_name", "");
        std::int64_t score = doc.value("score", 0);

        if (user_id.empty()) return make_error(-1004, "empty_user_id");

        std::optional<std::int64_t> new_rank;

        if (redis_lb_ && redis_lb_->available()) {
            new_rank = redis_lb_->submit(user_id, display_name, score);
        } else {
            leaderboard_.submit(user_id, display_name, score);
            auto entry = leaderboard_.rank_of(user_id);
            if (entry.has_value()) new_rank = entry->rank;
        }

        nlohmann::json body{{"status", "ok"}, {"user_id", user_id}};
        if (new_rank.has_value()) body["rank"] = *new_rank;
        return make_response(body);
    }

    v2::service::BackendEnvelope handle_top(
        const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        std::size_t k = doc.value("k", 10);
        if (k > 100) k = 100;  // cap

        nlohmann::json arr = nlohmann::json::array();

        if (redis_lb_ && redis_lb_->available()) {
            auto entries = redis_lb_->top_k(k);
            for (const auto& e : entries) {
                arr.push_back({
                    {"rank", e.rank},
                    {"user_id", e.user_id},
                    {"display_name", e.display_name},
                    {"score", e.score},
                });
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
        return make_response({{"status", "ok"}, {"entries", std::move(arr)}});
    }

    v2::service::BackendEnvelope handle_rank(
        const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        std::string user_id = doc.value("user_id", "");
        if (user_id.empty()) return make_error(-1004, "empty_user_id");

        std::optional<v3::persistence::LeaderboardEntry> entry;

        if (redis_lb_ && redis_lb_->available()) {
            entry = redis_lb_->rank_of(user_id);
        } else {
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
            return make_error(-1, "user_not_found");
        }
        return make_response({
            {"status", "ok"},
            {"user_id", entry->user_id},
            {"rank", entry->rank},
            {"score", entry->score},
        });
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

}  // namespace v2::leaderboard
