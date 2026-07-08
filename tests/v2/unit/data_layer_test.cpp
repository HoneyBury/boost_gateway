#include <gtest/gtest.h>

#include <fstream>
#include <filesystem>
#include <random>

#include <nlohmann/json.hpp>

#include "v2/battle/battle_snapshot.h"
#include "v2/battle/runtime_world.h"
#include "v2/data/replay_format.h"
#include "v2/gateway/battle_data_store.h"
#include "v2/gateway/runtime.h"
namespace fs = std::filesystem;

// ─── Replay Format: Encode/Decode Round-Trip ──────────────────

TEST(V2DataLayerTest, EncodeDecodeReplayRoundTrip) {
    const std::string payload = R"({"battle_id":"b001","frames":[]})";
    const auto encoded = v2::data::encode_replay(payload);
    EXPECT_GT(encoded.size(), payload.size());

    const auto decoded = v2::data::decode_replay(encoded);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(*decoded, payload);
}

TEST(V2DataLayerTest, EncodeDecodeResultRoundTrip) {
    const std::string payload = R"({"battle_id":"b001","winner":"alice"})";
    const auto encoded = v2::data::encode_result(payload);
    EXPECT_GT(encoded.size(), payload.size());

    const auto decoded = v2::data::decode_result(encoded);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(*decoded, payload);
}

TEST(V2DataLayerTest, EncodeDecodeSnapshotRoundTrip) {
    const std::string payload = R"({"clock":{"frame_number":5}})";
    const auto encoded = v2::data::encode_snapshot(payload);
    EXPECT_GT(encoded.size(), payload.size());

    const auto decoded = v2::data::decode_snapshot(encoded);
    ASSERT_TRUE(decoded.has_value());
    EXPECT_EQ(*decoded, payload);
}

// ─── Replay Format: Validation ─────────────────────────────────

TEST(V2DataLayerTest, DecodeReplayRejectsBadMagic) {
    std::string bad(11, 'X');
    EXPECT_FALSE(v2::data::decode_replay(bad).has_value());
}

TEST(V2DataLayerTest, DecodeResultRejectsBadMagic) {
    std::string bad(11, 'X');
    EXPECT_FALSE(v2::data::decode_result(bad).has_value());
}

TEST(V2DataLayerTest, DecodeReplayRejectsTruncatedHeader) {
    std::string truncated("V2RP", 4);  // only magic, no version/length
    EXPECT_FALSE(v2::data::decode_replay(truncated).has_value());
}

TEST(V2DataLayerTest, DecodeReplayRejectsTruncatedPayload) {
    const std::string payload = "hello world";
    const auto encoded = v2::data::encode_replay(payload);
    // Cut off last byte
    const std::string truncated(encoded.begin(), encoded.end() - 1);
    EXPECT_FALSE(v2::data::decode_replay(truncated).has_value());
}

TEST(V2DataLayerTest, ValidateHeaderRejectsWrongMagic) {
    const std::string payload = "{}";
    const auto encoded = v2::data::encode_replay(payload);
    // Check with wrong magic
    EXPECT_FALSE(v2::data::validate_header(encoded, v2::data::kResultMagic));
    // Check with correct magic
    EXPECT_TRUE(v2::data::validate_header(encoded, v2::data::kReplayMagic));
}

TEST(V2DataLayerTest, DecodeHeaderReturnsFields) {
    const std::string payload = "test payload";
    const auto encoded = v2::data::encode_replay(payload);

    const auto header = v2::data::decode_header(encoded);
    ASSERT_TRUE(header.has_value());
    EXPECT_EQ(header->magic, v2::data::kReplayMagic);
    EXPECT_EQ(header->version, v2::data::kCurrentVersion);
    EXPECT_EQ(header->payload_length, payload.size());
}

TEST(V2DataLayerTest, DecodeHeaderRejectsTruncated) {
    EXPECT_FALSE(v2::data::decode_header("XYZ").has_value());
}

// ─── Data Store: Replay ────────────────────────────────────────

class V2DataStoreTest : public ::testing::Test {
protected:
    void SetUp() override {
        static std::mt19937 rng{std::random_device{}()};
        dir_ = fs::temp_directory_path() /
               ("v2_data_layer_test_" + std::to_string(rng()));
        store_ = std::make_unique<v2::gateway::JsonFileBattleDataStore>(dir_);
    }

    void TearDown() override {
        store_.reset();
        fs::remove_all(dir_);
    }

    fs::path dir_;
    std::unique_ptr<v2::gateway::JsonFileBattleDataStore> store_;
};

TEST_F(V2DataStoreTest, PersistsAndLoadsReplay) {
    const std::string replay_json = R"({"battle_id":"b001","frames":[]})";
    ASSERT_TRUE(store_->save_replay("b001", replay_json));

    const auto loaded = store_->load_replay("b001");
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, replay_json);
}

TEST_F(V2DataStoreTest, PersistsAndLoadsResult) {
    const std::string result_json = R"({"battle_id":"b001","winner":"alice"})";
    ASSERT_TRUE(store_->save_result("b001", result_json));

    const auto loaded = store_->load_result("b001");
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, result_json);
}

TEST_F(V2DataStoreTest, PersistsAndLoadsSnapshot) {
    const std::string snapshot_json = R"({"clock":{"frame_number":5}})";
    ASSERT_TRUE(store_->save_snapshot("b001", snapshot_json));

    const auto loaded = store_->load_snapshot("b001");
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, snapshot_json);
}

TEST_F(V2DataStoreTest, LoadNonExistentReturnsNullopt) {
    EXPECT_FALSE(store_->load_replay("no_such_battle").has_value());
    EXPECT_FALSE(store_->load_result("no_such_battle").has_value());
    EXPECT_FALSE(store_->load_snapshot("no_such_battle").has_value());
}

TEST_F(V2DataStoreTest, PersistWritesAllArtifacts) {
    v2::gateway::Runtime::BattleArchive archive{
        .battle_id = "b_100",
        .room_id = "room_1",
        .reason = "frame_limit_reached",
        .triggering_user_id = "",
        .total_frames = 10,
        .participant_user_ids = {"alice", "bob"},
        .replay_payload = R"({"battle_id":"b_100","frames":[]})",
        .result =
            {
                .battle_id = "b_100",
                .room_id = "room_1",
                .reason = v2::battle::BattleFinishReason::kFrameLimitReached,
                .winner_user_id = "alice",
                .scores =
                    {
                        {.user_id = "alice", .score = 100},
                        {.user_id = "bob", .score = 50},
                    },
                .total_frames = 10,
            },
    };

    ASSERT_TRUE(store_->persist(archive));

    // All artifacts should be loadable
    const auto result = store_->load_result("b_100");
    ASSERT_TRUE(result.has_value());
    auto result_doc = nlohmann::json::parse(*result);
    EXPECT_EQ(result_doc.value("battle_id", ""), "b_100");
    EXPECT_EQ(result_doc.value("winner_user_id", ""), "alice");

    const auto replay = store_->load_replay("b_100");
    ASSERT_TRUE(replay.has_value());
    EXPECT_NE(replay->find("b_100"), std::string::npos);
}

// ─── Data Store: Backward Compatibility ────────────────────────

TEST_F(V2DataStoreTest, LoadReplayReadsRawJsonWithoutVersionEnvelope) {
    const std::string raw_json = R"({"battle_id":"old_format","frames":[]})";
    const auto replay_path = dir_ / "replays" / "old_format.replay";
    std::ofstream out(replay_path, std::ios::binary);
    out.write(raw_json.data(), static_cast<std::streamsize>(raw_json.size()));
    out.close();

    const auto loaded = store_->load_replay("old_format");
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, raw_json);
}

TEST_F(V2DataStoreTest, LoadResultReadsRawJsonWithoutVersionEnvelope) {
    const std::string raw_json = R"({"battle_id":"old_result","winner":"bob"})";
    const auto result_path = dir_ / "results" / "old_result.result";
    std::ofstream out(result_path, std::ios::binary);
    out.write(raw_json.data(), static_cast<std::streamsize>(raw_json.size()));
    out.close();

    const auto loaded = store_->load_result("old_result");
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, raw_json);
}

TEST_F(V2DataStoreTest, LoadSnapshotReadsRawJsonWithoutVersionEnvelope) {
    const std::string raw_json = R"({"clock":{"frame_number":99}})";
    const auto snapshot_path = dir_ / "snapshots" / "raw.snapshot";
    std::ofstream out(snapshot_path, std::ios::binary);
    out.write(raw_json.data(), static_cast<std::streamsize>(raw_json.size()));
    out.close();

    const auto loaded = store_->load_snapshot("raw");
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, raw_json);
}

// ─── World Snapshot ────────────────────────────────────────────

TEST(V2DataLayerTest, WorldSnapshotToJsonRoundTrip) {
    auto world = v2::battle::create_battle_world("battle_snap", "room_snap", {"alice", "bob"}, 50);

    // Apply some state changes
    (void)v2::battle::battle_world_process_input(*world, "alice", "move_a", 10, 1);
    (void)v2::battle::battle_world_process_input(*world, "bob", "move_b", 5, 1);
    (void)v2::battle::battle_world_advance_frame(*world, 1, "tick");
    (void)v2::battle::battle_world_advance_frame(*world, 2, "tick");

    const auto snapshot_json = v2::battle::battle_world_snapshot_to_json(*world);
    ASSERT_FALSE(snapshot_json.empty());

    // Verify the snapshot contains expected keys
    auto doc = nlohmann::json::parse(snapshot_json);
    EXPECT_TRUE(doc.contains("clock"));
    EXPECT_TRUE(doc.contains("metadata"));
    EXPECT_TRUE(doc.contains("participants"));
    EXPECT_TRUE(doc.contains("replay_inputs"));

    // Restore into a fresh world
    auto restored = v2::battle::create_battle_world("battle_snap", "room_snap", {"alice", "bob"}, 50);
    ASSERT_TRUE(v2::battle::battle_world_restore_from_json(*restored, snapshot_json));

    // Verify restored state
    const auto restored_state = v2::battle::battle_world_runtime_state(*restored);
    EXPECT_EQ(restored_state.battle_id, "battle_snap");
    EXPECT_EQ(restored_state.room_id, "room_snap");
    EXPECT_EQ(restored_state.frame_number, 2U);
    EXPECT_EQ(restored_state.replay_inputs.size(), 2U);
}

TEST(V2DataLayerTest, WorldSnapshotJsonFieldsAreComplete) {
    auto world = v2::battle::create_battle_world("b_full", "r_full", {"alice"}, 100);

    (void)v2::battle::battle_world_process_input(*world, "alice", "input_1", 42, 1);
    (void)v2::battle::battle_world_advance_frame(*world, 1, "scheduler");

    const auto json_str = v2::battle::battle_world_snapshot_to_json(*world);
    auto doc = nlohmann::json::parse(json_str);

    // Clock
    EXPECT_EQ(doc["clock"].value("frame_number", 0U), 1U);
    EXPECT_EQ(doc["clock"].value("last_trigger", ""), "scheduler");

    // Metadata
    EXPECT_EQ(doc["metadata"].value("battle_id", ""), "b_full");
    EXPECT_EQ(doc["metadata"].value("room_id", ""), "r_full");
    EXPECT_EQ(doc["metadata"].value("max_frames", 0U), 100U);
    EXPECT_GT(doc["metadata"].value("next_input_seq", 0ULL), 1ULL);

    // Participants
    ASSERT_EQ(doc["participants"].size(), 1U);
    EXPECT_EQ(doc["participants"][0].value("user_id", ""), "alice");
    EXPECT_EQ(doc["participants"][0].value("score", 0), 42);
    EXPECT_EQ(doc["participants"][0].value("online", false), true);

    // Replay inputs
    ASSERT_EQ(doc["replay_inputs"].size(), 1U);
    EXPECT_EQ(doc["replay_inputs"][0].value("user_id", ""), "alice");
    EXPECT_EQ(doc["replay_inputs"][0].value("score", 0), 42);
    EXPECT_EQ(doc["replay_inputs"][0].value("trigger", ""), "scheduler");
}

TEST(V2DataLayerTest, WorldSnapshotRestoreInvalidJsonFails) {
    auto world = v2::battle::create_battle_world("b", "r", {"alice"}, 10);
    EXPECT_FALSE(v2::battle::battle_world_restore_from_json(*world, "not valid json{{{"));
}

TEST(V2DataLayerTest, WorldSnapshotRestoreMissingKeysFails) {
    auto world = v2::battle::create_battle_world("b", "r", {"alice"}, 10);
    EXPECT_FALSE(v2::battle::battle_world_restore_from_json(*world, R"({"clock":{}})"));
}

TEST(V2DataLayerTest, WorldSnapshotRestoreMismatchedParticipantCountFails) {
    auto world = v2::battle::create_battle_world("b", "r", {"alice"}, 10);

    // Snapshot with 2 participants but world only has 1
    nlohmann::json bad_snapshot{
        {"clock", {{"frame_number", 0}, {"last_trigger", ""}}},
        {"metadata", {{"battle_id", "b"}, {"room_id", "r"}, {"lifecycle", 1},
                       {"frame_number", 0}, {"max_frames", 10}, {"next_input_seq", 1}}},
        {"participants", {{{"user_id", "alice"}}, {{"user_id", "bob"}}}},
        {"replay_inputs", nlohmann::json::array()},
    };

    EXPECT_FALSE(v2::battle::battle_world_restore_from_json(*world, bad_snapshot.dump()));
}

TEST(V2DataLayerTest, WorldSnapshotRestorePreservesReplayLog) {
    auto world = v2::battle::create_battle_world("b_rep", "r_rep", {"alice"}, 100);

    (void)v2::battle::battle_world_process_input(*world, "alice", "first", 10, 1);
    (void)v2::battle::battle_world_process_input(*world, "alice", "second", 20, 2);

    const auto snapshot = v2::battle::battle_world_snapshot_to_json(*world);

    auto restored = v2::battle::create_battle_world("b_rep", "r_rep", {"alice"}, 100);
    ASSERT_TRUE(v2::battle::battle_world_restore_from_json(*restored, snapshot));

    const auto state = v2::battle::battle_world_runtime_state(*restored);
    ASSERT_EQ(state.replay_inputs.size(), 2U);
    EXPECT_EQ(state.replay_inputs[0].input_seq, 1U);
    EXPECT_EQ(state.replay_inputs[0].input_data, "first");
    EXPECT_EQ(state.replay_inputs[0].score, 10);
    EXPECT_EQ(state.replay_inputs[1].input_seq, 2U);
    EXPECT_EQ(state.replay_inputs[1].input_data, "second");
    EXPECT_EQ(state.replay_inputs[1].score, 20);
}
