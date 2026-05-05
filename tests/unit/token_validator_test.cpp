#include "game/login/token_validator.h"

#include <filesystem>
#include <fstream>

#include <gtest/gtest.h>

TEST(TokenValidatorTest, AcceptsMatchingDevToken) {
    game::login::DevTokenValidator validator;

    const auto result = validator.validate("player_01", "token:player_01", std::string("PlayerOne"));
    EXPECT_TRUE(result.ok);
    EXPECT_EQ(result.user_id, "player_01");
    EXPECT_EQ(result.display_name, "PlayerOne");
}

TEST(TokenValidatorTest, RejectsInvalidToken) {
    game::login::DevTokenValidator validator;

    const auto result = validator.validate("player_01", "wrong_token", std::nullopt);
    EXPECT_FALSE(result.ok);
}

TEST(TokenValidatorTest, LoadsAndValidatesJsonUsers) {
    const auto path = std::filesystem::temp_directory_path() / "auth_users_test.json";
    {
        std::ofstream output(path);
        output << "{\n";
        output << "  \"users\": [\n";
        output << "    {\n";
        output << "      \"user_id\": \"player_json\",\n";
        output << "      \"token\": \"json_token\",\n";
        output << "      \"display_name\": \"JsonPlayer\"\n";
        output << "    }\n";
        output << "  ]\n";
        output << "}\n";
    }

    const auto validator = game::login::JsonFileTokenValidator::load_from_file(path);
    ASSERT_TRUE(validator.has_value());
    EXPECT_EQ(validator->user_count(), 1U);

    const auto ok_result = validator->validate("player_json", "json_token", std::nullopt);
    EXPECT_TRUE(ok_result.ok);
    EXPECT_EQ(ok_result.user_id, "player_json");
    EXPECT_EQ(ok_result.display_name, "JsonPlayer");

    const auto failed_result = validator->validate("player_json", "wrong_token", std::nullopt);
    EXPECT_FALSE(failed_result.ok);

    std::filesystem::remove(path);
}
