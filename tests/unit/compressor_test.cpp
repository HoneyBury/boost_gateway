#include "net/packet_compressor.h"

#include <gtest/gtest.h>

TEST(PacketCompressorTest, SmallBodyNotCompressed) {
    EXPECT_FALSE(net::packet::should_compress(100));
    EXPECT_TRUE(net::packet::should_compress(1000));
}

TEST(PacketCompressorTest, CompressDecompressRoundTrip) {
    std::string body(2000, 'Z');
    auto compressed = net::packet::compress_body(body);
    EXPECT_GT(compressed.size(), 4u);

    auto decompressed = net::packet::decompress_body(compressed);
    EXPECT_EQ(decompressed, body);
}

TEST(PacketCompressorTest, DecompressEmptyAndShort) {
    EXPECT_EQ(net::packet::decompress_body(""), "");
    EXPECT_EQ(net::packet::decompress_body("ab"), "ab");
}
