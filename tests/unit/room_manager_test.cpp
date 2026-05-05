#include "game/room/room_manager.h"

#include <boost/asio.hpp>

#include <gtest/gtest.h>

namespace {

std::shared_ptr<net::Session> make_session(boost::asio::io_context& io_context) {
    return std::make_shared<net::Session>(net::tcp::socket(io_context));
}

}  // namespace

TEST(RoomManagerTest, SupportsCreateJoinOwnerAndReadyState) {
    boost::asio::io_context io_context;
    game::room::RoomManager room_manager;

    auto owner = make_session(io_context);
    auto member = make_session(io_context);

    const auto [create_result, create_outcome] = room_manager.create_room(owner, "room_alpha");
    EXPECT_EQ(create_result, game::room::RoomManager::CreateRoomResult::kOk);
    EXPECT_EQ(create_outcome.player_count, 1U);

    const auto [join_result, join_outcome] = room_manager.join_room(member, "room_alpha");
    EXPECT_EQ(join_result, game::room::RoomManager::JoinRoomResult::kOk);
    EXPECT_EQ(join_outcome.player_count, 2U);

    const auto [ready_result, ready_outcome] = room_manager.set_ready(member, true);
    EXPECT_EQ(ready_result, game::room::RoomManager::ReadyResult::kOk);
    EXPECT_EQ(ready_outcome.room_id, "room_alpha");

    const auto snapshot = room_manager.room_snapshot("room_alpha");
    ASSERT_TRUE(snapshot);
    ASSERT_TRUE(snapshot->owner);
    EXPECT_EQ(snapshot->owner.get(), owner.get());
    EXPECT_EQ(snapshot->members.size(), 2U);

    room_manager.remove_session(owner);
    const auto snapshot_after_owner_leave = room_manager.room_snapshot("room_alpha");
    ASSERT_TRUE(snapshot_after_owner_leave);
    ASSERT_TRUE(snapshot_after_owner_leave->owner);
    EXPECT_EQ(snapshot_after_owner_leave->owner.get(), member.get());
}
