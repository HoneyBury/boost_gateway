// SDK v4.1.0: Protocol codec tests (standalone, no server deps)
#include <gtest/gtest.h>
#include "boost_gateway/sdk/protocol/codec.h"
#include "boost_gateway/sdk/protocol/message.h"
using namespace boost_gateway::sdk;

TEST(CodecV4Test, EncodeDecodeLoginRequest) {
    auto enc = protocol::encode(protocol::kLoginRequest, 42, 0, "alice|token:t|Alice");
    protocol::LengthHeader h{};
    std::memcpy(h.data(), enc.data(), 4);
    std::vector<char> payload(enc.begin() + 4, enc.end());
    auto dec = protocol::decode_payload(payload);
    EXPECT_EQ(dec.message_id, protocol::kLoginRequest);
    EXPECT_EQ(dec.request_id, 42U);
    EXPECT_EQ(dec.error_code, 0);
    EXPECT_EQ(dec.body, "alice|token:t|Alice");
}

TEST(CodecV4Test, EmptyBodyRoundTrip) {
    auto enc = protocol::encode(protocol::kEchoRequest, 1, 0, "");
    protocol::LengthHeader h{};
    std::memcpy(h.data(), enc.data(), 4);
    auto len = protocol::decode_length(h);
    EXPECT_EQ(len, 11U);
    std::vector<char> p(enc.begin() + 4, enc.end());
    auto dec = protocol::decode_payload(p);
    EXPECT_TRUE(dec.body.empty());
}

TEST(CodecV4Test, ErrorResponseEncoding) {
    auto enc = protocol::encode(protocol::kErrorResponse, 99, 1003, "invalid_token");
    protocol::LengthHeader h{};
    std::memcpy(h.data(), enc.data(), 4);
    std::vector<char> p(enc.begin() + 4, enc.end());
    auto dec = protocol::decode_payload(p);
    EXPECT_EQ(dec.message_id, protocol::kErrorResponse);
    EXPECT_EQ(dec.request_id, 99U);
    EXPECT_EQ(dec.error_code, 1003);
    EXPECT_EQ(dec.body, "invalid_token");
}

TEST(CodecV4Test, LargeBodyPreserved) {
    std::string big(10000, 'X');
    auto enc = protocol::encode(protocol::kEchoRequest, 1, 0, big);
    protocol::LengthHeader h{};
    std::memcpy(h.data(), enc.data(), 4);
    EXPECT_EQ(protocol::decode_length(h), 10011U);
    std::vector<char> p(enc.begin() + 4, enc.end());
    auto dec = protocol::decode_payload(p);
    EXPECT_EQ(dec.body.size(), 10000U);
}

TEST(CodecV4Test, AllMessageIds) {
    auto ids = {1,2,1001,1002,2001,2002,3001,3002,3005,3006,4001,4003,4006,9001};
    for (auto id : ids) {
        auto e = protocol::encode(id, 1, 0, "t");
        protocol::LengthHeader h{};
        std::memcpy(h.data(), e.data(), 4);
        std::vector<char> p(e.begin()+4, e.end());
        EXPECT_EQ(protocol::decode_payload(p).message_id, id);
    }
}

TEST(CodecV4Test, MessageIdsMatchServerProtocol) {
    EXPECT_EQ(protocol::kLoginRequest, 2001U);
    EXPECT_EQ(protocol::kLoginResponse, 2002U);
    EXPECT_EQ(protocol::kRoomLeaveRequest, 3005U);
    EXPECT_EQ(protocol::kRoomLeaveResponse, 3006U);
    EXPECT_EQ(protocol::kBattleStartRequest, 4001U);
    EXPECT_EQ(protocol::kErrorResponse, 9001U);
}
