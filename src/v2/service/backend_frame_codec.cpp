#include "v2/service/backend_frame_codec.h"

#include <boost/asio/read.hpp>
#include <boost/asio/write.hpp>

#include <array>
#include <chrono>
#include <cstring>

#ifdef __APPLE__
#include <sys/select.h>
#endif

namespace v2::service {

namespace asio = boost::asio;

namespace {

std::array<std::byte, kFrameLengthHeaderSize> encode_length(std::uint32_t length) {
    std::array<std::byte, kFrameLengthHeaderSize> header{};
    header[0] = static_cast<std::byte>(length & 0xFF);
    header[1] = static_cast<std::byte>((length >> 8) & 0xFF);
    header[2] = static_cast<std::byte>((length >> 16) & 0xFF);
    header[3] = static_cast<std::byte>((length >> 24) & 0xFF);
    return header;
}

std::uint32_t decode_length(const std::array<std::byte, kFrameLengthHeaderSize>& header) {
    return (static_cast<std::uint32_t>(header[0])) |
           (static_cast<std::uint32_t>(header[1]) << 8) |
           (static_cast<std::uint32_t>(header[2]) << 16) |
           (static_cast<std::uint32_t>(header[3]) << 24);
}

bool wait_for_data(int fd, std::chrono::milliseconds timeout) {
    fd_set read_fds;
    FD_ZERO(&read_fds);
    FD_SET(fd, &read_fds);

    const auto secs = std::chrono::duration_cast<std::chrono::seconds>(timeout);
    const auto usecs = std::chrono::duration_cast<std::chrono::microseconds>(timeout - secs);

    struct timeval tv{};
    tv.tv_sec = static_cast<long>(secs.count());
    tv.tv_usec = static_cast<long>(usecs.count());

    return ::select(fd + 1, &read_fds, nullptr, nullptr, &tv) > 0;
}

}  // namespace

std::string encode_frame(const BackendEnvelope& envelope) {
    const auto json = to_json(envelope);
    const auto length = static_cast<std::uint32_t>(json.size());
    const auto header = encode_length(length);

    std::string frame;
    frame.reserve(kFrameLengthHeaderSize + json.size());
    frame.append(reinterpret_cast<const char*>(header.data()), kFrameLengthHeaderSize);
    frame.append(json);
    return frame;
}

std::optional<BackendEnvelope> decode_frame(std::string_view json_bytes) {
    return from_json(json_bytes);
}

std::optional<BackendEnvelope> read_frame(asio::ip::tcp::socket& socket,
                                          std::chrono::milliseconds timeout) {
    const int fd = socket.native_handle();

    if (!wait_for_data(fd, timeout)) return std::nullopt;

    boost::system::error_code ec;

    std::array<std::byte, kFrameLengthHeaderSize> header{};
    asio::read(socket, asio::buffer(header.data(), kFrameLengthHeaderSize),
               asio::transfer_exactly(kFrameLengthHeaderSize), ec);
    if (ec) return std::nullopt;

    const auto payload_length = decode_length(header);
    if (payload_length == 0 || payload_length > 1024 * 1024) return std::nullopt;

    if (!wait_for_data(fd, timeout)) return std::nullopt;

    std::string json_bytes(payload_length, '\0');
    asio::read(socket, asio::buffer(json_bytes.data(), payload_length),
               asio::transfer_exactly(payload_length), ec);
    if (ec) return std::nullopt;

    return from_json(json_bytes);
}

bool write_frame(asio::ip::tcp::socket& socket, const BackendEnvelope& envelope) {
    const auto frame = encode_frame(envelope);
    boost::system::error_code ec;
    asio::write(socket, asio::buffer(frame.data(), frame.size()),
                asio::transfer_exactly(frame.size()), ec);
    return !ec;
}

// ── SSL stream overloads ──────────────────────────────────────────────

std::optional<BackendEnvelope> read_frame(
    asio::ssl::stream<asio::ip::tcp::socket&>& stream,
    std::chrono::milliseconds timeout) {
    auto& socket = stream.next_layer();
    const int fd = socket.native_handle();

    if (!wait_for_data(fd, timeout)) return std::nullopt;

    boost::system::error_code ec;

    std::array<std::byte, kFrameLengthHeaderSize> header{};
    asio::read(stream, asio::buffer(header.data(), kFrameLengthHeaderSize),
               asio::transfer_exactly(kFrameLengthHeaderSize), ec);
    if (ec) return std::nullopt;

    const auto payload_length = decode_length(header);
    if (payload_length == 0 || payload_length > 1024 * 1024) return std::nullopt;

    if (!wait_for_data(fd, timeout)) return std::nullopt;

    std::string json_bytes(payload_length, '\0');
    asio::read(stream, asio::buffer(json_bytes.data(), payload_length),
               asio::transfer_exactly(payload_length), ec);
    if (ec) return std::nullopt;

    return from_json(json_bytes);
}

bool write_frame(asio::ssl::stream<asio::ip::tcp::socket&>& stream,
                 const BackendEnvelope& envelope) {
    const auto frame = encode_frame(envelope);
    boost::system::error_code ec;
    asio::write(stream, asio::buffer(frame.data(), frame.size()),
                asio::transfer_exactly(frame.size()), ec);
    return !ec;
}

}  // namespace v2::service
