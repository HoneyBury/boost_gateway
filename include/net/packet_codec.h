#pragma once

#include <algorithm>
#include <array>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace net::packet {

constexpr std::size_t kLengthHeaderSize = 4;
constexpr std::size_t kMessageIdSize = 2;

struct DecodedPacket {
    std::uint16_t message_id = 0;
    std::string body;
};

using LengthHeader = std::array<unsigned char, kLengthHeaderSize>;

inline std::string encode(std::uint16_t message_id, std::string_view body) {
    const auto body_length = static_cast<std::uint32_t>(kMessageIdSize + body.size());

    std::string packet;
    packet.resize(kLengthHeaderSize + body_length);

    packet[0] = static_cast<char>((body_length >> 24U) & 0xFFU);
    packet[1] = static_cast<char>((body_length >> 16U) & 0xFFU);
    packet[2] = static_cast<char>((body_length >> 8U) & 0xFFU);
    packet[3] = static_cast<char>(body_length & 0xFFU);
    packet[4] = static_cast<char>((message_id >> 8U) & 0xFFU);
    packet[5] = static_cast<char>(message_id & 0xFFU);

    if (!body.empty()) {
        std::copy(body.begin(), body.end(), packet.begin() + 6);
    }

    return packet;
}

inline std::uint32_t decode_length(const LengthHeader& header) {
    return (static_cast<std::uint32_t>(header[0]) << 24U) |
           (static_cast<std::uint32_t>(header[1]) << 16U) |
           (static_cast<std::uint32_t>(header[2]) << 8U) |
           static_cast<std::uint32_t>(header[3]);
}

inline DecodedPacket decode_payload(const std::vector<char>& payload) {
    if (payload.size() < kMessageIdSize) {
        throw std::invalid_argument("payload is smaller than message id header");
    }

    const auto message_id = static_cast<std::uint16_t>(
        (static_cast<std::uint16_t>(static_cast<unsigned char>(payload[0])) << 8U) |
        static_cast<std::uint16_t>(static_cast<unsigned char>(payload[1])));

    return DecodedPacket{
        message_id,
        std::string(payload.begin() + static_cast<std::ptrdiff_t>(kMessageIdSize), payload.end())};
}

}  // namespace net::packet
