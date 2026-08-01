#pragma once
// SDK v4.2.1: Standalone packet codec — zero server dependencies.
// Encodes/decodes the BoostGateway binary protocol:
// [4B total(BE)][1B version][2B msg_id(BE)][4B req_id(BE)]
// [4B seq_num(BE)][4B err_code(BE)][1B flags][NB body]

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace boost_gateway {
namespace sdk {
namespace protocol {

constexpr std::size_t kLengthHeaderSize = 4;

// v2.0.0 wire format with version and sequence number
constexpr std::size_t kProtocolVersionSize = 1;
constexpr std::size_t kMessageIdSize = 2;
constexpr std::size_t kRequestIdSize = 4;
constexpr std::size_t kSequenceNumberSize = 4;
constexpr std::size_t kErrorCodeSize = 4;
constexpr std::size_t kFlagsSize = 1;
constexpr std::size_t kFixedMetadataSize = 16;  // 1+2+4+4+4+1

constexpr std::uint8_t kProtocolVersion = 1;

using LengthHeader = std::array<unsigned char, kLengthHeaderSize>;

struct DecodedPacket {
    std::uint8_t version = 0;
    std::uint16_t message_id = 0;
    std::uint32_t request_id = 0;
    std::uint32_t sequence_number = 0;
    std::int32_t error_code = 0;
    std::uint8_t flags = 0;
    std::string body;
};

// ── Encode ──────────────────────────────────────────────────────────────

inline std::string encode(std::uint16_t message_id,
                          std::uint32_t request_id,
                          std::int32_t error_code,
                          std::string_view body,
                          std::uint8_t flags = 0,
                          std::uint32_t sequence_number = 0,
                          std::uint8_t version = kProtocolVersion) {
    const auto body_length =
        static_cast<std::uint32_t>(kFixedMetadataSize + body.size());
    std::string packet;
    packet.resize(kLengthHeaderSize + body_length);

    packet[0] = static_cast<char>((body_length >> 24U) & 0xFFU);
    packet[1] = static_cast<char>((body_length >> 16U) & 0xFFU);
    packet[2] = static_cast<char>((body_length >> 8U) & 0xFFU);
    packet[3] = static_cast<char>(body_length & 0xFFU);

    packet[4] = static_cast<char>(version);

    packet[5] = static_cast<char>((message_id >> 8U) & 0xFFU);
    packet[6] = static_cast<char>(message_id & 0xFFU);

    packet[7] = static_cast<char>((request_id >> 24U) & 0xFFU);
    packet[8] = static_cast<char>((request_id >> 16U) & 0xFFU);
    packet[9] = static_cast<char>((request_id >> 8U) & 0xFFU);
    packet[10] = static_cast<char>(request_id & 0xFFU);

    packet[11] = static_cast<char>((sequence_number >> 24U) & 0xFFU);
    packet[12] = static_cast<char>((sequence_number >> 16U) & 0xFFU);
    packet[13] = static_cast<char>((sequence_number >> 8U) & 0xFFU);
    packet[14] = static_cast<char>(sequence_number & 0xFFU);

    packet[15] = static_cast<char>(
        (static_cast<std::uint32_t>(error_code) >> 24U) & 0xFFU);
    packet[16] = static_cast<char>(
        (static_cast<std::uint32_t>(error_code) >> 16U) & 0xFFU);
    packet[17] = static_cast<char>(
        (static_cast<std::uint32_t>(error_code) >> 8U) & 0xFFU);
    packet[18] = static_cast<char>(
        static_cast<std::uint32_t>(error_code) & 0xFFU);
    packet[19] = static_cast<char>(flags);

    if (!body.empty()) {
        std::copy(body.begin(), body.end(),
                  packet.begin() + kLengthHeaderSize + kFixedMetadataSize);
    }
    return packet;
}

// ── Decode ──────────────────────────────────────────────────────────────

inline std::uint32_t decode_length(const LengthHeader& header) {
    return (static_cast<std::uint32_t>(header[0]) << 24U) |
           (static_cast<std::uint32_t>(header[1]) << 16U) |
           (static_cast<std::uint32_t>(header[2]) << 8U) |
           static_cast<std::uint32_t>(header[3]);
}

inline DecodedPacket decode_payload(const std::vector<char>& payload) {
    DecodedPacket p;
    if (payload.size() < kFixedMetadataSize) return p;

    p.version = static_cast<std::uint8_t>(payload[0]);

    p.message_id = static_cast<std::uint16_t>(
        (static_cast<unsigned char>(payload[1]) << 8U) |
        static_cast<unsigned char>(payload[2]));

    p.request_id =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[3])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[4])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[5])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[6]));

    p.sequence_number =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[7])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[8])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[9])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[10]));

    p.error_code = static_cast<std::int32_t>(
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[11])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[12])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[13])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[14])));

    p.flags = static_cast<std::uint8_t>(payload[15]);

    if (payload.size() > kFixedMetadataSize) {
        p.body.assign(payload.begin() + kFixedMetadataSize, payload.end());
    }
    return p;
}

}  // namespace protocol
}  // namespace sdk
}  // namespace boost_gateway
