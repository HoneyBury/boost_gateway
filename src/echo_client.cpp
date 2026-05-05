#include "app/logging.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <string>
#include <vector>

namespace asio = boost::asio;
using boost::system::error_code;
using tcp = asio::ip::tcp;

class EchoClient : public std::enable_shared_from_this<EchoClient> {
public:
    EchoClient(asio::io_context& io_context,
               std::string host,
               std::string port,
               std::string message)
        : resolver_(io_context),
          socket_(io_context),
          host_(std::move(host)),
          port_(std::move(port)),
          // 客户端保存的是纯业务消息体，发包时再自动加长度头和消息号。
          message_(std::move(message)) {}

    void start() {
        auto self = shared_from_this();  // 保持客户端对象存活，直到异步解析完成。

        // async_resolve 会把主机名和端口解析成可连接的 TCP endpoint 列表。
        resolver_.async_resolve(
            host_,
            port_,
            [self](const error_code& ec, const tcp::resolver::results_type& endpoints) {
                if (ec) {
                    LOG_ERROR("Resolve failed: {}", ec.message());
                    return;
                }

                self->do_connect(endpoints);
            });
    }

private:
    void do_connect(const tcp::resolver::results_type& endpoints) {
        auto self = shared_from_this();  // 保持客户端对象存活，直到异步连接完成。

        // async_connect 会依次尝试这些 endpoint，直到其中一个连接成功。
        asio::async_connect(
            socket_,
            endpoints,
            [self](const error_code& ec, const tcp::endpoint& endpoint) {
                if (ec) {
                    LOG_ERROR("Connect failed: {}", ec.message());
                    return;
                }

                LOG_INFO("Connected to {}:{}", endpoint.address().to_string(), endpoint.port());
                self->do_write();
            });
    }

    void do_write() {
        auto self = shared_from_this();  // 保持客户端对象存活，直到发送完成。
        LOG_INFO("Sending echo request: {}", message_);

        outbound_packet_ = encode_packet(net::protocol::kEchoRequest, message_);

        // async_write 会把完整逻辑包一次性交给内核发送。
        asio::async_write(
            socket_,
            asio::buffer(outbound_packet_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Write failed: {}", ec.message());
                    return;
                }

                self->do_read_header();
            });
    }

    void do_read_header() {
        auto self = shared_from_this();  // 保持客户端对象存活，直到包头读取结束。

        // 先完整读取 4 字节长度头。
        asio::async_read(
            socket_,
            asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Read header failed: {}", ec.message());
                    return;
                }

                self->expected_body_length_ = decode_length(self->read_header_);
                self->read_body_.assign(self->expected_body_length_, '\0');
                self->do_read_body();
            });
    }

    void do_read_body() {
        auto self = shared_from_this();  // 保持客户端对象存活，直到完整包体到达。

        // 再按包头长度完整读取消息体，确保客户端拿到的是一个完整响应包。
        asio::async_read(
            socket_,
            asio::buffer(read_body_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Read body failed: {}", ec.message());
                    return;
                }

                const auto message_id = decode_message_id(self->read_body_);
                std::string body(self->read_body_.begin() + 2, self->read_body_.end());

                LOG_INFO("Received message {} with body: {}", message_id, body);

                error_code ignored_ec;
                self->socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
                self->socket_.close(ignored_ec);
            });
    }

    static std::string encode_packet(std::uint16_t message_id, const std::string& body) {
        const auto body_length = static_cast<std::uint32_t>(2 + body.size());

        std::string packet;
        packet.resize(4 + body_length);

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

    static std::uint32_t decode_length(const std::array<unsigned char, 4>& header) {
        return (static_cast<std::uint32_t>(header[0]) << 24U) |
               (static_cast<std::uint32_t>(header[1]) << 16U) |
               (static_cast<std::uint32_t>(header[2]) << 8U) |
               static_cast<std::uint32_t>(header[3]);
    }

    static std::uint16_t decode_message_id(const std::vector<char>& body) {
        return (static_cast<std::uint16_t>(static_cast<unsigned char>(body[0])) << 8U) |
               static_cast<std::uint16_t>(static_cast<unsigned char>(body[1]));
    }

    tcp::resolver resolver_;
    tcp::socket socket_;
    std::string host_;
    std::string port_;
    std::string message_;
    std::string outbound_packet_;
    std::array<unsigned char, 4> read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
};

int main(int argc, char* argv[]) {
    app::logging::init("echo_client");  // 在启动异步网络逻辑前初始化日志。

    const std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    const std::string port = argc > 2 ? argv[2] : "9000";
    const std::string message = argc > 3 ? argv[3] : "hello from client";

    asio::io_context io_context;  // 客户端事件循环，负责驱动所有异步操作。
    auto client = std::make_shared<EchoClient>(io_context, host, port, message);
    client->start();

    // run() 会阻塞主线程，直到客户端相关的异步操作全部执行完成。
    io_context.run();
    return 0;
}
