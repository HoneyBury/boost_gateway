#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;
using boost::system::error_code;
using tcp = asio::ip::tcp;

class LoadController : public std::enable_shared_from_this<LoadController> {
public:
    LoadController(asio::io_context& io_context, std::size_t client_count)
        : io_context_(io_context), client_count_(client_count) {}

    void on_client_done(std::size_t completed_packets) {
        completed_clients_.fetch_add(1, std::memory_order_relaxed);
        completed_packets_.fetch_add(completed_packets, std::memory_order_relaxed);
        try_finish();
    }

    void on_client_failed() {
        failed_clients_.fetch_add(1, std::memory_order_relaxed);
        try_finish();
    }

    [[nodiscard]] bool finished() const {
        return finished_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] std::size_t completed_clients() const {
        return completed_clients_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] std::size_t failed_clients() const {
        return failed_clients_.load(std::memory_order_relaxed);
    }

    [[nodiscard]] std::size_t completed_packets() const {
        return completed_packets_.load(std::memory_order_relaxed);
    }

private:
    void try_finish() {
        const auto done = completed_clients_.load(std::memory_order_relaxed) +
                          failed_clients_.load(std::memory_order_relaxed);
        if (done != client_count_) {
            return;
        }

        bool expected = false;
        if (finished_.compare_exchange_strong(expected, true, std::memory_order_relaxed)) {
            io_context_.stop();
        }
    }

    asio::io_context& io_context_;
    std::size_t client_count_ = 0;
    std::atomic<std::size_t> completed_clients_{0};
    std::atomic<std::size_t> failed_clients_{0};
    std::atomic<std::size_t> completed_packets_{0};
    std::atomic<bool> finished_{false};
};

class LoadClient : public std::enable_shared_from_this<LoadClient> {
public:
    LoadClient(asio::io_context& io_context,
               std::shared_ptr<LoadController> controller,
               std::string host,
               std::string port,
               std::string user_id,
               std::size_t total_echo_requests)
        : resolver_(io_context),
          socket_(io_context),
          controller_(std::move(controller)),
          host_(std::move(host)),
          port_(std::move(port)),
          user_id_(std::move(user_id)),
          total_echo_requests_(total_echo_requests) {}

    void start() {
        auto self = shared_from_this();
        resolver_.async_resolve(
            host_,
            port_,
            [self](const error_code& ec, const tcp::resolver::results_type& endpoints) {
                if (ec) {
                    LOG_ERROR("Resolve failed for {}: {}", self->user_id_, ec.message());
                    self->fail();
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
            [self](const error_code& ec, const tcp::endpoint&) {
                if (ec) {
                    LOG_ERROR("Connect failed for {}: {}", self->user_id_, ec.message());
                    self->fail();
                    return;
                }

                self->send_packet(net::protocol::kLoginRequest, self->user_id_);
            });
    }

    void send_packet(std::uint16_t message_id, const std::string& body) {
        outbound_packet_ = net::packet::encode(message_id, next_request_id_++, 0, body);
        auto self = shared_from_this();
        asio::async_write(
            socket_,
            asio::buffer(outbound_packet_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Write failed for {}: {}", self->user_id_, ec.message());
                    self->fail();
                    return;
                }

                self->read_header();
            });
    }

    void read_header() {
        auto self = shared_from_this();
        asio::async_read(
            socket_,
            asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Read header failed for {}: {}", self->user_id_, ec.message());
                    self->fail();
                    return;
                }

                self->expected_body_length_ = net::packet::decode_length(self->read_header_);
                self->read_body_.assign(self->expected_body_length_, '\0');
                self->read_body();
            });
    }

    void read_body() {
        auto self = shared_from_this();
        asio::async_read(
            socket_,
            asio::buffer(read_body_),
            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                if (ec) {
                    LOG_ERROR("Read body failed for {}: {}", self->user_id_, ec.message());
                    self->fail();
                    return;
                }

                const auto packet = net::packet::decode_payload(self->read_body_);
                self->handle_packet(packet.message_id, packet.body);
            });
    }

    void handle_packet(std::uint16_t message_id, const std::string& body) {
        if (message_id == net::protocol::kLoginResponse) {
            if (body.rfind("login_ok:", 0) != 0) {
                LOG_ERROR("Unexpected login response for {}: {}", user_id_, body);
                fail();
                return;
            }
            send_next_echo();
            return;
        }

        if (message_id == net::protocol::kEchoResponse) {
            ++completed_echo_requests_;
            if (completed_echo_requests_ >= total_echo_requests_) {
                finish();
                return;
            }

            send_next_echo();
            return;
        }

        LOG_ERROR("Unexpected message {} for {} with body {}", message_id, user_id_, body);
        fail();
    }

    void send_next_echo() {
        const auto echo_index = completed_echo_requests_;
        send_packet(net::protocol::kEchoRequest,
                    "load_message:" + user_id_ + ":" + std::to_string(echo_index));
    }

    void finish() {
        error_code ignored_ec;
        socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        socket_.close(ignored_ec);
        controller_->on_client_done(completed_echo_requests_);
    }

    void fail() {
        error_code ignored_ec;
        socket_.close(ignored_ec);
        controller_->on_client_failed();
    }

    tcp::resolver resolver_;
    tcp::socket socket_;
    std::shared_ptr<LoadController> controller_;
    std::string host_;
    std::string port_;
    std::string user_id_;
    std::size_t total_echo_requests_ = 0;
    std::size_t completed_echo_requests_ = 0;
    std::uint32_t next_request_id_ = 1;
    std::string outbound_packet_;
    net::packet::LengthHeader read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
};

int main(int argc, char* argv[]) {
    app::logging::init("gateway_pressure");

    const std::string host = argc > 1 ? argv[1] : "127.0.0.1";
    const std::string port = argc > 2 ? argv[2] : "9000";
    const auto client_count = static_cast<std::size_t>(argc > 3 ? std::strtoull(argv[3], nullptr, 10) : 100);
    const auto echo_count_per_client =
        static_cast<std::size_t>(argc > 4 ? std::strtoull(argv[4], nullptr, 10) : 10);

    asio::io_context io_context;
    auto controller = std::make_shared<LoadController>(io_context, client_count);

    std::vector<std::shared_ptr<LoadClient>> clients;
    clients.reserve(client_count);

    const auto started_at = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < client_count; ++i) {
        auto client = std::make_shared<LoadClient>(io_context,
                                                   controller,
                                                   host,
                                                   port,
                                                   "load_user_" + std::to_string(i),
                                                   echo_count_per_client);
        clients.push_back(client);
        client->start();
    }

    const auto thread_count = std::max(2u, std::thread::hardware_concurrency());
    std::vector<std::thread> workers;
    workers.reserve(thread_count);
    for (unsigned int i = 0; i < thread_count; ++i) {
        workers.emplace_back([&io_context]() { io_context.run(); });
    }

    for (auto& worker : workers) {
        worker.join();
    }

    const auto elapsed =
        std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - started_at);
    LOG_INFO("Pressure finished: clients_ok={}, clients_failed={}, echo_packets={}, elapsed_ms={}",
             controller->completed_clients(),
             controller->failed_clients(),
             controller->completed_packets(),
             elapsed.count());
    return controller->failed_clients() == 0 ? 0 : 1;
}
