// BoostGateway SDK: TCP connection wrapper with packet framing.

#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <chrono>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

namespace boost_gateway {
namespace sdk {
namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

}  // namespace

// ── TcpConnection ─────────────────────────────────────────────────────

class TcpConnection {
public:
    TcpConnection() : socket_(io_context_) {}

    bool connect(const std::string& host, std::uint16_t port,
                 std::chrono::milliseconds timeout) {
        boost::system::error_code ec;
        auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            socket_.connect(tcp::endpoint(asio::ip::make_address(host), port), ec);
            if (!ec) {
                // Start io_context in background thread for async reads
                io_thread_ = std::thread([this]() { io_context_.run(); });
                return true;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        return false;
    }

    void disconnect() {
        boost::system::error_code ec;
        socket_.shutdown(tcp::socket::shutdown_both, ec);
        socket_.close(ec);
        io_context_.stop();
        if (io_thread_.joinable()) io_thread_.join();
    }

    bool is_connected() const { return socket_.is_open(); }

    bool send(std::uint16_t msg_id, std::uint32_t request_id,
              const std::string& body) {
        auto encoded = net::packet::encode(msg_id, request_id, 0, body, 0);
        boost::system::error_code ec;
        asio::write(socket_, asio::buffer(encoded), ec);
        return !ec;
    }

    net::packet::DecodedPacket read(std::chrono::milliseconds timeout) {
        // Read 4-byte length prefix
        net::packet::LengthHeader header{};
        boost::system::error_code ec;
        asio::read(socket_, asio::buffer(header.data(), header.size()), ec);
        if (ec) return {};

        std::uint32_t total_len = net::packet::decode_length(header);
        std::vector<char> payload(total_len);
        asio::read(socket_, asio::buffer(payload.data(), payload.size()), ec);
        if (ec) return {};

        return net::packet::decode_payload(payload);
    }

    // Non-blocking check for readability
    bool has_data() {
        boost::system::error_code ec;
        socket_.available(ec);
        return !ec && socket_.available() > 0;
    }

private:
    boost::asio::io_context io_context_;
    tcp::socket socket_;
    std::thread io_thread_;
};

}  // namespace sdk
}  // namespace boost_gateway
