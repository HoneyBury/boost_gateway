#include <gtest/gtest.h>


#include "v2/battle/runtime_components.h"
#include "v2/battle/runtime_world.h"
#include "v2/ecs/world.h"

#include <vector>

TEST(V2BattleDeterminismTest, IdenticalInputsProduceIdenticalSnapshots) {
    auto w1 = v2::battle::create_battle_world("det_01", "r1", {"alice", "bob"}, 5);
    auto w2 = v2::battle::create_battle_world("det_01", "r1", {"alice", "bob"}, 5);

    auto feed_input = [](v2::ecs::World& w, const std::string& user,
                          const std::string& data, std::int64_t score,
                          std::uint32_t frame) {
        v2::battle::battle_world_process_input(w, user, data, score, frame);
    };

    feed_input(*w1, "alice", "move:10,20", 5, 1);
    feed_input(*w1, "bob", "attack:alice", 3, 1);
    feed_input(*w2, "alice", "move:10,20", 5, 1);
    feed_input(*w2, "bob", "attack:alice", 3, 1);

    v2::battle::battle_world_advance_frame(*w1, 1, "input:alice");
    v2::battle::battle_world_advance_frame(*w2, 1, "input:alice");

    auto s1 = v2::battle::battle_world_snapshot(*w1);
    auto s2 = v2::battle::battle_world_snapshot(*w2);

    EXPECT_EQ(s1.clock.frame_number, s2.clock.frame_number);
    EXPECT_EQ(s1.clock.last_trigger, s2.clock.last_trigger);
    ASSERT_EQ(s1.participants.size(), s2.participants.size());

    for (size_t i = 0; i < s1.participants.size(); ++i) {
        const auto& a = s1.participants[i];
        const auto& b = s2.participants[i];
        EXPECT_EQ(a.user_id, b.user_id) << "participant " << i;
        EXPECT_EQ(a.score, b.score) << "participant " << i;
        EXPECT_EQ(a.pos_x, b.pos_x) << "participant " << i;
        EXPECT_EQ(a.pos_y, b.pos_y) << "participant " << i;
        EXPECT_EQ(a.hp, b.hp) << "participant " << i;
        EXPECT_EQ(a.max_hp, b.max_hp) << "participant " << i;
        EXPECT_EQ(a.online, b.online) << "participant " << i;
    }
}

TEST(V2BattleDeterminismTest, IdenticalInputSequencesProduceIdenticalReplayLogs) {
    auto w1 = v2::battle::create_battle_world("det_02", "r2", {"alice"}, 3);
    auto w2 = v2::battle::create_battle_world("det_02", "r2", {"alice"}, 3);

    for (int frame = 1; frame <= 3; ++frame) {
        v2::battle::battle_world_process_input(*w1, "alice", "move:1,2", 10, frame);
        v2::battle::battle_world_process_input(*w2, "alice", "move:1,2", 10, frame);
        v2::battle::battle_world_advance_frame(*w1, frame, "tick");
        v2::battle::battle_world_advance_frame(*w2, frame, "tick");
    }

    auto r1 = v2::battle::battle_world_collect_replay_inputs(*w1);
    auto r2 = v2::battle::battle_world_collect_replay_inputs(*w2);

    ASSERT_EQ(r1.size(), r2.size());
    for (size_t i = 0; i < r1.size(); ++i) {
        EXPECT_EQ(r1[i].input_seq, r2[i].input_seq) << "record " << i;
        EXPECT_EQ(r1[i].frame_number, r2[i].frame_number) << "record " << i;
        EXPECT_EQ(r1[i].user_id, r2[i].user_id) << "record " << i;
        EXPECT_EQ(r1[i].input_data, r2[i].input_data) << "record " << i;
        EXPECT_EQ(r1[i].score, r2[i].score) << "record " << i;
    }
}

TEST(V2BattleDeterminismTest, MovementSystemAppliesMoveAuthoritatively) {
    auto world = v2::battle::create_battle_world("det_03", "r3", {"alice"}, 3);

    auto input_result = v2::battle::battle_world_process_input(
        *world, "alice", "move:42,77", 0, 1);
    EXPECT_TRUE(input_result.accepted);

    v2::battle::battle_world_advance_frame(*world, 1, "input:alice");

    auto snapshot = v2::battle::battle_world_snapshot(*world);
    ASSERT_GE(snapshot.participants.size(), 1U);

    bool found = false;
    for (const auto& p : snapshot.participants) {
        if (p.user_id == "alice") {
            EXPECT_EQ(p.pos_x, 42);
            EXPECT_EQ(p.pos_y, 77);
            found = true;
        }
    }
    EXPECT_TRUE(found);
}

TEST(V2BattleDeterminismTest, CombatSystemAppliesDamageInRange) {
    auto world = v2::battle::create_battle_world("det_04", "r4", {"alice", "bob"}, 5);

    // Move alice next to bob (manhattan distance 1)
    auto* sw = dynamic_cast<v2::ecs::SimpleWorld*>(world.get());
    ASSERT_NE(sw, nullptr);
    sw->for_each<v2::battle::PositionComponent>(
        [](v2::ecs::EntityHandle, v2::battle::PositionComponent& pos) {
            pos.x = 0;
            pos.y = 0;
        });

    // Process alice's attack on bob
    v2::battle::battle_world_process_input(*world, "alice", "attack:bob", 0, 1);
    v2::battle::battle_world_advance_frame(*world, 1, "input:alice");

    auto snapshot = v2::battle::battle_world_snapshot(*world);
    ASSERT_GE(snapshot.participants.size(), 2U);

    for (const auto& p : snapshot.participants) {
        if (p.user_id == "bob") {
            EXPECT_EQ(p.hp, 90);  // 100 - 10 (default attack damage)
        }
    }
}
