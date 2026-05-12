// v2.3.0 G1: MMR-based matchmaking backend service.
// Manages match queues per mode, periodically checks for eligible matches,
// and creates rooms when matches are found.

#include "v2/match/matchmaking_service.h"
#include "v2/service/backend_server.h"

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

    // Remove timed-out players, return removed count
    std::size_t purge_expired(std::uint64_t max_wait_ms) {
        std::lock_guard lock(mutex_);
        auto now = now_ms();
        auto before = players_.size();
        players_.erase(
            std::remove_if(players_.begin(), players_.end(),
                           [&](const MatchPlayer& p) {
                               return (now - p.queued_at_ms) > max_wait_ms;
                           }),
            players_.end());
        return before - players_.size();
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

    [[nodiscard]] std::size_t queue_size(MatchMode mode) const {
        return queues_[static_cast<int>(mode)].size();
    }

    // Callback when a match is found: (MatchResult) -> void
    using MatchCallback = std::function<void(MatchResult)>;
    void set_match_callback(MatchCallback cb) { on_match_ = std::move(cb); }

private:
    void run() {
        while (running_) {
            std::this_thread::sleep_for(
                std::chrono::milliseconds(config_.match_check_interval_ms));

            for (int m = 0; m < 3; ++m) {
                auto mode = static_cast<MatchMode>(m);
                auto& queue = queues_[m];
                auto required = players_for_mode(mode);

                if (queue.size() < static_cast<std::size_t>(required)) continue;

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

                    // Remove matched players from queue
                    queue.remove_players(result.player_ids);

                    if (on_match_) on_match_(result);
                }

                // Purge expired players
                queue.purge_expired(config_.max_wait_ms);
            }
        }
    }

    MatchmakingConfig config_;
    std::atomic<bool> running_;
    std::thread thread_;
    MatchQueue queues_[3];  // indexed by MatchMode
    MatchCallback on_match_;
};

}  // namespace

// ── MatchmakingService ──────────────────────────────────────────────────

class MatchmakingService::Impl {
public:
    explicit Impl(std::uint16_t port) : port_(port) {}

    void start() {
        // Initialize matchmaker for each mode
        matchmaker_.set_match_callback([this](MatchResult result) {
            // When a match is found, broadcast to all matched players
            for (const auto& user_id : result.player_ids) {
                nlohmann::json push{
                    {"match_id", result.match_id},
                    {"mode", to_string(result.mode)},
                    {"avg_mmr", result.avg_mmr},
                };
                // Push notification would be sent via GatewayServiceBridge
                // For now, store for polling
                std::lock_guard lock(matches_mutex_);
                pending_matches_[result.match_id] = result;
            }
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

        server_ = std::make_unique<v2::service::BackendServer>(port_, std::move(handlers));
        server_->start();
        matchmaker_.start();
    }

    void stop() {
        matchmaker_.stop();
        if (server_) server_->stop();
    }

    std::uint16_t local_port() const {
        return server_ ? server_->local_port() : port_;
    }

private:
    std::uint16_t port_;
    std::unique_ptr<v2::service::BackendServer> server_;
    Matchmaker matchmaker_{{}};
    std::mutex matches_mutex_;
    std::unordered_map<std::string, MatchResult> pending_matches_;

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

    v2::service::BackendEnvelope handle_match_join(
        const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        if (doc.is_discarded()) return make_error(-1004, "invalid_json");

        std::string user_id = doc.value("user_id", "");
        std::int64_t mmr = doc.value("mmr", 1000);
        std::string mode_str = doc.value("mode", "1v1");

        if (user_id.empty()) return make_error(-1004, "empty_user_id");

        MatchMode mode = MatchMode::k1v1;
        if (mode_str == "2v2") mode = MatchMode::k2v2;
        else if (mode_str == "4v4") mode = MatchMode::k4v4;

        matchmaker_.join_queue(MatchPlayer{
            .user_id = user_id,
            .mmr = mmr,
            .queued_at_ms = now_ms(),
            .mode = mode,
        });

        return make_ok({{"queued", true}, {"mode", mode_str}});
    }

    v2::service::BackendEnvelope handle_match_leave(
        const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        if (doc.is_discarded()) return make_error(-1004, "invalid_json");

        std::string user_id = doc.value("user_id", "");
        std::string mode_str = doc.value("mode", "1v1");
        MatchMode mode = MatchMode::k1v1;
        if (mode_str == "2v2") mode = MatchMode::k2v2;
        else if (mode_str == "4v4") mode = MatchMode::k4v4;

        matchmaker_.leave_queue(mode, user_id);
        return make_ok({{"left", true}});
    }

    v2::service::BackendEnvelope handle_match_status(
        const v2::service::BackendEnvelope& req) {
        auto doc = nlohmann::json::parse(req.payload, nullptr, false);
        std::string user_id = doc.value("user_id", "");
        std::string mode_str = doc.value("mode", "1v1");

        MatchMode mode = MatchMode::k1v1;
        if (mode_str == "2v2") mode = MatchMode::k2v2;
        else if (mode_str == "4v4") mode = MatchMode::k4v4;

        // Check for pending match
        std::lock_guard lock(matches_mutex_);
        for (auto it = pending_matches_.begin(); it != pending_matches_.end(); ++it) {
            for (const auto& pid : it->second.player_ids) {
                if (pid == user_id) {
                    auto result = it->second;
                    pending_matches_.erase(it);
                    return make_ok({
                        {"matched", true},
                        {"match_id", result.match_id},
                        {"mode", to_string(result.mode)},
                        {"avg_mmr", result.avg_mmr},
                    });
                }
            }
        }

        auto qsize = matchmaker_.queue_size(mode);
        return make_ok({
            {"matched", false},
            {"queue_size", qsize},
            {"mode", mode_str},
        });
    }
};

// ── PIMPL ──────────────────────────────────────────────────────────────

MatchmakingService::MatchmakingService(std::uint16_t port)
    : impl_(std::make_unique<Impl>(port)) {}

MatchmakingService::~MatchmakingService() = default;

void MatchmakingService::start() { impl_->start(); }
void MatchmakingService::stop() { impl_->stop(); }
std::uint16_t MatchmakingService::local_port() const { return impl_->local_port(); }

}  // namespace v2::match
