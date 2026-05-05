#pragma once

#include "net/packet_codec.h"

#include <algorithm>
#include <cstdint>
#include <string>
#include <string_view>

namespace net::packet {

constexpr std::size_t kCompressionThreshold = 512;

// Minimal compression wrapper. Integrates with flags::kCompressed.
// Uses a simple length-prefixed storage: [4B uncompressed_len][data]
// This is a stub — replace with zlib/zstd for production.
inline std::string compress_body(std::string_view body) {
    if (body.size() < kCompressionThreshold) {
        return std::string(body);
    }
    // Stub: store uncompressed with a marker.
    // Replace with real zlib: compress(body) → [4B orig_len][compressed_data]
    std::string result;
    result.reserve(4 + body.size());
    const auto orig_len = static_cast<std::uint32_t>(body.size());
    result.push_back(static_cast<char>(orig_len & 0xFF));
    result.push_back(static_cast<char>((orig_len >> 8) & 0xFF));
    result.push_back(static_cast<char>((orig_len >> 16) & 0xFF));
    result.push_back(static_cast<char>((orig_len >> 24) & 0xFF));
    result.append(body);
    return result;
}

inline std::string decompress_body(std::string_view body) {
    if (body.size() < 4) return std::string(body);
    // Stub: read length prefix, verify, return data
    std::uint32_t orig_len = static_cast<unsigned char>(body[0]) |
                             (static_cast<std::uint32_t>(static_cast<unsigned char>(body[1])) << 8) |
                             (static_cast<std::uint32_t>(static_cast<unsigned char>(body[2])) << 16) |
                             (static_cast<std::uint32_t>(static_cast<unsigned char>(body[3])) << 24);
    auto data = body.substr(4);
    if (data.size() >= orig_len) {
        return std::string(data.data(), orig_len);
    }
    return std::string(data);
}

inline bool should_compress(std::size_t body_size) {
    return body_size >= kCompressionThreshold;
}

}  // namespace net::packet
