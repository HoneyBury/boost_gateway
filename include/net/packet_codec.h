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
constexpr std::size_t kRequestIdSize = 4;
constexpr std::size_t kErrorCodeSize = 4;
constexpr std::size_t kFixedMetadataSize = kMessageIdSize + kRequestIdSize + kErrorCodeSize;

struct DecodedPacket {
    std::uint16_t message_id = 0;
    std::uint32_t request_id = 0;
    std::int32_t error_code = 0;
    std::string body;
};

using LengthHeader = std::array<unsigned char, kLengthHeaderSize>;

inline std::string encode(std::uint16_t message_id,
                          std::uint32_t request_id,
                          std::int32_t error_code,
                          std::string_view body) {
    const auto body_length = static_cast<std::uint32_t>(kFixedMetadataSize + body.size());

    std::string packet;
    packet.resize(kLengthHeaderSize + body_length);

    packet[0] = static_cast<char>((body_length >> 24U) & 0xFFU);
    packet[1] = static_cast<char>((body_length >> 16U) & 0xFFU);
    packet[2] = static_cast<char>((body_length >> 8U) & 0xFFU);
    packet[3] = static_cast<char>(body_length & 0xFFU);
    packet[4] = static_cast<char>((message_id >> 8U) & 0xFFU);
    packet[5] = static_cast<char>(message_id & 0xFFU);
    packet[6] = static_cast<char>((request_id >> 24U) & 0xFFU);
    packet[7] = static_cast<char>((request_id >> 16U) & 0xFFU);
    packet[8] = static_cast<char>((request_id >> 8U) & 0xFFU);
    packet[9] = static_cast<char>(request_id & 0xFFU);
    packet[10] = static_cast<char>((static_cast<std::uint32_t>(error_code) >> 24U) & 0xFFU);
    packet[11] = static_cast<char>((static_cast<std::uint32_t>(error_code) >> 16U) & 0xFFU);
    packet[12] = static_cast<char>((static_cast<std::uint32_t>(error_code) >> 8U) & 0xFFU);
    packet[13] = static_cast<char>(static_cast<std::uint32_t>(error_code) & 0xFFU);

    if (!body.empty()) {
        std::copy(body.begin(), body.end(), packet.begin() + static_cast<std::ptrdiff_t>(kLengthHeaderSize + kFixedMetadataSize));
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
    if (payload.size() < kFixedMetadataSize) {
        throw std::invalid_argument("payload is smaller than fixed packet metadata");
    }

    const auto message_id = static_cast<std::uint16_t>(
        (static_cast<std::uint16_t>(static_cast<unsigned char>(payload[0])) << 8U) |
        static_cast<std::uint16_t>(static_cast<unsigned char>(payload[1])));
    const auto request_id =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[2])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[3])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[4])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[5]));
    const auto error_code_unsigned =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[6])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[7])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[8])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[9]));
    const auto error_code = static_cast<std::int32_t>(error_code_unsigned);

    return DecodedPacket{
        message_id,
        request_id,
        error_code,
        std::string(payload.begin() + static_cast<std::ptrdiff_t>(kFixedMetadataSize), payload.end())};
}

}  // namespace net::packet
