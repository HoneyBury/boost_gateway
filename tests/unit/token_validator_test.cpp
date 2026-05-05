#include "game/login/token_validator.h"

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
