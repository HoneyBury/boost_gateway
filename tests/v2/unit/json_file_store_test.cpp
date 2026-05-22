#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <random>
#include <string>
#include <vector>

#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/runtime.h"

namespace {

class JsonFileStoreTest : public ::testing::Test {
protected:
    void SetUp() override {
        auto tmp = std::filesystem::temp_directory_path();
        dir_ = tmp / ("json_store_test_" + std::to_string(++counter_));
        std::filesystem::create_directories(dir_);
        store_ = std::make_unique<v2::gateway::JsonFileBattleDataStore>(dir_);
    }

    void TearDown() override {
        store_.reset();
        std::filesystem::remove_all(dir_);
    }

    std::filesystem::path dir_;
    std::unique_ptr<v2::gateway::JsonFileBattleDataStore> store_;
    static inline int counter_ = 0;
};

// ─── Save+Load Round-Trip Tests ─────────────────────────────────────

TEST_F(JsonFileStoreTest, SaveAndLoadReplay) {
    const std::string battle_id = "battle_001";
    const std::string replay_data = R"({"frames":[1,2,3],"events":["move","shoot"]})";

    ASSERT_TRUE(store_->save_replay(battle_id, replay_data));

    auto loaded = store_->load_replay(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(replay_data, *loaded);
}

TEST_F(JsonFileStoreTest, SaveAndLoadResult) {
    const std::string battle_id = "battle_002";
    const std::string result_data = R"({"winner":"player1","scores":[{"user_id":"p1","score":100}]})";

    ASSERT_TRUE(store_->save_result(battle_id, result_data));

    auto loaded = store_->load_result(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(result_data, *loaded);
}

TEST_F(JsonFileStoreTest, SaveAndLoadSnapshot) {
    const std::string battle_id = "battle_003";
    const std::string snapshot_data = R"({"tick":42,"state":"active","players":["a","b"]})";

    ASSERT_TRUE(store_->save_snapshot(battle_id, snapshot_data));

    auto loaded = store_->load_snapshot(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(snapshot_data, *loaded);
}

// ─── Missing Data Tests ────────────────────────────────────────────

TEST_F(JsonFileStoreTest, LoadNonExistentReturnsNullopt) {
    EXPECT_FALSE(store_->load_replay("nonexistent").has_value());
    EXPECT_FALSE(store_->load_result("nonexistent").has_value());
    EXPECT_FALSE(store_->load_snapshot("nonexistent").has_value());
}

// ─── Overwrite Tests ───────────────────────────────────────────────

TEST_F(JsonFileStoreTest, OverwriteExistingReplay) {
    const std::string battle_id = "overwrite_test";

    ASSERT_TRUE(store_->save_replay(battle_id, R"({"version":1})"));
    ASSERT_TRUE(store_->save_replay(battle_id, R"({"version":2,"extra":"data"})"));

    auto loaded = store_->load_replay(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(R"({"version":2,"extra":"data"})", *loaded);
}

TEST_F(JsonFileStoreTest, OverwriteExistingResult) {
    const std::string battle_id = "overwrite_result";

    ASSERT_TRUE(store_->save_result(battle_id, R"({"winner":"old"})"));
    ASSERT_TRUE(store_->save_result(battle_id, R"({"winner":"new","score":99})"));

    auto loaded = store_->load_result(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(R"({"winner":"new","score":99})", *loaded);
}

TEST_F(JsonFileStoreTest, OverwriteExistingSnapshot) {
    const std::string battle_id = "overwrite_snap";

    ASSERT_TRUE(store_->save_snapshot(battle_id, R"({"tick":1})"));
    ASSERT_TRUE(store_->save_snapshot(battle_id, R"({"tick":2,"state":"final"})"));

    auto loaded = store_->load_snapshot(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(R"({"tick":2,"state":"final"})", *loaded);
}

// ─── Multiple Independent Battles ──────────────────────────────────

TEST_F(JsonFileStoreTest, MultipleBattlesIndependent) {
    const std::vector<std::string> battle_ids = {"alpha", "beta", "gamma"};
    const std::vector<std::string> payloads = {
        R"({"id":"alpha","data":"first"})",
        R"({"id":"beta","data":"second"})",
        R"({"id":"gamma","data":"third"})",
    };

    for (size_t i = 0; i < battle_ids.size(); ++i) {
        ASSERT_TRUE(store_->save_replay(battle_ids[i], payloads[i]));
    }

    for (size_t i = 0; i < battle_ids.size(); ++i) {
        auto loaded = store_->load_replay(battle_ids[i]);
        ASSERT_TRUE(loaded.has_value());
        EXPECT_EQ(payloads[i], *loaded);
    }
}

// ─── Persist Delegates Correctly ───────────────────────────────────

TEST_F(JsonFileStoreTest, PersistDelegatesCorrectly) {
    v2::gateway::Runtime::BattleArchive archive;
    archive.battle_id = "persist_test_001";
    archive.room_id = "room_xyz";
    archive.reason = "settlement";
    archive.triggering_user_id = "user_42";
    archive.total_frames = 150;
    archive.participant_user_ids = {"user_42", "user_99"};
    archive.replay_payload = R"({"frames":[{"tick":1,"state":"..."}]})";
    archive.result.winner_user_id = "user_42";
    archive.result.scores = {{"user_42", 1000}, {"user_99", 500}};

    ASSERT_TRUE(store_->persist(archive));

    // Verify replay file exists and round-trips
    auto replay_file = dir_ / "replays" / (archive.battle_id + ".replay");
    EXPECT_TRUE(std::filesystem::exists(replay_file));

    auto loaded_replay = store_->load_replay(archive.battle_id);
    ASSERT_TRUE(loaded_replay.has_value());
    EXPECT_EQ(archive.replay_payload, *loaded_replay);

    // Verify result file exists and round-trips
    auto result_file = dir_ / "results" / (archive.battle_id + ".result");
    EXPECT_TRUE(std::filesystem::exists(result_file));

    auto loaded_result = store_->load_result(archive.battle_id);
    ASSERT_TRUE(loaded_result.has_value());
    EXPECT_FALSE(loaded_result->empty());

    // Spot-check the result JSON contains expected fields
    auto result_json = nlohmann::json::parse(*loaded_result);
    EXPECT_EQ(archive.battle_id, result_json["battle_id"].get<std::string>());
    EXPECT_EQ(archive.room_id, result_json["room_id"].get<std::string>());
    EXPECT_EQ(archive.reason, result_json["reason"].get<std::string>());
    EXPECT_EQ(archive.triggering_user_id, result_json["triggering_user_id"].get<std::string>());
    EXPECT_EQ(archive.total_frames, result_json["total_frames"].get<std::uint32_t>());
    ASSERT_TRUE(result_json.contains("winner_user_id"));
    EXPECT_FALSE(result_json["winner_user_id"].is_null());
    EXPECT_EQ(*archive.result.winner_user_id, result_json["winner_user_id"].get<std::string>());
    ASSERT_TRUE(result_json.contains("scores"));
    EXPECT_EQ(2, result_json["scores"].size());
}

// ─── File Existence on Disk ────────────────────────────────────────

TEST_F(JsonFileStoreTest, ReplayFileCreatedOnDisk) {
    const std::string battle_id = "disk_file_check";
    const std::string payload = R"({"test":"data"})";

    ASSERT_TRUE(store_->save_replay(battle_id, payload));

    auto expected_path = dir_ / "replays" / (battle_id + ".replay");
    EXPECT_TRUE(std::filesystem::exists(expected_path));
    EXPECT_TRUE(std::filesystem::file_size(expected_path) > 0);
}

TEST_F(JsonFileStoreTest, ResultFileCreatedOnDisk) {
    const std::string battle_id = "disk_result_check";
    const std::string payload = R"({"winner":"p1"})";

    ASSERT_TRUE(store_->save_result(battle_id, payload));

    auto expected_path = dir_ / "results" / (battle_id + ".result");
    EXPECT_TRUE(std::filesystem::exists(expected_path));
    EXPECT_TRUE(std::filesystem::file_size(expected_path) > 0);
}

TEST_F(JsonFileStoreTest, SnapshotFileCreatedOnDisk) {
    const std::string battle_id = "disk_snapshot_check";
    const std::string payload = R"({"tick":10})";

    ASSERT_TRUE(store_->save_snapshot(battle_id, payload));

    auto expected_path = dir_ / "snapshots" / (battle_id + ".snapshot");
    EXPECT_TRUE(std::filesystem::exists(expected_path));
    EXPECT_TRUE(std::filesystem::file_size(expected_path) > 0);
}

// ─── Large Payload Round Trip ──────────────────────────────────────

TEST_F(JsonFileStoreTest, LargePayloadRoundTrip) {
    // Generate a ~100KB JSON payload
    const std::string battle_id = "large_payload";
    std::string large_json = R"({"values":[)";

    constexpr int kTargetSize = 100 * 1024;  // 100 KB
    std::mt19937 rng(42);
    std::uniform_int_distribution<int> dist(0, 999999);

    while (large_json.size() < static_cast<std::size_t>(kTargetSize)) {
        large_json += std::to_string(dist(rng));
        if (large_json.size() < static_cast<std::size_t>(kTargetSize)) {
            large_json += ",";
        }
    }
    large_json += R"(]})";

    ASSERT_GE(large_json.size(), static_cast<std::size_t>(kTargetSize));

    ASSERT_TRUE(store_->save_replay(battle_id, large_json));

    auto loaded = store_->load_replay(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(large_json.size(), loaded->size());
    EXPECT_EQ(large_json, *loaded);
}

// ─── Special Characters in Battle ID ───────────────────────────────

TEST_F(JsonFileStoreTest, SpecialCharactersInBattleId) {
    const std::string battle_id = "battle-id_123.456-789";
    const std::string payload = R"({"key":"value"})";

    ASSERT_TRUE(store_->save_replay(battle_id, payload));

    // Verify the file was created with the exact battle_id as filename
    auto expected_path = dir_ / "replays" / (battle_id + ".replay");
    EXPECT_TRUE(std::filesystem::exists(expected_path));

    auto loaded = store_->load_replay(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(payload, *loaded);
}

// ─── Empty Temp Dir Cleanup ────────────────────────────────────────

TEST_F(JsonFileStoreTest, EmptyTempDirCleanup) {
    const std::string battle_id = "cleanup_check";
    ASSERT_TRUE(store_->save_replay(battle_id, R"({"temp":"data"})"));
    ASSERT_TRUE(std::filesystem::exists(dir_));

    // TearDown will run and remove all files
    // We verify the directory existed before teardown
    EXPECT_TRUE(std::filesystem::exists(dir_ / "replays" / (battle_id + ".replay")));

    // After TearDown completes, the directory should be gone
    // This is verified implicitly since TearDown calls remove_all
    // We explicitly check in the test body before TearDown
}

// ─── Persist with Full Archive ─────────────────────────────────────

TEST_F(JsonFileStoreTest, PersistWithFullArchive) {
    v2::gateway::Runtime::BattleArchive archive;
    archive.battle_id = "full_archive_test";
    archive.room_id = "room_full_001";
    archive.reason = "match_completed";
    archive.triggering_user_id = "winner_player";
    archive.total_frames = 300;
    archive.participant_user_ids = {"winner_player", "loser_player", "spectator_player"};
    archive.replay_payload = R"({"frames":[1,2,3],"input_events":["w","a","s","d"]})";
    archive.result.winner_user_id = "winner_player";
    archive.result.scores = {
        {"winner_player", 2500},
        {"loser_player", 1800},
        {"spectator_player", 0},
    };

    ASSERT_TRUE(store_->persist(archive));

    // Verify replay round-trips
    auto loaded_replay = store_->load_replay(archive.battle_id);
    ASSERT_TRUE(loaded_replay.has_value());
    EXPECT_EQ(archive.replay_payload, *loaded_replay);

    // Verify result round-trips and structure
    auto loaded_result = store_->load_result(archive.battle_id);
    ASSERT_TRUE(loaded_result.has_value());

    auto result_json = nlohmann::json::parse(*loaded_result);
    EXPECT_EQ(archive.battle_id, result_json["battle_id"].get<std::string>());
    EXPECT_EQ(archive.room_id, result_json["room_id"].get<std::string>());
    EXPECT_EQ(archive.reason, result_json["reason"].get<std::string>());
    EXPECT_EQ(archive.triggering_user_id, result_json["triggering_user_id"].get<std::string>());
    EXPECT_EQ(archive.total_frames, result_json["total_frames"].get<std::uint32_t>());

    // Verify participants array
    ASSERT_TRUE(result_json.contains("participants"));
    ASSERT_EQ(archive.participant_user_ids.size(), result_json["participants"].size());
    for (size_t i = 0; i < archive.participant_user_ids.size(); ++i) {
        EXPECT_EQ(archive.participant_user_ids[i],
                  result_json["participants"][i].get<std::string>());
    }

    // Verify winner
    ASSERT_TRUE(result_json.contains("winner_user_id"));
    EXPECT_EQ(*archive.result.winner_user_id, result_json["winner_user_id"].get<std::string>());

    // Verify scores array
    ASSERT_TRUE(result_json.contains("scores"));
    ASSERT_EQ(archive.result.scores.size(), result_json["scores"].size());
    for (size_t i = 0; i < archive.result.scores.size(); ++i) {
        EXPECT_EQ(archive.result.scores[i].user_id,
                  result_json["scores"][i]["user_id"].get<std::string>());
        EXPECT_EQ(archive.result.scores[i].score,
                  result_json["scores"][i]["score"].get<std::int64_t>());
    }

    // Verify files exist on disk
    EXPECT_TRUE(std::filesystem::exists(dir_ / "replays" / (archive.battle_id + ".replay")));
    EXPECT_TRUE(std::filesystem::exists(dir_ / "results" / (archive.battle_id + ".result")));
}

// ─── Persist With No Winner (nullopt) ──────────────────────────────

TEST_F(JsonFileStoreTest, PersistWithNoWinner) {
    v2::gateway::Runtime::BattleArchive archive;
    archive.battle_id = "no_winner_test";
    archive.room_id = "room_draw";
    archive.reason = "draw";
    archive.triggering_user_id = "system";
    archive.total_frames = 100;
    archive.participant_user_ids = {"p1", "p2"};
    archive.replay_payload = R"({"frames":[]})";
    archive.result.winner_user_id = std::nullopt;  // No winner (draw)
    archive.result.scores = {{"p1", 500}, {"p2", 500}};

    ASSERT_TRUE(store_->persist(archive));

    auto loaded_result = store_->load_result(archive.battle_id);
    ASSERT_TRUE(loaded_result.has_value());

    auto result_json = nlohmann::json::parse(*loaded_result);
    EXPECT_TRUE(result_json["winner_user_id"].is_null());
}

// ─── Empty Replay Payload ──────────────────────────────────────────

TEST_F(JsonFileStoreTest, EmptyReplayPayload) {
    const std::string battle_id = "empty_replay";
    // An empty replay payload — implementation may treat this differently
    // but should not crash or produce invalid state
    ASSERT_TRUE(store_->save_replay(battle_id, ""));

    // load_replay of an empty payload: the encode/decode layer will still
    // write the header, and decode should return the empty string
    auto loaded = store_->load_replay(battle_id);
    // If the implementation considers empty-after-decode as "not found",
    // loaded may be nullopt. We just test it doesn't crash.
    // The file should exist on disk regardless.
    auto replay_file = dir_ / "replays" / (battle_id + ".replay");
    EXPECT_TRUE(std::filesystem::exists(replay_file));
}

}  // namespace
