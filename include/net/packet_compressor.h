#pragma once

#include "net/packet_codec.h"

#include <algorithm>
#include <cstdint>
#include <string>
#include <string_view>

#ifdef HAS_ZLIB
#include <zlib.h>
#endif

namespace net::packet {

constexpr std::size_t kCompressionThreshold = 512;

#ifdef HAS_ZLIB

inline std::string compress_body(std::string_view body) {
    if (body.size() < kCompressionThreshold) return std::string(body);

    uLongf dest_len = compressBound(static_cast<uLong>(body.size()));
    std::string result(dest_len + sizeof(std::uint32_t), '\0');

    const auto orig_len = static_cast<std::uint32_t>(body.size());
    result[0] = static_cast<char>(orig_len & 0xFF);
    result[1] = static_cast<char>((orig_len >> 8) & 0xFF);
    result[2] = static_cast<char>((orig_len >> 16) & 0xFF);
    result[3] = static_cast<char>((orig_len >> 24) & 0xFF);

    if (compress2(reinterpret_cast<Bytef*>(result.data() + 4), &dest_len,
                  reinterpret_cast<const Bytef*>(body.data()),
                  static_cast<uLong>(body.size()), Z_BEST_SPEED) == Z_OK) {
        result.resize(dest_len + 4);
        return result;
    }
    return std::string(body);
}

inline std::string decompress_body(std::string_view body) {
    if (body.size() < 4) return std::string(body);

    std::uint32_t orig_len = static_cast<unsigned char>(body[0]) |
                             (static_cast<std::uint32_t>(static_cast<unsigned char>(body[1])) << 8) |
                             (static_cast<std::uint32_t>(static_cast<unsigned char>(body[2])) << 16) |
                             (static_cast<std::uint32_t>(static_cast<unsigned char>(body[3])) << 24);

    std::string result(orig_len, '\0');
    uLongf dest_len = orig_len;
    if (uncompress(reinterpret_cast<Bytef*>(result.data()), &dest_len,
                   reinterpret_cast<const Bytef*>(body.data() + 4),
                   static_cast<uLong>(body.size() - 4)) == Z_OK) {
        result.resize(dest_len);
        return result;
    }
    return std::string(body);
}

#else

// Fallback: length-prefixed passthrough (no compression)
inline std::string compress_body(std::string_view body) {
    if (body.size() < kCompressionThreshold) return std::string(body);
    std::string result(4 + body.size(), '\0');
    const auto len = static_cast<std::uint32_t>(body.size());
    result[0] = static_cast<char>(len & 0xFF);
    result[1] = static_cast<char>((len >> 8) & 0xFF);
    result[2] = static_cast<char>((len >> 16) & 0xFF);
    result[3] = static_cast<char>((len >> 24) & 0xFF);
    std::memcpy(result.data() + 4, body.data(), body.size());
    return result;
}

inline std::string decompress_body(std::string_view body) {
    if (body.size() < 4) return std::string(body);
    std::uint32_t len = static_cast<unsigned char>(body[0]) |
                       (static_cast<std::uint32_t>(static_cast<unsigned char>(body[1])) << 8) |
                       (static_cast<std::uint32_t>(static_cast<unsigned char>(body[2])) << 16) |
                       (static_cast<std::uint32_t>(static_cast<unsigned char>(body[3])) << 24);
    if (len <= body.size() - 4) return std::string(body.substr(4, len));
    return std::string(body);
}

#endif

inline bool should_compress(std::size_t body_size) {
    return body_size >= kCompressionThreshold;
}

}  // namespace net::packet
