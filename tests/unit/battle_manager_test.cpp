#include "game/battle/battle_manager.h"

#include <gtest/gtest.h>

TEST(BattleManagerTest, StartsBattleOncePerRoom) {
    game::battle::BattleManager battle_manager;

    const auto first = battle_manager.start_battle("room_alpha", 2);
    EXPECT_EQ(first.result, game::battle::BattleManager::StartBattleResult::kOk);
    EXPECT_TRUE(battle_manager.battle_started("room_alpha"));

    const auto duplicate = battle_manager.start_battle("room_alpha", 2);
    EXPECT_EQ(duplicate.result, game::battle::BattleManager::StartBattleResult::kAlreadyStarted);

    battle_manager.remove_room("room_alpha");
    EXPECT_FALSE(battle_manager.battle_started("room_alpha"));
}
