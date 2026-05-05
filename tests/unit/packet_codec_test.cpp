#include "net/packet_codec.h"

#include <gtest/gtest.h>

TEST(PacketCodecTest, EncodeAndDecodeRoundTrip) {
    const auto encoded = net::packet::encode(1001, 77, -3, "payload");

    net::packet::LengthHeader header{};
    std::copy(encoded.begin(), encoded.begin() + 4, header.begin());
    const auto length = net::packet::decode_length(header);

    std::vector<char> payload(encoded.begin() + 4, encoded.end());
    const auto decoded = net::packet::decode_payload(payload);

    EXPECT_EQ(length, payload.size());
    EXPECT_EQ(decoded.message_id, 1001);
    EXPECT_EQ(decoded.request_id, 77U);
    EXPECT_EQ(decoded.error_code, -3);
    EXPECT_EQ(decoded.body, "payload");
}

TEST(PacketCodecTest, DecodePayloadRejectsTooShortPacket) {
    std::vector<char> invalid_payload(1, '\0');
    EXPECT_THROW((void)net::packet::decode_payload(invalid_payload), std::invalid_argument);
}
