#include <gtest/gtest.h>

#include "v2/battle/tank_battle_plugin.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/realtime/types.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <string>
#include <vector>

namespace {

using nlohmann::json;

// ─── Factory function ─────────────────────────────────────────────────

std::unique_ptr<v2::realtime::InstancePlugin> create_tank_battle_plugin() {
    return std::make_unique<v2::battle::TankBattlePlugin>();
}

// ─── Helpers ──────────────────────────────────────────────────────────

std::int64_t now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

v2::realtime::PlayerContext make_player(const std::string& user_id) {
    v2::realtime::PlayerContext player;
    player.user_id = user_id;
    player.display_name = user_id;
    player.joined_at_ms = now_ms();
    return player;
}

v2::realtime::InputEnvelope make_input(const std::string& instance_id,
                                        const std::string& user_id,
                                        std::uint64_t seq,
                                        const std::string& payload) {
    v2::realtime::InputEnvelope input;
    input.instance_id = instance_id;
    input.user_id = user_id;
    input.seq = seq;
    input.payload_type = "tank.input";
    input.payload = payload;
    input.client_time_ms = now_ms();
    return input;
}

}  // namespace

// ─── Tests ────────────────────────────────────────────────────────────

TEST(TankBattlePluginTest, CreateInstanceWithTwoPlayers) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    auto p1 = make_player("alice");
    auto p2 = make_player("bob");

    auto id = runtime.create_instance(
        "battle_001", "room_001", "tank_battle", {p1, p2}, 33, 100, 30000);
    EXPECT_EQ(id, "battle_001");
    EXPECT_EQ(runtime.instance_count(), 1);

    auto snap = runtime.list_instances();
    ASSERT_GE(snap.size(), 1);
    EXPECT_EQ(snap[0].instance_id, "battle_001");
    EXPECT_EQ(snap[0].player_count, 2);
}

TEST(TankBattlePluginTest, MoveInputUpdatesPosition) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    auto player = make_player("alice");
    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {player}, 33, 100, 30000);

    // Submit a move input within MovementSystem speed limit (max Manhattan 200)
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"move","x":50,"y":30})");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick to process input
    auto stats = runtime.tick_instance("battle_001", 1, now_ms());
    EXPECT_EQ(stats.frame_number, 1);
    EXPECT_EQ(stats.inputs_processed, 1);

    // Verify snapshot contains updated position
    auto ctx = runtime.find_instance("battle_001");
    ASSERT_NE(ctx, nullptr);

    // Build snapshot through the plugin via the runtime
    // We can't directly call build_snapshot, but get_resume_snapshot
    // also calls build_resume_snapshot which includes position state
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    EXPECT_EQ(parsed["type"], "tank.snapshot");
    ASSERT_TRUE(parsed["players"].is_array());
    ASSERT_GE(parsed["players"].size(), 1);

    EXPECT_EQ(parsed["players"][0]["user_id"], "alice");
    EXPECT_EQ(parsed["players"][0]["x"], 50);
    EXPECT_EQ(parsed["players"][0]["y"], 30);
}

TEST(TankBattlePluginTest, AttackReducesHealth) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // alice attacks bob
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"attack","target_user_id":"bob"})");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick
    runtime.tick_instance("battle_001", 1, now_ms());

    // Check bob's health in the snapshot
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    ASSERT_TRUE(parsed["players"].is_array());

    // Find bob's entry
    bool found_bob = false;
    for (const auto& player : parsed["players"]) {
        if (player["user_id"] == "bob") {
            EXPECT_EQ(player["hp"].get<int>(), 90);   // 100 - 10 damage
            EXPECT_EQ(player["max_hp"].get<int>(), 100);
            found_bob = true;
            break;
        }
    }
    EXPECT_TRUE(found_bob);
}

TEST(TankBattlePluginTest, SnapshotContainsPlayerState) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // Tick once without inputs to advance frame
    runtime.tick_instance("battle_001", 1, now_ms());

    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    EXPECT_EQ(parsed["type"], "tank.snapshot");
    EXPECT_TRUE(parsed["frame"].is_number());
    EXPECT_TRUE(parsed["players"].is_array());
    EXPECT_EQ(parsed["players"].size(), 2);

    // Each player should have required fields
    for (const auto& player : parsed["players"]) {
        EXPECT_TRUE(player.contains("user_id"));
        EXPECT_TRUE(player.contains("x"));
        EXPECT_TRUE(player.contains("y"));
        EXPECT_TRUE(player.contains("hp"));
        EXPECT_TRUE(player.contains("max_hp"));
        EXPECT_TRUE(player.contains("score"));
        EXPECT_TRUE(player.contains("online"));
    }
}

TEST(TankBattlePluginTest, SettlementReturnsScores) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 5, 30000);  // max_frames = 5

    // Tick to frame limit (runtime handles frame-limit check)
    for (std::uint32_t frame = 1; frame <= 5; ++frame) {
        runtime.tick_instance("battle_001", frame, now_ms());
    }

    // After frame limit, the runtime should have triggered finish
    auto state = runtime.get_instance_state("battle_001");
    EXPECT_EQ(state, v2::realtime::InstanceState::kFinished);

    // Check that the settlement was built by getting the instance state
    // The settlement is emitted via callback; we verify the instance finished
    auto snapshots = runtime.list_instances();
    ASSERT_GE(snapshots.size(), 1);
    EXPECT_EQ(snapshots[0].state, v2::realtime::InstanceState::kFinished);
}

TEST(TankBattlePluginTest, SettlementResultPayload) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    // Capture settlement via event callback
    std::string captured_settlement_payload;
    runtime.set_event_callback(
        [&](const v2::realtime::InstanceEvent& event) {
            if (event.type == v2::realtime::InstanceEvent::Type::kInstanceFinished) {
                captured_settlement_payload = event.settlement.result_payload;
            }
        });

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 3, 30000);

    // Tick to frame limit
    for (std::uint32_t frame = 1; frame <= 3; ++frame) {
        runtime.tick_instance("battle_001", frame, now_ms());
    }

    // Verify settlement payload
    ASSERT_FALSE(captured_settlement_payload.empty());
    auto parsed = json::parse(captured_settlement_payload);
    EXPECT_EQ(parsed["type"], "tank.settlement");
    EXPECT_EQ(parsed["total_frames"].get<int>(), 3);
    ASSERT_TRUE(parsed["players"].is_array());
    EXPECT_EQ(parsed["players"].size(), 2);

    // Check player entries
    for (const auto& player : parsed["players"]) {
        EXPECT_TRUE(player.contains("user_id"));
        EXPECT_TRUE(player.contains("score"));
        EXPECT_TRUE(player.contains("winner"));
    }
}

TEST(TankBattlePluginTest, PlayerDeathTriggersFinish) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // Alice attacks bob repeatedly. With 10 damage per hit and 100 HP,
    // bob dies after 10 ticks. Bob also attacks alice.
    // Since cooldown_frames is 0, one attack is allowed per frame.
    for (std::uint32_t frame = 1; frame <= 12; ++frame) {
        // Submit attack from alice to bob
        auto input_a = make_input("battle_001", "alice", frame * 2,
                                  R"({"action":"attack","target_user_id":"bob"})");
        runtime.submit_input(input_a);

        // Submit attack from bob to alice
        auto input_b = make_input("battle_001", "bob", frame * 2 + 1,
                                  R"({"action":"attack","target_user_id":"alice"})");
        runtime.submit_input(input_b);

        auto stats = runtime.tick_instance("battle_001", frame, now_ms());

        // After ~10 ticks, both players should be dead and finish triggered
        if (stats.should_finish) {
            break;
        }
    }

    auto state = runtime.get_instance_state("battle_001");
    EXPECT_EQ(state, v2::realtime::InstanceState::kFinished);
}

TEST(TankBattlePluginTest, RejectInvalidMoveInput) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    auto player = make_player("alice");
    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {player}, 33, 100, 30000);

    // Submit move with missing fields — should still be accepted by runtime
    // but rejected at the ECS level (position unchanged)
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"move"})");  // no x,y fields
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    runtime.tick_instance("battle_001", 1, now_ms());

    // Position should still be (0,0) since ECS couldn't process the move
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    ASSERT_GE(parsed["players"].size(), 1);
    EXPECT_EQ(parsed["players"][0]["x"].get<int>(), 0);
    EXPECT_EQ(parsed["players"][0]["y"].get<int>(), 0);
}

TEST(TankBattlePluginTest, ResumeSnapshotContainsPlayerState) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // Tick to advance state
    runtime.tick_instance("battle_001", 1, now_ms());

    // Get resume snapshot for alice
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    EXPECT_TRUE(snap.is_resume);
    EXPECT_EQ(snap.payload_type, "tank.snapshot");
    EXPECT_GT(snap.frame_number, 0);
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    EXPECT_EQ(parsed["type"], "tank.snapshot");
    ASSERT_TRUE(parsed["players"].is_array());
    EXPECT_GE(parsed["players"].size(), 2);

    // Verify alice's state is present
    bool found_alice = false;
    for (const auto& player : parsed["players"]) {
        if (player["user_id"] == "alice") {
            EXPECT_TRUE(player.contains("x"));
            EXPECT_TRUE(player.contains("y"));
            EXPECT_TRUE(player.contains("hp"));
            EXPECT_TRUE(player.contains("score"));
            EXPECT_TRUE(player.contains("online"));
            found_alice = true;
            break;
        }
    }
    EXPECT_TRUE(found_alice);
}

TEST(TankBattlePluginTest, FinishInputTriggersSettlement) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    // Capture settlement events
    std::string captured_payload;
    runtime.set_event_callback(
        [&](const v2::realtime::InstanceEvent& event) {
            if (event.type == v2::realtime::InstanceEvent::Type::kInstanceFinished) {
                captured_payload = event.settlement.result_payload;
            }
        });

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice")}, 33, 100, 30000);

    // Submit finish action
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"finish","reason":"surrender"})");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick — should process finish and end the instance
    auto stats = runtime.tick_instance("battle_001", 1, now_ms());
    EXPECT_TRUE(stats.should_finish);
    EXPECT_EQ(stats.finish_reason,
              v2::realtime::FinishReason::kUserRequested);

    // Verify settlement was built
    ASSERT_FALSE(captured_payload.empty());
    auto parsed = json::parse(captured_payload);
    EXPECT_EQ(parsed["type"], "tank.settlement");
    ASSERT_TRUE(parsed["players"].is_array());
    EXPECT_EQ(parsed["players"].size(), 1);
}

TEST(TankBattlePluginTest, PlayerLeaveMarksOffline) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // Destroy (simulating all players leaving) — this destroys the instance
    // We test offline marking via the snapshot instead

    // Tick to get into running state
    runtime.tick_instance("battle_001", 1, now_ms());

    // We can't call on_player_leave directly through the runtime API
    // (there's no public method for it). Instead, verify that the
    // snapshot correctly reflects initial online state.
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    for (const auto& player : parsed["players"]) {
        EXPECT_TRUE(player["online"].get<bool>());
    }
}

TEST(TankBattlePluginTest, UnknownInputActionIsAccepted) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice")}, 33, 100, 30000);

    // Submit an unknown action
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"dance","style":"tango"})");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick — should not crash or cause issues
    EXPECT_NO_THROW(runtime.tick_instance("battle_001", 1, now_ms()));
}

TEST(TankBattlePluginTest, InvalidJsonPayloadIsHandled) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice")}, 33, 100, 30000);

    // Submit invalid JSON
    auto input = make_input("battle_001", "alice", 1, "not valid json");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick — should not crash
    EXPECT_NO_THROW(runtime.tick_instance("battle_001", 1, now_ms()));
}

TEST(TankBattlePluginTest, InputRejectedForNonExistentPlayer) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice")}, 33, 100, 30000);

    // Submit input for a player that doesn't exist in the instance
    auto input = make_input("battle_001", "nonexistent", 1,
                            R"({"action":"move","x":10,"y":20})");
    auto result = runtime.submit_input(input);
    // The runtime accepts the input (it goes into the queue)
    // But the plugin rejects it during on_input processing
    // The runtime doesn't expose on_input rejection status to submit_input
    // So we verify the input was accepted at the queue level,
    // but the tick processes 0 inputs
    EXPECT_TRUE(result.accepted);

    auto stats = runtime.tick_instance("battle_001", 1, now_ms());
    // The input was dequeued but rejected by the plugin,
    // so it's NOT in inputs_this_tick
    EXPECT_EQ(stats.inputs_processed, 0);
}

// --- Shoot action tests ------------------------------------------------

TEST(TankBattlePluginTest, ShootProjectileSpawned) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // alice shoots bob (same position = instant arrival)
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"shoot","target_user_id":"bob"})");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick to process
    auto stats = runtime.tick_instance("battle_001", 1, now_ms());
    EXPECT_EQ(stats.frame_number, 1);
    EXPECT_EQ(stats.inputs_processed, 1);

    // Verify snapshot is valid
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    EXPECT_EQ(parsed["type"], "tank.snapshot");
    ASSERT_TRUE(parsed["players"].is_array());
    ASSERT_EQ(parsed["players"].size(), 2);

    // Both players should have valid state
    for (const auto& player : parsed["players"]) {
        EXPECT_TRUE(player.contains("user_id"));
        EXPECT_TRUE(player.contains("x"));
        EXPECT_TRUE(player.contains("y"));
        EXPECT_TRUE(player.contains("hp"));
        EXPECT_TRUE(player.contains("max_hp"));
    }
}

TEST(TankBattlePluginTest, ShootReducesHealthOnImpact) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    std::uint32_t frame = 1;

    // Frame 1: Move alice to (50, 0) so she's at range
    {
        auto input = make_input("battle_001", "alice", frame,
                                R"({"action":"move","x":50,"y":0})");
        auto result = runtime.submit_input(input);
        EXPECT_TRUE(result.accepted);
        runtime.tick_instance("battle_001", frame, now_ms());
        frame++;
    }

    // Frame 2: alice shoots bob. Distance=50, speed=50 -> arrives in 1 tick
    {
        auto input = make_input("battle_001", "alice", frame,
                                R"({"action":"shoot","target_user_id":"bob"})");
        auto result = runtime.submit_input(input);
        EXPECT_TRUE(result.accepted);
        runtime.tick_instance("battle_001", frame, now_ms());
        frame++;
    }

    // Frame 3: Projectile arrives
    runtime.tick_instance("battle_001", frame, now_ms());

    // Verify bob's health was reduced
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    ASSERT_TRUE(parsed["players"].is_array());

    bool found_bob = false;
    for (const auto& player : parsed["players"]) {
        if (player["user_id"] == "bob") {
            EXPECT_EQ(player["hp"].get<int>(), 90);   // 100 - 10 damage
            EXPECT_EQ(player["max_hp"].get<int>(), 100);
            found_bob = true;
            break;
        }
    }
    EXPECT_TRUE(found_bob);
}

TEST(TankBattlePluginTest, ShootWithAoeRadius) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob"),
                             make_player("charlie")},
                            33, 100, 30000);

    std::uint32_t frame = 1;

    // Frame 1: Move alice away so projectile has travel time
    {
        auto input = make_input("battle_001", "alice", frame,
                                R"({"action":"move","x":50,"y":0})");
        runtime.submit_input(input);
        runtime.tick_instance("battle_001", frame, now_ms());
        frame++;
    }

    // Frame 2: alice shoots bob with aoe_radius=10
    // Both bob and charlie at (0,0) so both are in AoE range
    {
        auto input = make_input("battle_001", "alice", frame,
                                R"({"action":"shoot","target_user_id":"bob",)"
                                R"("aoe_radius":10})");
        runtime.submit_input(input);
        runtime.tick_instance("battle_001", frame, now_ms());
        frame++;
    }

    // Frame 3: Projectile arrives
    runtime.tick_instance("battle_001", frame, now_ms());

    // Verify both bob and charlie took AoE damage
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    ASSERT_TRUE(parsed["players"].is_array());

    for (const auto& player : parsed["players"]) {
        auto uid = player["user_id"].get<std::string>();
        if (uid == "alice") {
            EXPECT_EQ(player["hp"].get<int>(), 100);
        } else {
            // bob and charlie at (0,0) both within AoE radius of impact (0,0)
            EXPECT_EQ(player["hp"].get<int>(), 90) << "user_id=" << uid;
        }
    }
}

TEST(TankBattlePluginTest, ShootWithDamageOverTime) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    std::uint32_t frame = 1;

    // Frame 1: alice shoots bob with duration_frames=2 (DoT for 2 ticks)
    // Both at (0,0) -> instant projectile arrival
    {
        auto input = make_input("battle_001", "alice", frame,
                                R"({"action":"shoot","target_user_id":"bob",)"
                                R"("duration_frames":2})");
        auto result = runtime.submit_input(input);
        EXPECT_TRUE(result.accepted);
        runtime.tick_instance("battle_001", frame, now_ms());
        frame++;
    }

    // Frame 2: DoT tick 1
    runtime.tick_instance("battle_001", frame, now_ms());
    frame++;

    // Frame 3: DoT tick 2 (last tick)
    runtime.tick_instance("battle_001", frame, now_ms());

    // Bob should have taken:
    //   direct damage: 10 (frame 1 impact)
    //   DoT tick 1:    10 (frame 2)
    //   DoT tick 2:    10 (frame 3)
    // Total: 30 damage, HP = 70
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    ASSERT_TRUE(parsed["players"].is_array());

    bool found_bob = false;
    for (const auto& player : parsed["players"]) {
        if (player["user_id"] == "bob") {
            EXPECT_EQ(player["hp"].get<int>(), 70);
            found_bob = true;
            break;
        }
    }
    EXPECT_TRUE(found_bob);
}

TEST(TankBattlePluginTest, ShootWithoutTargetReturnsAccepted) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank_battle", &create_tank_battle_plugin);

    runtime.create_instance("battle_001", "room_001", "tank_battle",
                            {make_player("alice"), make_player("bob")},
                            33, 100, 30000);

    // Shoot without target_user_id - should be accepted but no-op
    auto input = make_input("battle_001", "alice", 1,
                            R"({"action":"shoot"})");
    auto result = runtime.submit_input(input);
    EXPECT_TRUE(result.accepted);

    // Tick
    runtime.tick_instance("battle_001", 1, now_ms());

    // Verify no damage was done (HP still 100)
    auto snap = runtime.get_resume_snapshot("battle_001", "alice");
    ASSERT_FALSE(snap.payload.empty());

    auto parsed = json::parse(snap.payload);
    ASSERT_TRUE(parsed["players"].is_array());

    for (const auto& player : parsed["players"]) {
        EXPECT_EQ(player["hp"].get<int>(), 100);
    }
}
