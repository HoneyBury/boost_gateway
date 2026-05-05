#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

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
          message_(std::move(message)) {}

    void start() {
        auto self = shared_from_this();

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
        auto self = shared_from_this();

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
        auto self = shared_from_this();
        LOG_INFO("Sending echo request: {}", message_);

        outbound_packet_ = net::packet::encode(net::protocol::kEchoRequest, message_);

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
        auto self = shared_from_this();

        asio::async_read(
            socket_,
            asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Read header failed: {}", ec.message());
                    return;
                }

                self->expected_body_length_ = net::packet::decode_length(self->read_header_);
                self->read_body_.assign(self->expected_body_length_, '\0');
                self->do_read_body();
            });
    }

    void do_read_body() {
        auto self = shared_from_this();

        asio::async_read(
            socket_,
            asio::buffer(read_body_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Read body failed: {}", ec.message());
                    return;
                }

                auto packet = net::packet::decode_payload(self->read_body_);
                LOG_INFO("Received message {} with body: {}", packet.message_id, packet.body);

                error_code ignored_ec;
                self->socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
                self->socket_.close(ignored_ec);
            });
    }

    tcp::resolver resolver_;
    tcp::socket socket_;
    std::string host_;
    std::string port_;
    std::string message_;
    std::string outbound_packet_;
    net::packet::LengthHeader read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
};

int main(int argc, char* argv[]) {
    app::logging::init("echo_client");

    const std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    const std::string port = argc > 2 ? argv[2] : "9000";
    const std::string message = argc > 3 ? argv[3] : "hello from client";

    asio::io_context io_context;
    auto client = std::make_shared<EchoClient>(io_context, host, port, message);
    client->start();
    io_context.run();
    return 0;
}
