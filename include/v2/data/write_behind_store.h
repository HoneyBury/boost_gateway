#pragma once

#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/runtime.h"

#include <atomic>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>

namespace v2::data {

class WriteBehindDataStore final : public v2::gateway::BattleArchiveSink {
public:
    struct Stats {
        std::uint64_t enqueued = 0;
        std::uint64_t flushed = 0;
        std::uint64_t failed = 0;
        std::size_t pending = 0;
        bool worker_idle = true;
    };

    explicit WriteBehindDataStore(
        std::shared_ptr<v2::gateway::BattleArchiveSink> delegate);
    ~WriteBehindDataStore() override;

    // Reads go directly to the delegate (synchronous).
    std::optional<std::string> load_replay(
        const std::string& battle_id) override;
    std::optional<std::string> load_result(
        const std::string& battle_id) override;
    std::optional<std::string> load_snapshot(
        const std::string& battle_id) override;

    // Writes are enqueued and return immediately (fire-and-forget).
    bool save_replay(const std::string& battle_id,
                     std::string_view replay_json) override;
    bool save_result(const std::string& battle_id,
                     std::string_view result_json) override;
    bool save_snapshot(const std::string& battle_id,
                       std::string_view snapshot_json) override;
    bool persist(const v2::gateway::Runtime::BattleArchive& archive) override;

    // Blocks until all pending writes are flushed to the delegate.
    void flush();
    [[nodiscard]] Stats stats() const;

private:
    struct WriteCommand {
        enum class Kind { kReplay, kResult, kSnapshot, kPersist };
        Kind kind = Kind::kReplay;
        std::string battle_id;
        std::string json;
        v2::gateway::Runtime::BattleArchive archive{};
    };

    void worker_loop();

    std::shared_ptr<v2::gateway::BattleArchiveSink> delegate_;
    std::deque<WriteCommand> queue_;
    mutable std::mutex mutex_;
    std::condition_variable cv_;
    std::thread worker_;
    std::atomic<bool> running_{true};
    std::uint64_t enqueued_count_ = 0;
    std::uint64_t flushed_count_ = 0;
    std::uint64_t failed_count_ = 0;
    bool worker_idle_ = true;
};

}  // namespace v2::data
