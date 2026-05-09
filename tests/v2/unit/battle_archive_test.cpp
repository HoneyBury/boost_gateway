#include <gtest/gtest.h>

#include <filesystem>

#include "game/battle/replay_player.h"
#include "game/persistence/player_store.h"
#include "net/protocol.h"
#include "v2/gateway/battle_archive_store.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

TEST(V2BattleArchiveTest, RuntimeBuildsResultSummaryAndReplayPayloadOnBattleSettlement) {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    const auto gateway_actor = runtime.create_gateway_actor();
    adapter.bind_gateway(gateway_actor);

    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 1,
        .body = "owner|token:owner|Owner",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kLoginRequest,
        .request_id = 2,
        .body = "member|token:member|Member",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomCreateRequest,
        .request_id = 3,
        .body = "room_archive",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomJoinRequest,
        .request_id = 4,
        .body = "room_archive",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 5,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 200,
        .protocol_message_id = net::protocol::kRoomReadyRequest,
        .request_id = 6,
        .body = "true",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleStartRequest,
        .request_id = 7,
        .body = "room_archive",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 8,
        .body = "score=8:move:1,1",
    });
    (void)adapter.handle_incoming(v2::gateway::ClientEnvelope{
        .session_id = 100,
        .protocol_message_id = net::protocol::kBattleInputRequest,
        .request_id = 9,
        .body = "finish:surrender",
    });

    const auto archive = runtime.archived_battle("battle_0001");
    ASSERT_TRUE(archive.has_value());
    EXPECT_EQ(archive->battle_id, "battle_0001");
    EXPECT_EQ(archive->room_id, "room_archive");
    EXPECT_EQ(archive->reason, "surrender");
    EXPECT_EQ(archive->triggering_user_id, "owner");
    EXPECT_EQ(archive->total_frames, 1U);
    ASSERT_EQ(archive->participant_user_ids.size(), 2U);
    EXPECT_EQ(archive->participant_user_ids[0], "owner");
    EXPECT_EQ(archive->participant_user_ids[1], "member");
    EXPECT_EQ(archive->result.battle_id, "battle_0001");
    EXPECT_EQ(archive->result.room_id, "room_archive");
    EXPECT_EQ(archive->result.reason, v2::battle::BattleFinishReason::kSurrender);
    ASSERT_TRUE(archive->result.winner_user_id.has_value());
    EXPECT_EQ(*archive->result.winner_user_id, "owner");
    ASSERT_EQ(archive->result.scores.size(), 2U);
    EXPECT_EQ(archive->result.scores[0].user_id, "owner");
    EXPECT_EQ(archive->result.scores[0].score, 8);  // "move:1,1" = 8 bytes
    EXPECT_EQ(archive->result.scores[1].user_id, "member");
    EXPECT_EQ(archive->result.scores[1].score, 0);
    EXPECT_EQ(archive->result.total_frames, 1U);

    const auto replay_dir = std::filesystem::temp_directory_path() / "v2_runtime_replay_archive";
    game::persistence::JsonFileBattleReplayStore store(replay_dir);
    ASSERT_TRUE(store.save_replay(archive->battle_id, archive->replay_payload));

    game::battle::ReplayPlayer player(store);
    ASSERT_TRUE(player.load(archive->battle_id));
    EXPECT_EQ(player.battle_id(), "battle_0001");
    EXPECT_EQ(player.total_frames(), 1U);

    std::vector<game::battle::BattleManager::FrameSnapshot> frames;
    bool ended = false;
    while (!player.finished()) {
        player.play(
            [&frames](const game::battle::BattleManager::FrameSnapshot& frame) { frames.push_back(frame); },
            [&ended]() { ended = true; });
    }

    ASSERT_EQ(frames.size(), 1U);
    EXPECT_EQ(frames[0].frame_number, 1U);
    ASSERT_EQ(frames[0].inputs.size(), 1U);
    EXPECT_EQ(frames[0].inputs[0].user_id, "owner");
    EXPECT_EQ(frames[0].inputs[0].payload, "move:1,1");
    EXPECT_TRUE(ended);

    std::filesystem::remove_all(replay_dir);
}

TEST(V2BattleArchiveTest, ArchiveSinkPersistsReportAndReplayArtifacts) {
    const auto archive_dir = std::filesystem::temp_directory_path() / "v2_runtime_archive_store";
    v2::gateway::JsonFileBattleArchiveStore store(archive_dir);

    v2::gateway::Runtime::BattleArchive archive{
        .battle_id = "battle_9001",
        .room_id = "room_store",
        .reason = "timeout",
        .triggering_user_id = "owner",
        .total_frames = 3,
        .participant_user_ids = {"owner", "member"},
        .replay_payload = R"({"battle_id":"battle_9001","room_id":"room_store","total_frames":1,"frames":[{"frame":1,"inputs":[{"seq":1,"user_id":"owner","payload":"move:1,1"}]}]})",
        .result =
            {
                .battle_id = "battle_9001",
                .room_id = "room_store",
                .reason = v2::battle::BattleFinishReason::kTimeout,
                .winner_user_id = "owner",
                .scores =
                    {
                        {.user_id = "owner", .score = 42},
                        {.user_id = "member", .score = 7},
                    },
                .total_frames = 3,
            },
    };

    ASSERT_TRUE(store.persist(archive));
    const auto report = store.load_report("battle_9001");
    ASSERT_TRUE(report.has_value());
    EXPECT_NE(report->find("\"battle_id\": \"battle_9001\""), std::string::npos);
    EXPECT_NE(report->find("\"reason\": \"timeout\""), std::string::npos);
    EXPECT_NE(report->find("\"winner_user_id\": \"owner\""), std::string::npos);
    EXPECT_NE(report->find("\"score\": 42"), std::string::npos);

    game::persistence::JsonFileBattleReplayStore replay_store(archive_dir / "replays");
    game::battle::ReplayPlayer player(replay_store);
    ASSERT_TRUE(player.load("battle_9001"));
    EXPECT_EQ(player.battle_id(), "battle_9001");

    std::filesystem::remove_all(archive_dir);
}
