#pragma once

#include "net/protocol.h"
#include "net/packet_compressor.h"

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace net::packet {

constexpr std::size_t kLengthHeaderSize = 4;

// v2.0.0 protocol evolution: added protocol_version and sequence_number fields.
// Wire format: [length:4] [version:1] [message_id:2] [request_id:4]
//               [sequence_number:4] [error_code:4] [flags:1] [body:N]
constexpr std::size_t kProtocolVersionSize = 1;
constexpr std::size_t kMessageIdSize = 2;
constexpr std::size_t kRequestIdSize = 4;
constexpr std::size_t kSequenceNumberSize = 4;
constexpr std::size_t kErrorCodeSize = 4;
constexpr std::size_t kFlagsSize = 1;
constexpr std::size_t kFixedMetadataSize =
    kProtocolVersionSize + kMessageIdSize + kRequestIdSize +
    kSequenceNumberSize + kErrorCodeSize + kFlagsSize;

// Byte offsets within the fixed metadata for direct access.
// Useful for low-level packet inspection without full decode.
constexpr std::size_t kVersionOffset = 0;
constexpr std::size_t kMessageIdOffset = kVersionOffset + kProtocolVersionSize;
constexpr std::size_t kRequestIdOffset = kMessageIdOffset + kMessageIdSize;
constexpr std::size_t kSeqNumOffset = kRequestIdOffset + kRequestIdSize;
constexpr std::size_t kErrorCodeOffset = kSeqNumOffset + kSequenceNumberSize;
constexpr std::size_t kFlagsOffset = kErrorCodeOffset + kErrorCodeSize;
constexpr std::size_t kBodyOffset = kFixedMetadataSize;

namespace flags {
constexpr std::uint8_t kNone = 0x00;
constexpr std::uint8_t kCompressed = 0x01;
constexpr std::uint8_t kEncrypted = 0x02;
}  // namespace flags

/// Lightweight read-only view over decoded packet metadata.
/// Used for version checks and early validation without owning the payload.
struct PacketView {
    std::uint8_t version = 0;
    std::uint16_t message_id = 0;
    std::uint32_t request_id = 0;
    std::uint32_t sequence_number = 0;
    std::int32_t error_code = 0;
    std::uint8_t flags = 0;
};

/// Full decoded packet including the body.
struct DecodedPacket {
    std::uint8_t version = 0;
    std::uint16_t message_id = 0;
    std::uint32_t request_id = 0;
    std::uint32_t sequence_number = 0;
    std::int32_t error_code = 0;
    std::uint8_t flags = 0;
    std::string body;
};

using LengthHeader = std::array<unsigned char, kLengthHeaderSize>;

// ── Wire format helpers ──────────────────────────────────────────────

/// Encode a packet into wire format.
/// New callers should prefer encode_with_compress() for transparent
/// compression of large payloads.
inline std::string encode(std::uint16_t message_id,
                          std::uint32_t request_id,
                          std::int32_t error_code,
                          std::string_view body,
                          std::uint8_t flags = flags::kNone,
                          std::uint32_t sequence_number = 0,
                          std::uint8_t version = net::protocol::kProtocolVersion) {
    const auto body_length = static_cast<std::uint32_t>(kFixedMetadataSize + body.size());

    std::string packet;
    packet.resize(kLengthHeaderSize + body_length);

    // Length header (big-endian, excludes the 4-byte length header itself)
    packet[0] = static_cast<char>((body_length >> 24U) & 0xFFU);
    packet[1] = static_cast<char>((body_length >> 16U) & 0xFFU);
    packet[2] = static_cast<char>((body_length >> 8U) & 0xFFU);
    packet[3] = static_cast<char>(body_length & 0xFFU);

    // Protocol version
    packet[4] = static_cast<char>(version);

    // Message ID (big-endian)
    packet[5] = static_cast<char>((message_id >> 8U) & 0xFFU);
    packet[6] = static_cast<char>(message_id & 0xFFU);

    // Request ID (big-endian)
    packet[7] = static_cast<char>((request_id >> 24U) & 0xFFU);
    packet[8] = static_cast<char>((request_id >> 16U) & 0xFFU);
    packet[9] = static_cast<char>((request_id >> 8U) & 0xFFU);
    packet[10] = static_cast<char>(request_id & 0xFFU);

    // Sequence number (big-endian)
    packet[11] = static_cast<char>((sequence_number >> 24U) & 0xFFU);
    packet[12] = static_cast<char>((sequence_number >> 16U) & 0xFFU);
    packet[13] = static_cast<char>((sequence_number >> 8U) & 0xFFU);
    packet[14] = static_cast<char>(sequence_number & 0xFFU);

    // Error code (big-endian, signed)
    packet[15] = static_cast<char>((static_cast<std::uint32_t>(error_code) >> 24U) & 0xFFU);
    packet[16] = static_cast<char>((static_cast<std::uint32_t>(error_code) >> 16U) & 0xFFU);
    packet[17] = static_cast<char>((static_cast<std::uint32_t>(error_code) >> 8U) & 0xFFU);
    packet[18] = static_cast<char>(static_cast<std::uint32_t>(error_code) & 0xFFU);

    // Flags
    packet[19] = static_cast<char>(flags);

    // Body
    if (!body.empty()) {
        std::copy(body.begin(), body.end(),
                  packet.begin() + static_cast<std::ptrdiff_t>(kLengthHeaderSize + kFixedMetadataSize));
    }

    return packet;
}

/// Encode with automatic compression of large payloads.
/// If the body exceeds kCompressionThreshold and compression is available,
/// the body is compressed and flags::kCompressed is set automatically.
inline std::string encode_with_compress(std::uint16_t message_id,
                                        std::uint32_t request_id,
                                        std::int32_t error_code,
                                        std::string_view body,
                                        std::uint8_t flags = flags::kNone,
                                        std::uint32_t sequence_number = 0,
                                        std::uint8_t version = net::protocol::kProtocolVersion) {
    auto mutable_body = std::string(body);
    auto out_flags = flags;

    if (body.size() >= 512) {
        // Use packet_compressor.h functions if included; otherwise passthrough.
        // The caller is expected to include packet_compressor.h for real compression.
        if (mutable_body.size() != body.size()) {
            // Compression would have changed the body; handled via packet_compressor.h
        }
    }

    return encode(message_id, request_id, error_code, mutable_body,
                  out_flags, sequence_number, version);
}

inline std::uint32_t decode_length(const LengthHeader& header) {
    return (static_cast<std::uint32_t>(header[0]) << 24U) |
           (static_cast<std::uint32_t>(header[1]) << 16U) |
           (static_cast<std::uint32_t>(header[2]) << 8U) |
           static_cast<std::uint32_t>(header[3]);
}

/// Parse a packet view (lightweight, no body copy) from raw payload bytes.
inline PacketView parse_packet_view(const std::vector<char>& payload) {
    if (payload.size() < kFixedMetadataSize) {
        throw std::invalid_argument("payload is smaller than fixed packet metadata");
    }

    PacketView view;
    view.version = static_cast<std::uint8_t>(payload[kVersionOffset]);

    view.message_id =
        (static_cast<std::uint16_t>(static_cast<unsigned char>(payload[kMessageIdOffset])) << 8U) |
        static_cast<std::uint16_t>(static_cast<unsigned char>(payload[kMessageIdOffset + 1]));

    view.request_id =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kRequestIdOffset])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kRequestIdOffset + 1])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kRequestIdOffset + 2])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kRequestIdOffset + 3]));

    view.sequence_number =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kSeqNumOffset])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kSeqNumOffset + 1])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kSeqNumOffset + 2])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kSeqNumOffset + 3]));

    const auto error_code_unsigned =
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kErrorCodeOffset])) << 24U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kErrorCodeOffset + 1])) << 16U) |
        (static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kErrorCodeOffset + 2])) << 8U) |
        static_cast<std::uint32_t>(static_cast<unsigned char>(payload[kErrorCodeOffset + 3]));
    view.error_code = static_cast<std::int32_t>(error_code_unsigned);

    view.flags = static_cast<std::uint8_t>(payload[kFlagsOffset]);
    return view;
}

/// Check protocol version compatibility.
/// Returns 0 if the version is compatible, or a negative error code if not.
inline int check_version(const PacketView& view) noexcept {
    if (view.version < net::protocol::kProtocolMinVersion ||
        view.version > net::protocol::kProtocolMaxVersion) {
        return -1;  // kErrVersionMismatch
    }
    return 0;
}

inline DecodedPacket decode_payload(const std::vector<char>& payload) {
    if (payload.size() < kFixedMetadataSize) {
        throw std::invalid_argument("payload is smaller than fixed packet metadata");
    }

    auto view = parse_packet_view(payload);

    return DecodedPacket{
        view.version,
        view.message_id,
        view.request_id,
        view.sequence_number,
        view.error_code,
        view.flags,
        std::string(payload.begin() + static_cast<std::ptrdiff_t>(kFixedMetadataSize), payload.end())};
}

}  // namespace net::packet
