#include "game/battle/battle_manager.h"
#include "game/room/room_battle_lifecycle.h"
#include "game/room/room_manager.h"

#include <boost/asio.hpp>

#include <gtest/gtest.h>

namespace {

std::shared_ptr<net::Session> make_session(boost::asio::io_context& io_context) {
    return std::make_shared<net::Session>(net::tcp::socket(io_context));
}

}  // namespace

TEST(RoomBattleLifecycleTest, ClearBattleWhenRoomBecomesEmptyAfterLeaves) {
    boost::asio::io_context io_context;
    game::battle::BattleManager battle_manager;
    game::room::RoomManager room_manager;

    room_manager.set_battle_active_query([&battle_manager](const std::string& room_id) {
        return battle_manager.battle_started(room_id);
    });

    auto owner = make_session(io_context);
    auto member = make_session(io_context);

    ASSERT_EQ(room_manager.create_room(owner, "room_z").first, game::room::RoomManager::CreateRoomResult::kOk);
    ASSERT_EQ(room_manager.join_room(member, "room_z").first, game::room::RoomManager::JoinRoomResult::kOk);
    ASSERT_EQ(room_manager.set_ready(owner, true).first, game::room::RoomManager::ReadyResult::kOk);
    ASSERT_EQ(room_manager.set_ready(member, true).first, game::room::RoomManager::ReadyResult::kOk);

    ASSERT_EQ(battle_manager.start_battle("room_z", {"p_owner", "p_member"}).result,
              game::battle::BattleManager::StartBattleResult::kOk);
    ASSERT_TRUE(battle_manager.battle_started("room_z"));

    auto first_leave = room_manager.leave_room(owner);
    ASSERT_EQ(first_leave.first, game::room::RoomManager::LeaveRoomResult::kOk);
    game::room::clear_battle_if_room_empty(battle_manager, room_manager, first_leave.second.room_id);
    EXPECT_TRUE(battle_manager.battle_started("room_z"));

    auto second_leave = room_manager.leave_room(member);
    ASSERT_EQ(second_leave.first, game::room::RoomManager::LeaveRoomResult::kOk);
    game::room::clear_battle_if_room_empty(battle_manager, room_manager, second_leave.second.room_id);
    EXPECT_FALSE(battle_manager.battle_started("room_z"));
}
