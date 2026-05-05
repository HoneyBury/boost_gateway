#include "game/room/room_manager.h"

#include <boost/asio.hpp>

#include <gtest/gtest.h>

namespace {

std::shared_ptr<net::Session> make_session(boost::asio::io_context& io_context) {
    return std::make_shared<net::Session>(net::tcp::socket(io_context));
}

}  // namespace

TEST(RoomManagerTest, TracksRoomMembersAndBattleState) {
    boost::asio::io_context io_context;
    game::room::RoomManager room_manager;

    auto first = make_session(io_context);
    auto second = make_session(io_context);

    const auto first_join = room_manager.join_room(first, "room_alpha");
    const auto second_join = room_manager.join_room(second, "room_alpha");

    EXPECT_EQ(first_join.result, game::room::RoomManager::JoinRoomResult::kOk);
    EXPECT_EQ(second_join.result, game::room::RoomManager::JoinRoomResult::kOk);
    EXPECT_EQ(room_manager.room_count(), 1U);
    EXPECT_EQ(room_manager.member_count("room_alpha"), 2U);

    EXPECT_TRUE(room_manager.mark_battle_started("room_alpha"));
    EXPECT_TRUE(room_manager.battle_started("room_alpha"));

    room_manager.remove_session(first);
    room_manager.remove_session(second);
    EXPECT_EQ(room_manager.room_count(), 0U);
}
