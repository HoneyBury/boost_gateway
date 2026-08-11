#include <gtest/gtest.h>

#include <memory>
#include <string>
#include <unordered_map>

#include "v2/data/write_behind_store.h"
#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/runtime.h"

namespace {

// ─── In-memory delegate for testing ──────────────────────────────

class InMemoryStore final : public v2::gateway::BattleArchiveSink {
public:
    bool persist(const v2::gateway::Runtime::BattleArchive&) override {
        return true;
    }

    bool save_replay(const std::string& battle_id,
                     std::string_view replay_json) override {
        replays[battle_id] = replay_json;
        return true;
    }

    std::optional<std::string> load_replay(
        const std::string& battle_id) override {
        auto it = replays.find(battle_id);
        if (it != replays.end()) {
            return it->second;
        }
        return std::nullopt;
    }

    bool save_result(const std::string& battle_id,
                     std::string_view result_json) override {
        results[battle_id] = result_json;
        return true;
    }

    std::optional<std::string> load_result(
        const std::string& battle_id) override {
        auto it = results.find(battle_id);
        if (it != results.end()) {
            return it->second;
        }
        return std::nullopt;
    }

    bool save_snapshot(const std::string& battle_id,
                       std::string_view snapshot_json) override {
        snapshots[battle_id] = snapshot_json;
        return true;
    }

    std::optional<std::string> load_snapshot(
        const std::string& battle_id) override {
        auto it = snapshots.find(battle_id);
        if (it != snapshots.end()) {
            return it->second;
        }
        return std::nullopt;
    }

    std::unordered_map<std::string, std::string> replays;
    std::unordered_map<std::string, std::string> results;
    std::unordered_map<std::string, std::string> snapshots;
};

class FailingStore final : public v2::gateway::BattleArchiveSink {
public:
    bool persist(const v2::gateway::Runtime::BattleArchive&) override {
        return false;
    }

    bool save_replay(const std::string&, std::string_view) override {
        return false;
    }

    std::optional<std::string> load_replay(const std::string&) override {
        return std::nullopt;
    }

    bool save_result(const std::string&, std::string_view) override {
        return false;
    }

    std::optional<std::string> load_result(const std::string&) override {
        return std::nullopt;
    }

    bool save_snapshot(const std::string&, std::string_view) override {
        return false;
    }

    std::optional<std::string> load_snapshot(const std::string&) override {
        return std::nullopt;
    }
};

}  // namespace

// ─── Tests ───────────────────────────────────────────────────────

TEST(V2WriteBehindStoreTest, WriteBehindSaveReplayIsFlushed) {
    auto delegate = std::make_shared<InMemoryStore>();
    auto* raw = delegate.get();
    v2::data::WriteBehindDataStore store(std::move(delegate));

    store.save_replay("battle_001", R"({"battle_id":"b001"})");
    store.flush();

    ASSERT_TRUE(raw->replays.contains("battle_001"));
    EXPECT_EQ(raw->replays["battle_001"], R"({"battle_id":"b001"})");
}

TEST(V2WriteBehindStoreTest, WriteBehindSaveResultIsFlushed) {
    auto delegate = std::make_shared<InMemoryStore>();
    auto* raw = delegate.get();
    v2::data::WriteBehindDataStore store(std::move(delegate));

    store.save_result("battle_002", R"({"winner":"alice"})");
    store.flush();

    ASSERT_TRUE(raw->results.contains("battle_002"));
    EXPECT_EQ(raw->results["battle_002"], R"({"winner":"alice"})");
}

TEST(V2WriteBehindStoreTest, WriteBehindSaveSnapshotIsFlushed) {
    auto delegate = std::make_shared<InMemoryStore>();
    auto* raw = delegate.get();
    v2::data::WriteBehindDataStore store(std::move(delegate));

    store.save_snapshot("battle_003", R"({"frame":42})");
    store.flush();

    ASSERT_TRUE(raw->snapshots.contains("battle_003"));
    EXPECT_EQ(raw->snapshots["battle_003"], R"({"frame":42})");
}

TEST(V2WriteBehindStoreTest, WriteBehindMultipleWritesAllFlushed) {
    auto delegate = std::make_shared<InMemoryStore>();
    auto* raw = delegate.get();
    v2::data::WriteBehindDataStore store(std::move(delegate));

    for (int i = 0; i < 10; ++i) {
        const auto id = "battle_" + std::to_string(i);
        store.save_replay(id, R"({"idx":)" + std::to_string(i) + "}");
        store.save_result(id, R"({"idx":)" + std::to_string(i) + "}");
        store.save_snapshot(id, R"({"idx":)" + std::to_string(i) + "}");
    }
    store.flush();

    ASSERT_EQ(raw->replays.size(), 10U);
    ASSERT_EQ(raw->results.size(), 10U);
    ASSERT_EQ(raw->snapshots.size(), 10U);

    for (int i = 0; i < 10; ++i) {
        const auto id = "battle_" + std::to_string(i);
        EXPECT_EQ(raw->replays[id], R"({"idx":)" + std::to_string(i) + "}");
        EXPECT_EQ(raw->results[id], R"({"idx":)" + std::to_string(i) + "}");
        EXPECT_EQ(raw->snapshots[id], R"({"idx":)" + std::to_string(i) + "}");
    }
}

TEST(V2WriteBehindStoreTest, WriteBehindLoadGoesDirectlyToDelegate) {
    auto delegate = std::make_shared<InMemoryStore>();
    auto* raw = delegate.get();
    v2::data::WriteBehindDataStore store(std::move(delegate));

    // Pre-populate the delegate directly.
    raw->replays["battle_010"] = R"({"direct":"replay"})";
    raw->results["battle_010"] = R"({"direct":"result"})";
    raw->snapshots["battle_010"] = R"({"direct":"snapshot"})";

    auto replay = store.load_replay("battle_010");
    ASSERT_TRUE(replay.has_value());
    EXPECT_EQ(*replay, R"({"direct":"replay"})");

    auto result = store.load_result("battle_010");
    ASSERT_TRUE(result.has_value());
    EXPECT_EQ(*result, R"({"direct":"result"})");

    auto snapshot = store.load_snapshot("battle_010");
    ASSERT_TRUE(snapshot.has_value());
    EXPECT_EQ(*snapshot, R"({"direct":"snapshot"})");
}

TEST(V2WriteBehindStoreTest, WriteBehindDestructorFlushesRemaining) {
    auto delegate = std::make_shared<InMemoryStore>();

    {
        v2::data::WriteBehindDataStore store(delegate);
        store.save_replay("battle_020", R"({"destructed":"replay"})");
        store.save_result("battle_020", R"({"destructed":"result"})");
        store.save_snapshot("battle_020", R"({"destructed":"snapshot"})");
        // No explicit flush — destructor will handle it.
    }

    ASSERT_TRUE(delegate->replays.contains("battle_020"));
    EXPECT_EQ(delegate->replays["battle_020"], R"({"destructed":"replay"})");
    ASSERT_TRUE(delegate->results.contains("battle_020"));
    EXPECT_EQ(delegate->results["battle_020"], R"({"destructed":"result"})");
    ASSERT_TRUE(delegate->snapshots.contains("battle_020"));
    EXPECT_EQ(delegate->snapshots["battle_020"], R"({"destructed":"snapshot"})");
}

TEST(V2WriteBehindStoreTest, EmptyDestructorCannotMissWorkerShutdown) {
    auto delegate = std::make_shared<InMemoryStore>();

    // Repeated construction makes it likely that destruction races worker
    // startup; every instance must still join without a lost notification.
    for (int i = 0; i < 200; ++i) {
        v2::data::WriteBehindDataStore store(delegate);
    }
}

TEST(V2WriteBehindStoreTest, WriteBehindFlushReportsDelegateFailures) {
    auto delegate = std::make_shared<FailingStore>();
    v2::data::WriteBehindDataStore store(std::move(delegate));

    store.save_replay("battle_fail", R"({"replay":true})");
    store.save_result("battle_fail", R"({"result":true})");
    store.save_snapshot("battle_fail", R"({"snapshot":true})");
    store.flush();

    const auto stats = store.stats();
    EXPECT_EQ(stats.enqueued, 3U);
    EXPECT_EQ(stats.flushed, 0U);
    EXPECT_EQ(stats.failed, 3U);
    EXPECT_EQ(stats.pending, 0U);
    EXPECT_TRUE(stats.worker_idle);
}

TEST(V2WriteBehindStoreTest, WriteBehindDestructorDrainsLargePendingQueue) {
    auto delegate = std::make_shared<InMemoryStore>();

    {
        v2::data::WriteBehindDataStore store(delegate);
        for (int i = 0; i < 200; ++i) {
            const auto id = "battle_drain_" + std::to_string(i);
            store.save_replay(id, R"({"drain":true})");
        }
        const auto before_flush = store.stats();
        EXPECT_EQ(before_flush.enqueued, 200U);
    }

    EXPECT_EQ(delegate->replays.size(), 200U);
    EXPECT_TRUE(delegate->replays.contains("battle_drain_0"));
    EXPECT_TRUE(delegate->replays.contains("battle_drain_199"));
}
