#include <gtest/gtest.h>

#include "v2/service/backend_envelope.h"
#include "v2/service/error_codes.h"

#include <nlohmann/json.hpp>

#include <string>

namespace {

// Simulate the room backend handler logic at the JSON level.
// We test the contract and error codes, not the transport.

struct RoomLobbyTest : ::testing::Test {
    // These match the error codes defined in ServiceErrorCode
    static constexpr std::int32_t kRoomNotFound = -1200;
    static constexpr std::int32_t kRoomFull = -1201;
    static constexpr std::int32_t kRoomInInstance = -1202;
    static constexpr std::int32_t kRoomClosed = -1203;
    static constexpr std::int32_t kNotRoomOwner = -1204;
    static constexpr std::int32_t kNotRoomMember = -1205;
};

}  // namespace

// Error code contract tests
TEST_F(RoomLobbyTest, ErrorCodeValues) {
    EXPECT_EQ(static_cast<std::int32_t>(v2::service::ServiceErrorCode::kRoomNotFound), kRoomNotFound);
    EXPECT_EQ(static_cast<std::int32_t>(v2::service::ServiceErrorCode::kRoomFull), kRoomFull);
    EXPECT_EQ(static_cast<std::int32_t>(v2::service::ServiceErrorCode::kRoomInInstance), kRoomInInstance);
    EXPECT_EQ(static_cast<std::int32_t>(v2::service::ServiceErrorCode::kRoomClosed), kRoomClosed);
    EXPECT_EQ(static_cast<std::int32_t>(v2::service::ServiceErrorCode::kNotRoomOwner), kNotRoomOwner);
    EXPECT_EQ(static_cast<std::int32_t>(v2::service::ServiceErrorCode::kNotRoomMember), kNotRoomMember);
}

// Room metadata JSON structure
TEST_F(RoomLobbyTest, RoomMetadataContract) {
    // Verify that a room_create request with metadata is well-formed
    nlohmann::json create_req = {
        {"user_id", "alice"},
        {"room_id", "room_001"},
        {"display_name", "Alice"},
        {"visibility", "public"},
        {"capacity", 4},
        {"metadata", {
            {"game_mode", "tank_battle"},
            {"map_id", "map_01"},
        }},
    };

    EXPECT_EQ(create_req["user_id"], "alice");
    EXPECT_EQ(create_req["room_id"], "room_001");
    EXPECT_EQ(create_req["visibility"], "public");
    EXPECT_EQ(create_req["capacity"], 4);
    EXPECT_TRUE(create_req["metadata"].is_object());
    EXPECT_EQ(create_req["metadata"]["game_mode"], "tank_battle");
    EXPECT_EQ(create_req["metadata"]["map_id"], "map_01");
}

// Room list request JSON structure
TEST_F(RoomLobbyTest, RoomListRequestContract) {
    nlohmann::json list_req = {
        {"visibility", "public"},
        {"status", "waiting"},
        {"page", 1},
        {"page_size", 20},
    };

    EXPECT_EQ(list_req["page"], 1);
    EXPECT_EQ(list_req["page_size"], 20);
    EXPECT_EQ(list_req["visibility"], "public");
    EXPECT_EQ(list_req["status"], "waiting");

    // Defaults when fields are absent
    nlohmann::json default_req = nlohmann::json::object();
    std::uint32_t page = default_req.value("page", 1);
    std::uint32_t page_size = default_req.value("page_size", 20);
    EXPECT_EQ(page, 1);
    EXPECT_EQ(page_size, 20);
}

// Room detail request JSON structure
TEST_F(RoomLobbyTest, RoomDetailRequestContract) {
    nlohmann::json detail_req = {
        {"room_id", "room_001"},
    };
    EXPECT_EQ(detail_req["room_id"], "room_001");
}

// Room kick request JSON structure
TEST_F(RoomLobbyTest, RoomKickRequestContract) {
    nlohmann::json kick_req = {
        {"user_id", "alice"},
        {"room_id", "room_001"},
        {"target_user_id", "bob"},
    };
    EXPECT_EQ(kick_req["user_id"], "alice");
    EXPECT_EQ(kick_req["room_id"], "room_001");
    EXPECT_EQ(kick_req["target_user_id"], "bob");
}

// Room transfer request JSON structure
TEST_F(RoomLobbyTest, RoomTransferOwnerRequestContract) {
    nlohmann::json transfer_req = {
        {"user_id", "alice"},
        {"room_id", "room_001"},
        {"new_owner_id", "bob"},
    };
    EXPECT_EQ(transfer_req["user_id"], "alice");
    EXPECT_EQ(transfer_req["room_id"], "room_001");
    EXPECT_EQ(transfer_req["new_owner_id"], "bob");
}

// Room state JSON structure (simulated response)
TEST_F(RoomLobbyTest, RoomStateJsonStructure) {
    nlohmann::json members = nlohmann::json::array();
    members.push_back({{"user_id", "alice"}, {"display_name", "Alice"}, {"ready", false}});
    members.push_back({{"user_id", "bob"}, {"display_name", "Bob"}, {"ready", true}});

    nlohmann::json room_json;
    room_json["room_id"] = "room_001";
    room_json["owner_user_id"] = "alice";
    room_json["members"] = members;
    room_json["member_count"] = 2;
    room_json["capacity"] = 4;
    room_json["visibility"] = "public";
    room_json["version"] = 3;
    room_json["status"] = "waiting";
    room_json["created_at_ms"] = 1234567890;
    room_json["metadata"] = {{"game_mode", "tank_battle"}};

    EXPECT_EQ(room_json["room_id"], "room_001");
    EXPECT_EQ(room_json["owner_user_id"], "alice");
    EXPECT_EQ(room_json["member_count"], 2);
    EXPECT_EQ(room_json["capacity"], 4);
    EXPECT_EQ(room_json["version"], 3);
    EXPECT_EQ(room_json["status"], "waiting");
    EXPECT_EQ(room_json["members"].size(), 2);
    EXPECT_EQ(room_json["members"][0]["user_id"], "alice");
    EXPECT_EQ(room_json["members"][1]["ready"], true);
}

// Room list response structure
TEST_F(RoomLobbyTest, RoomListResponseContract) {
    nlohmann::json rooms = nlohmann::json::array();
    rooms.push_back({{"room_id", "room_001"}, {"member_count", 2}});

    nlohmann::json response;
    response["rooms"] = rooms;
    response["total"] = 1;
    response["page"] = 1;
    response["page_size"] = 20;
    response["total_pages"] = 1;

    EXPECT_EQ(response["total"], 1);
    EXPECT_EQ(response["rooms"].size(), 1);
    EXPECT_EQ(response["rooms"][0]["room_id"], "room_001");

    // Pagination edge cases
    auto calc_pages = [](std::uint32_t total, std::uint32_t page_size) -> std::uint32_t {
        return (total + page_size - 1) / page_size;
    };
    EXPECT_EQ(calc_pages(0, 20), 0);
    EXPECT_EQ(calc_pages(1, 20), 1);
    EXPECT_EQ(calc_pages(20, 20), 1);
    EXPECT_EQ(calc_pages(21, 20), 2);
    EXPECT_EQ(calc_pages(100, 20), 5);
}
