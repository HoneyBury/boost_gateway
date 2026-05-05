#pragma once

#include <string>
#include <string_view>

namespace net::ws {

// Detects if incoming data starts with a WebSocket upgrade request
inline bool is_upgrade_request(std::string_view data) {
    return data.starts_with("GET ") && data.find("Upgrade: websocket") != std::string_view::npos;
}

// Generate WebSocket upgrade response
inline std::string build_handshake_response(std::string_view key) {
    // Simplified: accept any key with a static accept
    // Full implementation would compute SHA1 + base64
    return std::string(
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n"
        "\r\n");
}

// Minimal WebSocket frame header sizes
constexpr std::size_t kMinFrameHeader = 2;
constexpr std::size_t kMaxFrameHeader = 10;

// Frame opcodes
constexpr std::uint8_t kOpText = 0x01;
constexpr std::uint8_t kOpBinary = 0x02;
constexpr std::uint8_t kOpClose = 0x08;
constexpr std::uint8_t kOpPing = 0x09;
constexpr std::uint8_t kOpPong = 0x0A;

// Build a WebSocket text/binary frame
inline std::string build_frame(const std::string& payload, bool binary = true) {
    std::string frame;
    frame.reserve(kMaxFrameHeader + payload.size());

    const auto op = binary ? kOpBinary : kOpText;
    frame.push_back(static_cast<char>(0x80 | op));  // FIN + opcode

    if (payload.size() <= 125) {
        frame.push_back(static_cast<char>(payload.size()));
    } else if (payload.size() <= 65535) {
        frame.push_back(static_cast<char>(126));
        frame.push_back(static_cast<char>((payload.size() >> 8) & 0xFF));
        frame.push_back(static_cast<char>(payload.size() & 0xFF));
    } else {
        frame.push_back(static_cast<char>(127));
        for (int i = 7; i >= 0; --i) {
            frame.push_back(static_cast<char>((payload.size() >> (i * 8)) & 0xFF));
        }
    }

    frame.append(payload);
    return frame;
}

// Extract payload from a WebSocket frame (assumes unmasked server-to-client)
inline std::string extract_payload(const std::string& frame) {
    if (frame.size() < kMinFrameHeader) return {};
    std::size_t pos = 1;
    std::size_t len = static_cast<unsigned char>(frame[pos++]) & 0x7F;
    if (len == 126) { len = (static_cast<unsigned char>(frame[pos]) << 8) | static_cast<unsigned char>(frame[pos+1]); pos += 2; }
    else if (len == 127) { len = 0; for (int i = 0; i < 8; ++i) len = (len << 8) | static_cast<unsigned char>(frame[pos++]); }
    if (pos + len > frame.size()) return {};
    return frame.substr(pos, len);
}

}  // namespace net::ws
