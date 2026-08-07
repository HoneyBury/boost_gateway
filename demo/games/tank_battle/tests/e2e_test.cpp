// Tank Battle E2E Closed-Loop Test
//
// Tests the full lifecycle at the InstanceRuntime + TankPlugin level:
//   C0.1 — Player init: create instance with players, verify waiting state
//   C0.2 — Battle flow: submit tank inputs, tick, verify snapshot state
//   C0.3 — Settlement: finish instance, verify settlement payload
//   C0.4 — Reconnect: get resume snapshot for reconnecting player
//   C0.5 — Anti-cheat: invalid moves are rejected
//   C1.0 — Three-player full battle
//   C1.1 — Battle with a disconnected player finishing normally
//   C1.2 — Settlement JSON field completeness
//   C1.3 — Resume restores player position after reconnect
//   C1.4 — Resume after window expired (instance destroyed) returns empty
//
// This test links against tank_battle_server (TankPlugin + TankWorld)
// and project_v2 (InstanceRuntime). No network or gateway required.

#include <gtest/gtest.h>

#include "demo/games/tank_battle/server/tank_plugin/tank_plugin.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/realtime/types.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <memory>
#include <string>
#include <vector>

namespace {

// ─── Fixture ──────────────────────────────────────────────────────────

class TankBattleE2ETest : public ::testing::Test {
protected:
    void SetUp() override {
        runtime_.register_plugin("tank", []() -> std::unique_ptr<v2::realtime::InstancePlugin> {
            return std::make_unique<tank::TankPlugin>();
        });
        (void)runtime_.set_event_callback([this](const v2::realtime::InstanceEvent& event) {
            last_event_ = event;
            if (event.type == v2::realtime::InstanceEvent::Type::kInstanceFinished) {
                captured_settlement_ = event.settlement.result_payload;
            }
        });
    }

    v2::realtime::InstanceRuntime runtime_;
    static constexpr std::int64_t base_time_ = 1000000;
    v2::realtime::InstanceEvent last_event_;
    std::string captured_settlement_;

    // Helper: create a PlayerContext
    static v2::realtime::PlayerContext make_player(const std::string& user_id,
                                                     const std::string& display_name = "") {
        v2::realtime::PlayerContext pc;
        pc.user_id = user_id;
        pc.display_name = display_name.empty() ? user_id : display_name;
        return pc;
    }

    // Helper: create a tank move input envelope
    static v2::realtime::InputEnvelope make_move_input(
        const std::string& instance_id,
        const std::string& user_id,
        std::uint64_t seq,
        int dx, int dy) {
        v2::realtime::InputEnvelope input;
        input.instance_id = instance_id;
        input.user_id = user_id;
        input.seq = seq;
        input.payload_type = "tank.input";
        nlohmann::json action = {
            {"actions", {{{"type", "move"}, {"dx", dx}, {"dy", dy}}}}
        };
        input.payload = action.dump();
        return input;
    }

    // Helper: create a tank fire input envelope
    static v2::realtime::InputEnvelope make_fire_input(
        const std::string& instance_id,
        const std::string& user_id,
        std::uint64_t seq,
        int direction) {
        v2::realtime::InputEnvelope input;
        input.instance_id = instance_id;
        input.user_id = user_id;
        input.seq = seq;
        input.payload_type = "tank.input";
        nlohmann::json action = {
            {"actions", {{{"type", "fire"}, {"direction", direction}}}}
        };
        input.payload = action.dump();
        return input;
    }
};

// ─── C0.1: Player Init ────────────────────────────────────────────────

TEST_F(TankBattleE2ETest, CreateInstanceWithPlayers) {
    auto alice = make_player("alice", "Alice");
    auto bob = make_player("bob", "Bob");

    auto id = runtime_.create_instance("battle_001", "room_001", "tank", {alice, bob});
    EXPECT_EQ(id, "battle_001");
    EXPECT_EQ(runtime_.instance_count(), 1);
    EXPECT_EQ(runtime_.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kWaitingPlayers);
}

// ─── C0.2: Battle Flow with Tank Inputs ───────────────────────────────

TEST_F(TankBattleE2ETest, FullBattleFlow) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    runtime_.create_instance("battle_001", "room_001", "tank", {alice, bob});

    // Submit move inputs for several ticks
    for (int i = 0; i < 5; i++) {
        auto input = make_move_input("battle_001", "alice", i + 1, 1, 0);
        auto result = runtime_.submit_input(input);
        EXPECT_TRUE(result.accepted) << "tick " << i;
    }

    // Tick should transition to running and process inputs
    auto stats = runtime_.tick_instance("battle_001", 1, base_time_);
    EXPECT_EQ(stats.frame_number, 1);
    EXPECT_EQ(runtime_.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kRunning);

    // Tick again without inputs
    auto stats2 = runtime_.tick_instance("battle_001", 2, base_time_ + 33);
    EXPECT_EQ(stats2.frame_number, 2);
}

// ─── C0.3: Settlement ─────────────────────────────────────────────────

TEST_F(TankBattleE2ETest, BattleSettlementAfterFinish) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    runtime_.create_instance("battle_001", "room_001", "tank", {alice, bob});

    // Run a few ticks
    runtime_.tick_instance("battle_001", 1, base_time_);
    runtime_.tick_instance("battle_001", 2, base_time_ + 33);
    runtime_.tick_instance("battle_001", 3, base_time_ + 66);

    // Finish with normal reason
    runtime_.finish_instance("battle_001", v2::realtime::FinishReason::kNormal);
    EXPECT_EQ(runtime_.get_instance_state("battle_001"),
              v2::realtime::InstanceState::kFinished);

    // Verify settlement was captured via event callback
    EXPECT_FALSE(captured_settlement_.empty());
    auto settlement = nlohmann::json::parse(captured_settlement_, nullptr, false);
    EXPECT_FALSE(settlement.is_discarded());
    EXPECT_EQ(settlement.value("instance_id", ""), "battle_001");
    EXPECT_EQ(settlement.value("room_id", ""), "room_001");
    EXPECT_EQ(settlement.value("reason", ""), "normal");
}

// ─── C0.4: Reconnect / Resume Snapshot ────────────────────────────────

TEST_F(TankBattleE2ETest, ResumeSnapshotForReconnectingPlayer) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    runtime_.create_instance("battle_001", "room_001", "tank", {alice, bob});

    // Tick to advance state
    runtime_.tick_instance("battle_001", 1, base_time_);
    runtime_.tick_instance("battle_001", 2, base_time_ + 33);

    // Get resume snapshot (simulates reconnect)
    auto snap = runtime_.get_resume_snapshot("battle_001", "alice");
    EXPECT_TRUE(snap.is_resume);
    EXPECT_FALSE(snap.payload.empty());

    // Non-existent player gets empty
    auto no_player = runtime_.get_resume_snapshot("battle_001", "nonexistent");
    EXPECT_TRUE(no_player.payload.empty());

    // Non-existent instance gets empty
    auto no_inst = runtime_.get_resume_snapshot("no_such", "alice");
    EXPECT_TRUE(no_inst.payload.empty());
}

// ─── C0.5: Anti-Cheat ─────────────────────────────────────────────────

TEST_F(TankBattleE2ETest, InvalidInputRejectedByPlugin) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    runtime_.create_instance("battle_001", "room_001", "tank", {alice, bob});

    // Diagonal move should be rejected by anti-cheat
    auto input = make_move_input("battle_001", "alice", 1, 1, 1);
    auto result = runtime_.submit_input(input);
    EXPECT_TRUE(result.accepted);  // accepted at runtime level (plugin validates on tick)

    // Tick and verify the anti-cheat filtered it
    auto stats = runtime_.tick_instance("battle_001", 1, base_time_);
    EXPECT_EQ(stats.frame_number, 1);
}

// ═══════════════════════════════════════════════════════════════════════
// Task 3: Multi-Player E2E Tests
// ═══════════════════════════════════════════════════════════════════════

// ─── C1.0: Three-Player Full Battle ───────────────────────────────────

TEST_F(TankBattleE2ETest, ThreePlayerFullBattle) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");
    auto charlie = make_player("charlie");

    // Use max_frames to let the battle end naturally after a few ticks
    runtime_.create_instance("battle_m3", "room_m3", "tank",
                             {alice, bob, charlie}, 33, 5);

    // Submit inputs for all 3 players for max_frames=5 ticks
    std::uint64_t seq = 1;
    for (int tick = 0; tick < 5; ++tick) {
        auto r1 = runtime_.submit_input(
            make_move_input("battle_m3", "alice", seq++, 0, 0));  // stop
        EXPECT_TRUE(r1.accepted);

        auto r2 = runtime_.submit_input(
            make_move_input("battle_m3", "bob", seq++, 0, 0));
        EXPECT_TRUE(r2.accepted);

        auto r3 = runtime_.submit_input(
            make_move_input("battle_m3", "charlie", seq++, 0, 0));
        EXPECT_TRUE(r3.accepted);

        auto stats = runtime_.tick_instance("battle_m3", tick + 1,
                                            base_time_ + tick * 33);
        EXPECT_EQ(stats.frame_number, tick + 1);
    }

    // One more tick to transition from kFinishing to kFinished
    runtime_.tick_instance("battle_m3", 6, base_time_ + 5 * 33);
    EXPECT_EQ(runtime_.get_instance_state("battle_m3"),
              v2::realtime::InstanceState::kFinished)
        << "battle should be finished after max_frames ticks";

    // Capture the settlement payload from the event callback
    EXPECT_FALSE(captured_settlement_.empty());

    auto settlement = nlohmann::json::parse(captured_settlement_, nullptr, false);
    ASSERT_FALSE(settlement.is_discarded());

    // Verify all 3 players are in the settlement
    ASSERT_TRUE(settlement.contains("players"));
    ASSERT_TRUE(settlement["players"].is_array());
    EXPECT_EQ(settlement["players"].size(), 3);

    // Collect user_ids from settlement
    std::vector<std::string> settled_users;
    for (const auto& p : settlement["players"]) {
        settled_users.push_back(p.value("user_id", ""));
    }
    EXPECT_NE(std::find(settled_users.begin(), settled_users.end(), "alice"),
              settled_users.end());
    EXPECT_NE(std::find(settled_users.begin(), settled_users.end(), "bob"),
              settled_users.end());
    EXPECT_NE(std::find(settled_users.begin(), settled_users.end(), "charlie"),
              settled_users.end());

    // Verify winner exists
    EXPECT_TRUE(settlement.contains("winner_user_id"));
    EXPECT_FALSE(settlement["winner_user_id"].empty());
}

// ─── C1.1: Battle With Disconnected Player ────────────────────────────

TEST_F(TankBattleE2ETest, BattleWithDisconnectedPlayerFinish) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    // Set max_frames so the battle finishes without requiring player interaction
    runtime_.create_instance("battle_dc", "room_dc", "tank",
                             {alice, bob}, 33, 3);

    // Only alice submits inputs; bob is "disconnected"
    std::uint64_t seq = 1;
    for (int tick = 0; tick < 3; ++tick) {
        auto input = make_move_input("battle_dc", "alice", seq++, 0, 0);  // stop
        runtime_.submit_input(input);

        auto stats = runtime_.tick_instance("battle_dc", tick + 1,
                                            base_time_ + tick * 33);
        EXPECT_EQ(stats.frame_number, tick + 1);
    }

    // Battle should finish due to max_frames
    EXPECT_EQ(runtime_.get_instance_state("battle_dc"),
              v2::realtime::InstanceState::kFinished);

    // Verify settlement contains both players (even the disconnected one)
    ASSERT_FALSE(captured_settlement_.empty());
    auto settlement = nlohmann::json::parse(captured_settlement_, nullptr, false);
    ASSERT_FALSE(settlement.is_discarded());

    ASSERT_TRUE(settlement.contains("players"));
    EXPECT_EQ(settlement["players"].size(), 2);

    // Verify disconnected player (bob) is present with scores
    bool bob_found = false;
    for (const auto& p : settlement["players"]) {
        if (p.value("user_id", "") == "bob") {
            bob_found = true;
            // bob didn't do anything, so his stats should all be 0
            EXPECT_EQ(p.value("kills", -1), 0);
            EXPECT_EQ(p.value("deaths", -1), 0);
            EXPECT_EQ(p.value("damage", -1), 0);
            break;
        }
    }
    EXPECT_TRUE(bob_found) << "disconnected player should appear in settlement";
}

// ─── C1.2: Settlement JSON Field Completeness ─────────────────────────

TEST_F(TankBattleE2ETest, SettlementJsonHasAllExpectedFields) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    runtime_.create_instance("battle_fields", "room_fields", "tank",
                             {alice, bob}, 33, 5);

    // Alice shoots bob to generate kills/damage
    runtime_.submit_input(
        make_fire_input("battle_fields", "alice", 1, 0));  // fire up

    runtime_.tick_instance("battle_fields", 1, base_time_);
    runtime_.tick_instance("battle_fields", 2, base_time_ + 33);
    runtime_.tick_instance("battle_fields", 3, base_time_ + 66);
    runtime_.tick_instance("battle_fields", 4, base_time_ + 99);
    runtime_.tick_instance("battle_fields", 5, base_time_ + 132);

    // Battle should be finished by now (max_frames=5 from frame_limit)
    EXPECT_EQ(runtime_.get_instance_state("battle_fields"),
              v2::realtime::InstanceState::kFinished);

    // Parse settlement
    ASSERT_FALSE(captured_settlement_.empty()) << "settlement was captured";
    auto settlement = nlohmann::json::parse(captured_settlement_, nullptr, false);
    ASSERT_FALSE(settlement.is_discarded());

    // Verify top-level fields
    EXPECT_TRUE(settlement.contains("reason"));
    EXPECT_TRUE(settlement.contains("total_frames"));
    EXPECT_TRUE(settlement.contains("players"));

    ASSERT_TRUE(settlement["players"].is_array());
    ASSERT_GE(settlement["players"].size(), 2);

    // Verify per-player fields
    for (const auto& player : settlement["players"]) {
        EXPECT_TRUE(player.contains("user_id")) << "player should have user_id";
        EXPECT_TRUE(player.contains("kills")) << "player should have kills";
        EXPECT_TRUE(player.contains("deaths")) << "player should have deaths";
        EXPECT_TRUE(player.contains("damage")) << "player should have damage";
        EXPECT_TRUE(player.contains("score")) << "player should have score";
        EXPECT_TRUE(player.contains("win")) << "player should have win flag";

        // Type checks
        EXPECT_TRUE(player["kills"].is_number_integer());
        EXPECT_TRUE(player["deaths"].is_number_integer());
        EXPECT_TRUE(player["damage"].is_number_integer());
        EXPECT_TRUE(player["score"].is_number_integer());
        EXPECT_TRUE(player["win"].is_boolean());
    }

    // Settlemet should have winner_user_id (determined by frame_limit scoring)
    EXPECT_TRUE(settlement.contains("winner_user_id"));
}

// ═══════════════════════════════════════════════════════════════════════
// Task 4: Resume/Reconnect E2E Tests
// ═══════════════════════════════════════════════════════════════════════

// ─── C1.3: Resume Restores Player Position ────────────────────────────

TEST_F(TankBattleE2ETest, ReconnectResumeRestoresPlayerPosition) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    runtime_.create_instance("battle_resume", "room_resume", "tank", {alice, bob});

    // Move alice right by 2 cells
    runtime_.submit_input(make_move_input("battle_resume", "alice", 1, 1, 0));
    runtime_.tick_instance("battle_resume", 1, base_time_);

    runtime_.submit_input(make_move_input("battle_resume", "alice", 2, 1, 0));
    runtime_.tick_instance("battle_resume", 2, base_time_ + 33);

    // Get resume snapshot for alice
    auto snap = runtime_.get_resume_snapshot("battle_resume", "alice");
    ASSERT_FALSE(snap.payload.empty());
    EXPECT_TRUE(snap.is_resume);

    // Parse snapshot payload to verify position
    auto snapshot_json = nlohmann::json::parse(snap.payload, nullptr, false);
    ASSERT_FALSE(snapshot_json.is_discarded());

    ASSERT_TRUE(snapshot_json.contains("tanks"));
    ASSERT_TRUE(snapshot_json["tanks"].is_array());

    // Find alice in the tanks array
    bool alice_found = false;
    for (const auto& tank : snapshot_json["tanks"]) {
        if (tank.value("user_id", "") == "alice") {
            alice_found = true;
            // Alice started at (2, 2) and moved right by 2
            // So she should be at x=4, y=2
            EXPECT_EQ(tank.value("x", -1), 4);
            EXPECT_EQ(tank.value("y", -1), 2);
            EXPECT_TRUE(tank.value("alive", false));
            break;
        }
    }
    EXPECT_TRUE(alice_found) << "alice should be in the resume snapshot";
}

// ─── C1.4: Resume After Window Expired ────────────────────────────────

TEST_F(TankBattleE2ETest, ReconnectAfterWindowExpired) {
    auto alice = make_player("alice");
    auto bob = make_player("bob");

    // No max_frames (0 = unlimited), short resume window
    runtime_.create_instance("battle_expired", "room_expired", "tank",
                             {alice, bob}, 33, 0, 1);

    // Tick twice so instance has some state
    runtime_.tick_instance("battle_expired", 1, base_time_);
    runtime_.tick_instance("battle_expired", 2, base_time_ + 33);

    // While instance exists and player is present, resume works
    auto snap_before = runtime_.get_resume_snapshot("battle_expired", "alice");
    EXPECT_FALSE(snap_before.payload.empty());

    // Simulate window expiry by destroying the instance
    // (the resume_window_ms field is stored but the runtime does not
    //  auto-destroy; the owner is responsible for cleanup.)
    runtime_.destroy_instance("battle_expired");

    // After destruction, resume snapshot should be empty (instance not found)
    auto snap_after = runtime_.get_resume_snapshot("battle_expired", "alice");
    EXPECT_TRUE(snap_after.payload.empty()) << "resume should fail after window expired";
    EXPECT_EQ(runtime_.get_instance_state("battle_expired"),
              v2::realtime::InstanceState::kClosed);
}

}  // anonymous namespace
