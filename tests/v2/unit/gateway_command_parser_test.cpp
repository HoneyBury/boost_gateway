#include <gtest/gtest.h>

#include "v2/gateway/gateway_command_parser.h"

TEST(V2GatewayCommandParserTest, ParsesLoginBodyIntoStructuredFields) {
    const auto parsed = v2::gateway::parse_login_command_body("player_01|token:player_01|PlayerOne");
    ASSERT_TRUE(parsed.has_value());
    EXPECT_EQ(parsed->user_id, "player_01");
    EXPECT_EQ(parsed->token, "token:player_01");
    ASSERT_TRUE(parsed->display_name.has_value());
    EXPECT_EQ(*parsed->display_name, "PlayerOne");
    EXPECT_TRUE(v2::gateway::validate_login_command_body(*parsed));
}

TEST(V2GatewayCommandParserTest, RejectsEmptyLoginUserIdAndInvalidReadyState) {
    const auto parsed = v2::gateway::parse_login_command_body("|token:broken");
    ASSERT_TRUE(parsed.has_value());
    EXPECT_FALSE(v2::gateway::validate_login_command_body(*parsed));

    EXPECT_EQ(v2::gateway::parse_room_ready_body("true"), true);
    EXPECT_EQ(v2::gateway::parse_room_ready_body("false"), false);
    EXPECT_FALSE(v2::gateway::parse_room_ready_body("ready").has_value());
}

TEST(V2GatewayCommandParserTest, ParsesAndValidatesRoomIdentifiers) {
    const auto room_id = v2::gateway::parse_room_id_body("room_alpha");
    ASSERT_TRUE(room_id.has_value());
    EXPECT_EQ(*room_id, "room_alpha");
    EXPECT_TRUE(v2::gateway::validate_room_id_body("room_beta"));
    EXPECT_FALSE(v2::gateway::validate_room_id_body(""));
    EXPECT_FALSE(v2::gateway::parse_room_id_body("").has_value());
}

TEST(V2GatewayCommandParserTest, ParsesBattleStartAndInputBodies) {
    const auto start_empty = v2::gateway::parse_battle_start_command_body("");
    ASSERT_TRUE(start_empty.has_value());
    EXPECT_FALSE(start_empty->room_id.has_value());

    const auto start_room = v2::gateway::parse_battle_start_command_body("room_alpha");
    ASSERT_TRUE(start_room.has_value());
    ASSERT_TRUE(start_room->room_id.has_value());
    EXPECT_EQ(*start_room->room_id, "room_alpha");

    const auto finish = v2::gateway::parse_battle_input_command_body("finish:surrender");
    ASSERT_TRUE(finish.has_value());
    EXPECT_TRUE(finish->is_finish_request);
    EXPECT_EQ(finish->finish_reason, v2::battle::BattleFinishReason::kSurrender);
    EXPECT_TRUE(v2::gateway::validate_battle_input_command_body(*finish));

    const auto input = v2::gateway::parse_battle_input_command_body("move:left");
    ASSERT_TRUE(input.has_value());
    EXPECT_FALSE(input->is_finish_request);
    EXPECT_EQ(input->input_data, "move:left");
    EXPECT_TRUE(v2::gateway::validate_battle_input_command_body(*input));
    EXPECT_FALSE(v2::gateway::parse_battle_input_command_body("").has_value());
}
