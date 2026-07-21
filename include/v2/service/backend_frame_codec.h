#pragma once

#include "v2/service/backend_envelope.h"

#include <boost/asio.hpp>
#include <boost/asio/ssl.hpp>

#include <chrono>
#include <cstddef>
#include <functional>
#include <optional>
#include <string>
#include <string_view>

namespace v2::service {

inline constexpr std::size_t kFrameLengthHeaderSize = 4;
using FrameOperationCancelled = std::function<bool()>;

[[nodiscard]] std::string encode_frame(const BackendEnvelope& envelope);

[[nodiscard]] std::optional<BackendEnvelope> decode_frame(std::string_view json_bytes);

[[nodiscard]] std::optional<BackendEnvelope> read_frame(
    boost::asio::ip::tcp::socket& socket,
    std::chrono::milliseconds timeout = std::chrono::milliseconds(5000),
    FrameOperationCancelled cancelled = {});

// Plain and TLS operations use bounded non-blocking loops so the owning
// session thread can observe server cancellation without cross-thread close.
[[nodiscard]] std::optional<BackendEnvelope> read_frame(
    boost::asio::ssl::stream<boost::asio::ip::tcp::socket&>& stream,
    std::chrono::milliseconds timeout = std::chrono::milliseconds(5000),
    FrameOperationCancelled cancelled = {});

bool write_frame(boost::asio::ip::tcp::socket& socket,
                 const BackendEnvelope& envelope,
                 std::chrono::milliseconds timeout = std::chrono::milliseconds(5000),
                 FrameOperationCancelled cancelled = {});

// v3.0.0: Write a frame through an SSL stream.
bool write_frame(boost::asio::ssl::stream<boost::asio::ip::tcp::socket&>& stream,
                 const BackendEnvelope& envelope,
                 std::chrono::milliseconds timeout = std::chrono::milliseconds(5000),
                 FrameOperationCancelled cancelled = {});

}  // namespace v2::service
