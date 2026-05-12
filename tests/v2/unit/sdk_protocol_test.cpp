// v3.0.0 V2: SDK protocol encoding validation
// Verifies protocol encoding/decoding consistency across SDK languages.

#include <gtest/gtest.h>
#include "net/packet_codec.h"
#include "net/protocol.h"
#include <cstring>

// ─── C++ encode → decode round-trip ────────────────────────────────────

TEST(SdkProtocolTest, CppEncodeDecodeRoundTrip) {
    std::string body = "alice|token:alice_token|Alice";
    auto encoded = net::packet::encode(
        net::protocol::kLoginRequest, 42, 0, body);

    net::packet::LengthHeader header{};
    std::memcpy(header.data(), encoded.data(), 4);
    auto total = net::packet::decode_length(header);

    std::vector<char> payload(encoded.begin() + 4, encoded.end());
    auto decoded = net::packet::decode_payload(payload);

    EXPECT_EQ(decoded.message_id, net::protocol::kLoginRequest);
    EXPECT_EQ(decoded.request_id, 42U);
    EXPECT_EQ(decoded.error_code, 0);
    EXPECT_EQ(decoded.body, body);
}

TEST(SdkProtocolTest, EmptyBodyRoundTrip) {
    auto encoded = net::packet::encode(
        net::protocol::kHeartbeatRequest, 1, 0, "");

    net::packet::LengthHeader header{};
    std::memcpy(header.data(), encoded.data(), 4);
    auto total = net::packet::decode_length(header);

    std::vector<char> payload(encoded.begin() + 4, encoded.end());
    auto decoded = net::packet::decode_payload(payload);

    EXPECT_EQ(decoded.message_id, net::protocol::kHeartbeatRequest);
    EXPECT_TRUE(decoded.body.empty());
}

TEST(SdkProtocolTest, LargeBodyRoundTrip) {
    std::string large(5000, 'x');
    auto encoded = net::packet::encode(
        net::protocol::kEchoRequest, 100, 0, large);

    net::packet::LengthHeader header{};
    std::memcpy(header.data(), encoded.data(), 4);
    auto total = net::packet::decode_length(header);

    EXPECT_EQ(total, 11U + 5000U);

    std::vector<char> payload(encoded.begin() + 4, encoded.end());
    auto decoded = net::packet::decode_payload(payload);
    EXPECT_EQ(decoded.body.size(), 5000U);
}

TEST(SdkProtocolTest, SpecialCharactersInBody) {
    std::string body = "user:alice\ntoken:abc|def:ghi";
    auto encoded = net::packet::encode(
        net::protocol::kLoginRequest, 1, 0, body);

    net::packet::LengthHeader header{};
    std::memcpy(header.data(), encoded.data(), 4);

    std::vector<char> payload(encoded.begin() + 4, encoded.end());
    auto decoded = net::packet::decode_payload(payload);
    EXPECT_EQ(decoded.body, body);
}

TEST(SdkProtocolTest, ErrorResponseEncoding) {
    std::string body = "invalid_token";
    auto encoded = net::packet::encode(
        net::protocol::kErrorResponse, 55,
        static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken),
        body);

    net::packet::LengthHeader header{};
    std::memcpy(header.data(), encoded.data(), 4);

    std::vector<char> payload(encoded.begin() + 4, encoded.end());
    auto decoded = net::packet::decode_payload(payload);

    EXPECT_EQ(decoded.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(decoded.request_id, 55U);
    EXPECT_EQ(decoded.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken));
    EXPECT_EQ(decoded.body, body);
}

TEST(SdkProtocolTest, AllMessageIdsEncodeCorrectly) {
    // Verify all protocol message IDs encode/decode correctly
    std::vector<std::uint16_t> ids = {
        1, 2,           // heartbeat
        1001, 1002,     // echo
        2001, 2002,     // login
        3001, 3002, 3003, 3004, 3005, 3006, 3007, 3008, 3009,  // room
        4001, 4002, 4003, 4004, 4005, 4006,  // battle
        5001, 5005,     // admin
        9001,           // error
    };

    for (auto id : ids) {
        auto encoded = net::packet::encode(id, 1, 0, "test");
        net::packet::LengthHeader header{};
        std::memcpy(header.data(), encoded.data(), 4);
        std::vector<char> payload(encoded.begin() + 4, encoded.end());
        auto decoded = net::packet::decode_payload(payload);
        EXPECT_EQ(decoded.message_id, id) << "msg_id=" << id;
    }
}
