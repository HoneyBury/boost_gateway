#include "net/packet_codec.h"
#include "net/packet_fragment.h"

#include <gtest/gtest.h>

#include <algorithm>
#include <cstdint>
#include <random>
#include <string>
#include <vector>

TEST(PacketFuzzTest, DecodeEmptyPayload) {
    std::vector<char> empty;
    EXPECT_THROW(net::packet::decode_payload(empty), std::invalid_argument);
}

TEST(PacketFuzzTest, DecodeTruncatedPayloads) {
    // Test payloads from 1 to kFixedMetadataSize-1 bytes
    for (std::size_t i = 1; i < net::packet::kFixedMetadataSize; ++i) {
        std::vector<char> payload(i, '\x00');
        EXPECT_THROW(net::packet::decode_payload(payload), std::invalid_argument);
    }
}

TEST(PacketFuzzTest, DecodeMinimumValidPayload) {
    std::vector<char> payload(net::packet::kFixedMetadataSize, '\x00');
    EXPECT_NO_THROW({
        auto p = net::packet::decode_payload(payload);
        EXPECT_EQ(p.message_id, 0);
        EXPECT_EQ(p.request_id, 0);
        EXPECT_EQ(p.error_code, 0);
        EXPECT_EQ(p.flags, 0);
        EXPECT_TRUE(p.body.empty());
    });
}

TEST(PacketFuzzTest, DecodeMaxSizePayload) {
    std::vector<char> payload(net::packet::kFixedMetadataSize + 65535, '\x42');
    EXPECT_NO_THROW({
        auto p = net::packet::decode_payload(payload);
        EXPECT_EQ(p.body.size(), 65535);
    });
}

TEST(PacketFuzzTest, DecodeRandomBytesNoCrash) {
    std::mt19937 rng(42);
    std::uniform_int_distribution<int> size_dist(0, 256);
    std::uniform_int_distribution<unsigned short> byte_dist(0, 255);

    for (int i = 0; i < 1000; ++i) {
        auto size = size_dist(rng);
        std::vector<char> payload(size);
        std::generate(payload.begin(), payload.end(), [&] { return static_cast<char>(byte_dist(rng)); });

        // Must never crash, may throw
        try {
            net::packet::decode_payload(payload);
        } catch (const std::invalid_argument&) {
            // expected for short payloads
        } catch (...) {
            FAIL() << "Unexpected exception for payload size " << size;
        }
    }
}

TEST(PacketFuzzTest, EncodeDecodeRoundTripWithFlags) {
    std::string body(1024, 'Z');
    auto packet = net::packet::encode(42, 123, 0, body, 0xAB);
    // Decode length header manually
    net::packet::LengthHeader hdr;
    std::copy_n(packet.begin(), 4, hdr.begin());
    auto len = net::packet::decode_length(hdr);
    EXPECT_EQ(len + 4, packet.size());

    // Extract payload
    std::vector<char> payload(packet.begin() + 4, packet.end());
    auto decoded = net::packet::decode_payload(payload);
    EXPECT_EQ(decoded.message_id, 42);
    EXPECT_EQ(decoded.request_id, 123);
    EXPECT_EQ(decoded.flags, 0xAB);
    EXPECT_EQ(decoded.body, body);
}

TEST(PacketFuzzTest, FragmentFlagsEncoding) {
    using namespace net::packet;
    std::uint8_t f0_flags = fragment_flags::kFragment | static_cast<std::uint8_t>(3 << 4) | 0;
    EXPECT_TRUE((f0_flags & fragment_flags::kFragment) != 0);
    EXPECT_EQ((f0_flags >> 4) & 0x07, 3u);

    std::uint8_t f_last_flags = fragment_flags::kFragment | fragment_flags::kLastFragment |
                                static_cast<std::uint8_t>(3 << 4) | 2;
    EXPECT_TRUE((f_last_flags & fragment_flags::kLastFragment) != 0);
}

TEST(PacketFuzzTest, FragmentPacketSplitsLargeBody) {
    std::string big_body(net::packet::kFragmentThreshold + 1, 'Z');
    auto fragments = net::packet::fragment_packet(100, 1, 0, big_body);
    EXPECT_GT(fragments.size(), 1u);
    // Total body across all fragments should match original size
    std::size_t total = 0;
    for (const auto& f : fragments) total += f.body.size();
    EXPECT_EQ(total, big_body.size());
}

TEST(PacketFuzzTest, NonFragmentPassesThroughAssembler) {
    net::packet::FragmentAssembler assembler;
    net::packet::DecodedPacket pkt{42, 0, 0, 0, "hello"};
    auto result = assembler.feed(pkt);
    ASSERT_TRUE(result.has_value());
    EXPECT_EQ(result->body, "hello");
}
