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
    EXPECT_TRUE(v2::gateway::validate_battle_start_command_body(*start_empty));

    const auto start_room = v2::gateway::parse_battle_start_command_body("room_alpha");
    ASSERT_TRUE(start_room.has_value());
    ASSERT_TRUE(start_room->room_id.has_value());
    EXPECT_EQ(*start_room->room_id, "room_alpha");
    EXPECT_TRUE(v2::gateway::validate_battle_start_command_body(*start_room));

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

TEST(V2GatewayCommandParserTest, AcceptsBattleStartWithoutRoomIdAsCurrentRoom) {
    const auto parsed = v2::gateway::parse_battle_start_command_body("");
    ASSERT_TRUE(parsed.has_value());
    EXPECT_FALSE(parsed->room_id.has_value());
    EXPECT_TRUE(v2::gateway::validate_battle_start_command_body(*parsed));
    EXPECT_FALSE(v2::gateway::validate_battle_start_command_body(
        v2::gateway::ParsedBattleStartCommandBody{.room_id = std::string{}}));
}

TEST(V2GatewayCommandParserTest, ParsesFinishVariantsAsStructuredReasons) {
    const auto surrender = v2::gateway::parse_battle_input_command_body("finish:surrender");
    ASSERT_TRUE(surrender.has_value());
    EXPECT_TRUE(surrender->is_finish_request);
    EXPECT_EQ(surrender->finish_reason, v2::battle::BattleFinishReason::kSurrender);

    const auto timeout = v2::gateway::parse_battle_input_command_body("finish:timeout");
    ASSERT_TRUE(timeout.has_value());
    EXPECT_TRUE(timeout->is_finish_request);
    EXPECT_EQ(timeout->finish_reason, v2::battle::BattleFinishReason::kTimeout);

    const auto user_requested = v2::gateway::parse_battle_input_command_body("finish:user_requested");
    ASSERT_TRUE(user_requested.has_value());
    EXPECT_TRUE(user_requested->is_finish_request);
    EXPECT_EQ(user_requested->finish_reason, v2::battle::BattleFinishReason::kUserRequested);

    const auto custom = v2::gateway::parse_battle_input_command_body("finish:custom_reason");
    ASSERT_TRUE(custom.has_value());
    EXPECT_TRUE(custom->is_finish_request);
    EXPECT_EQ(custom->finish_reason, v2::battle::BattleFinishReason::kFinished);
}

TEST(V2GatewayCommandParserTest, RejectsInvalidBattleInputBody) {
    EXPECT_FALSE(v2::gateway::validate_battle_input_command_body(
        v2::gateway::ParsedBattleInputCommandBody{}));
}

TEST(V2GatewayCommandParserTest, ParsesAndValidatesMatchCommandBodies) {
    const auto join = v2::gateway::parse_match_command_body("alice|1234|2v2");
    ASSERT_TRUE(join.has_value());
    EXPECT_EQ(join->user_id, "alice");
    EXPECT_EQ(join->mmr, 1234);
    EXPECT_EQ(join->mode, "2v2");
    EXPECT_TRUE(v2::gateway::validate_match_command_body(*join));

    const auto defaults = v2::gateway::parse_match_command_body("bob");
    ASSERT_TRUE(defaults.has_value());
    EXPECT_EQ(defaults->user_id, "bob");
    EXPECT_EQ(defaults->mmr, 1000);
    EXPECT_EQ(defaults->mode, "1v1");
    EXPECT_TRUE(v2::gateway::validate_match_command_body(*defaults));

    const auto invalid_mode = v2::gateway::parse_match_command_body("bob|1000|solo");
    ASSERT_TRUE(invalid_mode.has_value());
    EXPECT_FALSE(v2::gateway::validate_match_command_body(*invalid_mode));

    EXPECT_FALSE(v2::gateway::parse_match_command_body("").has_value());
    EXPECT_FALSE(v2::gateway::parse_match_command_body("bob|not_a_number|1v1").has_value());
}

TEST(V2GatewayCommandParserTest, ParsesAndValidatesLeaderboardCommandBodies) {
    const auto submit = v2::gateway::parse_leaderboard_submit_command_body("alice|Alice|9001");
    ASSERT_TRUE(submit.has_value());
    EXPECT_EQ(submit->user_id, "alice");
    EXPECT_EQ(submit->display_name, "Alice");
    EXPECT_EQ(submit->score, 9001);
    EXPECT_TRUE(v2::gateway::validate_leaderboard_submit_command_body(*submit));

    EXPECT_FALSE(v2::gateway::parse_leaderboard_submit_command_body("alice|Alice").has_value());
    EXPECT_FALSE(v2::gateway::parse_leaderboard_submit_command_body("alice|Alice|NaN").has_value());

    const auto default_top = v2::gateway::parse_leaderboard_top_command_body("");
    ASSERT_TRUE(default_top.has_value());
    EXPECT_EQ(*default_top, 10U);

    const auto explicit_top = v2::gateway::parse_leaderboard_top_command_body("25");
    ASSERT_TRUE(explicit_top.has_value());
    EXPECT_EQ(*explicit_top, 25U);

    EXPECT_FALSE(v2::gateway::parse_leaderboard_top_command_body("zero").has_value());

    const auto rank = v2::gateway::parse_leaderboard_rank_command_body("alice");
    ASSERT_TRUE(rank.has_value());
    EXPECT_EQ(*rank, "alice");
    EXPECT_FALSE(v2::gateway::parse_leaderboard_rank_command_body("").has_value());
}
