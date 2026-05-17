// SDK v4.1.0: C ABI boundary tests.
#include <gtest/gtest.h>

#include "boost_gateway/sdk/c_api.h"
#include "boost_gateway/sdk/version.h"

#include <cstring>

TEST(CApiV41Test, VersionMatchesGeneratedHeader) {
    ASSERT_NE(gsdk_version(), nullptr);
    EXPECT_STREQ(gsdk_version(), BOOST_GATEWAY_SDK_VERSION);
}

TEST(CApiV41Test, NullHandleOperationsAreSafe) {
    EXPECT_EQ(gsdk_is_connected(nullptr), 0);
    EXPECT_EQ(gsdk_connect(nullptr, "127.0.0.1", 9201, 1), 0);
    gsdk_disconnect(nullptr);
    gsdk_destroy(nullptr);

    auto login = gsdk_login(nullptr, "u", "t", 1);
    EXPECT_EQ(login.ok, 0);
    EXPECT_LT(login.error_code, 0);
    EXPECT_STREQ(login.error_message, "invalid_argument");

    auto room = gsdk_create_room(nullptr, "r", 1);
    EXPECT_EQ(room.ok, 0);
    EXPECT_LT(room.error_code, 0);

    auto battle = gsdk_start_battle(nullptr, "r", 1);
    EXPECT_EQ(battle.ok, 0);
    EXPECT_LT(battle.error_code, 0);

    auto input = gsdk_send_battle_input(nullptr, "move:1,2", 1);
    EXPECT_EQ(input.ok, 0);
    EXPECT_LT(input.error_code, 0);

    auto echo = gsdk_echo(nullptr, "hello", 1);
    EXPECT_EQ(echo.ok, 0);
    EXPECT_STREQ(echo.body, "invalid_argument");
}

TEST(CApiV41Test, InvalidArgumentsReturnErrors) {
    gsdk_client_t* client = gsdk_create();
    ASSERT_NE(client, nullptr);

    EXPECT_EQ(gsdk_connect(client, nullptr, 9201, 1), 0);
    EXPECT_EQ(gsdk_connect(client, "127.0.0.1", 9201, -1), 0);

    auto login = gsdk_login(client, nullptr, "t", 1);
    EXPECT_EQ(login.ok, 0);
    EXPECT_LT(login.error_code, 0);

    auto room = gsdk_join_room(client, nullptr, 1);
    EXPECT_EQ(room.ok, 0);
    EXPECT_LT(room.error_code, 0);

    auto battle = gsdk_start_battle(client, nullptr, 1);
    EXPECT_EQ(battle.ok, 0);
    EXPECT_LT(battle.error_code, 0);

    auto input = gsdk_send_battle_input(client, nullptr, 1);
    EXPECT_EQ(input.ok, 0);
    EXPECT_LT(input.error_code, 0);

    auto echo = gsdk_echo(client, nullptr, 1);
    EXPECT_EQ(echo.ok, 0);
    EXPECT_STREQ(echo.body, "invalid_argument");

    gsdk_destroy(client);
}

TEST(CApiV41Test, UnconnectedCallsFailWithoutThrowing) {
    gsdk_client_t* client = gsdk_create();
    ASSERT_NE(client, nullptr);
    EXPECT_EQ(gsdk_is_connected(client), 0);

    auto echo = gsdk_echo(client, "hello", 1);
    EXPECT_EQ(echo.ok, 0);

    auto login = gsdk_login(client, "u", "t", 1);
    EXPECT_EQ(login.ok, 0);

    auto room = gsdk_leave_room(client, "r", 1);
    EXPECT_EQ(room.ok, 0);

    gsdk_destroy(client);
}
