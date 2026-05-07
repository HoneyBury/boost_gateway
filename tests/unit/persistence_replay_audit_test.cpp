#include "app/audit_log.h"
#include "app/logging.h"
#include "game/battle/replay_player.h"
#include "game/persistence/player_store.h"
#include "game/persistence/sqlite_store.h"

#include <gtest/gtest.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

namespace {

std::string slurp(const std::filesystem::path& path) {
    std::ifstream input(path, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(input), {});
}

}  // namespace

TEST(PersistenceReplayAuditTest, JsonFilePlayerStoreRoundTrip) {
    const auto dir = std::filesystem::temp_directory_path() / "player_store_round_trip";
    std::filesystem::remove_all(dir);

    game::persistence::JsonFilePlayerStore store(dir);
    const game::persistence::PlayerRecord expected{
        .user_id = "player_store_user",
        .display_name = "Store User",
        .score = 42,
        .last_login_ts = 1715000000,
    };

    ASSERT_TRUE(store.save(expected));
    const auto loaded = store.load(expected.user_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(loaded->user_id, expected.user_id);
    EXPECT_EQ(loaded->display_name, expected.display_name);
    EXPECT_EQ(loaded->score, expected.score);
    EXPECT_EQ(loaded->last_login_ts, expected.last_login_ts);

    std::filesystem::remove_all(dir);
}

#ifdef HAS_SQLITE
TEST(PersistenceReplayAuditTest, SqlitePlayerStoreRoundTrip) {
    const auto path = std::filesystem::temp_directory_path() / "player_store_round_trip.sqlite";
    std::filesystem::remove(path);

    game::persistence::SqlitePlayerStore store(path);
    const game::persistence::PlayerRecord expected{
        .user_id = "player_sqlite_user",
        .display_name = "Sqlite User",
        .score = 9,
        .last_login_ts = 1715001111,
    };

    ASSERT_TRUE(store.save(expected));
    const auto loaded = store.load(expected.user_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(loaded->display_name, expected.display_name);
    EXPECT_EQ(loaded->score, expected.score);
    EXPECT_EQ(loaded->last_login_ts, expected.last_login_ts);

    std::filesystem::remove(path);
}
#endif

TEST(PersistenceReplayAuditTest, JsonFileBattleReplayStoreRoundTrip) {
    const auto dir = std::filesystem::temp_directory_path() / "battle_replay_round_trip";
    std::filesystem::remove_all(dir);

    game::persistence::JsonFileBattleReplayStore store(dir);
    const std::string battle_id = "battle_store_01";
    const std::string replay_data = "{\"battle_id\":\"battle_store_01\",\"frames\":[]}";

    ASSERT_TRUE(store.save_replay(battle_id, replay_data));
    const auto loaded = store.load_replay(battle_id);
    ASSERT_TRUE(loaded.has_value());
    EXPECT_EQ(*loaded, replay_data);

    std::filesystem::remove_all(dir);
}

TEST(PersistenceReplayAuditTest, ReplayPlayerLoadsFramesAndPlaysToCompletion) {
    const auto dir = std::filesystem::temp_directory_path() / "replay_player_round_trip";
    std::filesystem::remove_all(dir);

    game::persistence::JsonFileBattleReplayStore store(dir);
    const std::string battle_id = "battle_replay_player_01";
    const std::string replay_data =
        "{"
        "\"battle_id\":\"battle_replay_player_01\","
        "\"room_id\":\"room_replay\","
        "\"total_frames\":2,"
        "\"frames\":["
        "  {\"frame\":0,\"inputs\":[{\"seq\":1,\"user_id\":\"u1\",\"payload\":\"left\"}]},"
        "  {\"frame\":1,\"inputs\":[{\"seq\":2,\"user_id\":\"u2\",\"payload\":\"right\"}]}"
        "]"
        "}";

    ASSERT_TRUE(store.save_replay(battle_id, replay_data));

    game::battle::ReplayPlayer player(store);
    ASSERT_TRUE(player.load(battle_id));
    EXPECT_EQ(player.battle_id(), battle_id);
    EXPECT_EQ(player.total_frames(), 2U);

    std::vector<game::battle::BattleManager::FrameSnapshot> frames;
    int end_calls = 0;

    player.play([&](const auto& frame) { frames.push_back(frame); }, [&] { ++end_calls; });
    player.play([&](const auto& frame) { frames.push_back(frame); }, [&] { ++end_calls; });

    EXPECT_TRUE(player.finished());
    ASSERT_EQ(frames.size(), 2U);
    EXPECT_EQ(frames[0].frame_number, 0U);
    EXPECT_EQ(frames[0].inputs[0].payload, "left");
    EXPECT_EQ(frames[1].frame_number, 1U);
    EXPECT_EQ(frames[1].inputs[0].payload, "right");
    EXPECT_EQ(end_calls, 1);

    std::filesystem::remove_all(dir);
}

TEST(PersistenceReplayAuditTest, AuditLogAppendsApproxJsonLine) {
    app::logging::init("project_tests");

    const auto unique = std::to_string(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()).count());
    const std::string event_name = "audit_test_event_" + unique;
    const std::string details = "stage=T20 marker=" + unique;

    AUDIT_LOG(event_name, details);

    const auto log_text = slurp("logs/audit.log");
    EXPECT_NE(log_text.find("\"event\":\"" + event_name + "\""), std::string::npos);
    EXPECT_NE(log_text.find("\"details\":\"" + details + "\""), std::string::npos);
}
