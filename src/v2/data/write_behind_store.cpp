#include "v2/data/write_behind_store.h"

#include <nlohmann/json.hpp>

#include <utility>

namespace v2::data {

WriteBehindDataStore::WriteBehindDataStore(
    std::shared_ptr<v2::gateway::BattleArchiveSink> delegate)
    : delegate_(std::move(delegate)) {
    worker_ = std::thread(&WriteBehindDataStore::worker_loop, this);
}

WriteBehindDataStore::~WriteBehindDataStore() {
    running_ = false;
    cv_.notify_all();
    flush();
    if (worker_.joinable()) {
        worker_.join();
    }
}

// ─── Reads (synchronous, delegate only) ──────────────────────────

std::optional<std::string> WriteBehindDataStore::load_replay(
    const std::string& battle_id) {
    return delegate_->load_replay(battle_id);
}

std::optional<std::string> WriteBehindDataStore::load_result(
    const std::string& battle_id) {
    return delegate_->load_result(battle_id);
}

std::optional<std::string> WriteBehindDataStore::load_snapshot(
    const std::string& battle_id) {
    return delegate_->load_snapshot(battle_id);
}

// ─── Writes (enqueue, fire-and-forget) ───────────────────────────

bool WriteBehindDataStore::save_replay(const std::string& battle_id,
                                       std::string_view replay_json) {
    std::lock_guard lock(mutex_);
    queue_.push_back(WriteCommand{
        .kind = WriteCommand::Kind::kReplay,
        .battle_id = battle_id,
        .json = std::string(replay_json),
    });
    ++enqueued_count_;
    cv_.notify_one();
    return true;
}

bool WriteBehindDataStore::save_result(const std::string& battle_id,
                                       std::string_view result_json) {
    std::lock_guard lock(mutex_);
    queue_.push_back(WriteCommand{
        .kind = WriteCommand::Kind::kResult,
        .battle_id = battle_id,
        .json = std::string(result_json),
    });
    ++enqueued_count_;
    cv_.notify_one();
    return true;
}

bool WriteBehindDataStore::save_snapshot(const std::string& battle_id,
                                         std::string_view snapshot_json) {
    std::lock_guard lock(mutex_);
    queue_.push_back(WriteCommand{
        .kind = WriteCommand::Kind::kSnapshot,
        .battle_id = battle_id,
        .json = std::string(snapshot_json),
    });
    ++enqueued_count_;
    cv_.notify_one();
    return true;
}

bool WriteBehindDataStore::persist(
    const v2::gateway::Runtime::BattleArchive& archive) {
    std::lock_guard lock(mutex_);
    queue_.push_back(WriteCommand{
        .kind = WriteCommand::Kind::kPersist,
        .battle_id = archive.battle_id,
        .json = {},
        .archive = archive,
    });
    ++enqueued_count_;
    cv_.notify_one();
    return true;
}

// ─── Flush ───────────────────────────────────────────────────────

void WriteBehindDataStore::flush() {
    std::unique_lock lock(mutex_);
    cv_.wait(lock, [this] { return queue_.empty() && worker_idle_; });
}

WriteBehindDataStore::Stats WriteBehindDataStore::stats() const {
    std::lock_guard lock(mutex_);
    return Stats{
        .enqueued = enqueued_count_,
        .flushed = flushed_count_,
        .failed = failed_count_,
        .pending = queue_.size(),
        .worker_idle = worker_idle_,
    };
}

// ─── Worker ──────────────────────────────────────────────────────

void WriteBehindDataStore::worker_loop() {
    while (true) {
        WriteCommand cmd;
        {
            std::unique_lock lock(mutex_);
            cv_.wait(lock, [this] {
                return !queue_.empty() || !running_;
            });
            if (!running_ && queue_.empty()) {
                return;
            }
            worker_idle_ = false;
            cmd = std::move(queue_.front());
            queue_.pop_front();
        }

        bool ok = false;
        switch (cmd.kind) {
            case WriteCommand::Kind::kReplay:
                ok = delegate_->save_replay(cmd.battle_id, cmd.json);
                break;
            case WriteCommand::Kind::kResult:
                ok = delegate_->save_result(cmd.battle_id, cmd.json);
                break;
            case WriteCommand::Kind::kSnapshot:
                ok = delegate_->save_snapshot(cmd.battle_id, cmd.json);
                break;
            case WriteCommand::Kind::kPersist: {
                const auto& archive = cmd.archive;
                nlohmann::json scores = nlohmann::json::array();
                for (const auto& score : archive.result.scores) {
                    scores.push_back({
                        {"user_id", score.user_id},
                        {"score", score.score},
                    });
                }

                nlohmann::json report{
                    {"battle_id", archive.battle_id},
                    {"room_id", archive.room_id},
                    {"reason", archive.reason},
                    {"triggering_user_id", archive.triggering_user_id},
                    {"total_frames", archive.total_frames},
                    {"participants", archive.participant_user_ids},
                    {"winner_user_id",
                     archive.result.winner_user_id.has_value()
                         ? nlohmann::json(*archive.result.winner_user_id)
                         : nlohmann::json(nullptr)},
                    {"scores", std::move(scores)},
                };

                const bool result_ok = delegate_->save_result(archive.battle_id, report.dump(2));
                const bool replay_ok = delegate_->save_replay(archive.battle_id,
                                                              archive.replay_payload);
                ok = result_ok && replay_ok;
                break;
            }
        }

        {
            std::lock_guard lock(mutex_);
            if (ok) {
                ++flushed_count_;
            } else {
                ++failed_count_;
            }
            worker_idle_ = true;
        }
        cv_.notify_all();
    }
}

}  // namespace v2::data
