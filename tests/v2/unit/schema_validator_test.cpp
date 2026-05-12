// v2.3.0: Schema validator tests

#include <gtest/gtest.h>

#include "v2/gateway/schema_validator.h"

TEST(SchemaValidatorTest, ValidLoginRequestPasses) {
    v2::gateway::SchemaValidator validator;
    auto result = validator.validate(
        "login_request", R"({"user_id":"alice","token":"jwt_token"})");
    EXPECT_TRUE(result.valid) << result.error;
}

TEST(SchemaValidatorTest, MissingRequiredFieldRejected) {
    v2::gateway::SchemaValidator validator;
    auto result = validator.validate(
        "login_request", R"({"user_id":"alice"})");  // missing token
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "missing_required_field:token");
}

TEST(SchemaValidatorTest, TypeMismatchRejected) {
    v2::gateway::SchemaValidator validator;
    // user_id should be string, not integer
    auto result = validator.validate(
        "login_request", R"({"user_id":123,"token":"jwt"})");
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "type_mismatch:user_id:expected_string");
}

TEST(SchemaValidatorTest, EmptyRequiredFieldRejected) {
    v2::gateway::SchemaValidator validator;
    auto result = validator.validate(
        "login_request", R"({"user_id":"","token":"jwt"})");
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "empty_field:user_id");
}

TEST(SchemaValidatorTest, InvalidJsonRejected) {
    v2::gateway::SchemaValidator validator;
    auto result = validator.validate("login_request", "not json");
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "invalid_json");
}

TEST(SchemaValidatorTest, UnknownMessageTypeRejected) {
    v2::gateway::SchemaValidator validator;
    auto result = validator.validate("unknown_type", R"({"x":1})");
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "no_schema_registered");
}

TEST(SchemaValidatorTest, RoomCreateValid) {
    v2::gateway::SchemaValidator validator;
    auto result = validator.validate(
        "room_create", R"({"user_id":"alice","room_id":"room_001"})");
    EXPECT_TRUE(result.valid) << result.error;
}

TEST(SchemaValidatorTest, RoomReadyBooleanCheck) {
    v2::gateway::SchemaValidator validator;
    // ready should be boolean, not string
    auto result = validator.validate(
        "room_ready", R"({"user_id":"alice","room_id":"r1","ready":"yes"})");
    EXPECT_FALSE(result.valid);
    EXPECT_EQ(result.error, "type_mismatch:ready:expected_boolean");
}

TEST(SchemaValidatorTest, HasSchemaCheck) {
    v2::gateway::SchemaValidator validator;
    EXPECT_TRUE(validator.has_schema("login_request"));
    EXPECT_TRUE(validator.has_schema("room_create"));
    EXPECT_FALSE(validator.has_schema("nonexistent"));
}
