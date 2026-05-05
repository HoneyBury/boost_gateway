#include "app/config.h"
#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;
using boost::system::error_code;
using tcp = asio::ip::tcp;

namespace {

bool is_numeric_arg(const char* value) {
    if (value == nullptr || *value == '\0') {
        return false;
    }

    for (const auto* current = value; *current != '\0'; ++current) {
        if (!std::isdigit(static_cast<unsigned char>(*current))) {
            return false;
        }
    }

    return true;
}

app::config::PressureAppConfig load_runtime_config(int argc, char* argv[]) {
    if (argc > 1) {
        const std::filesystem::path first_arg = argv[1];
        if (first_arg.extension() == ".json" || first_arg.extension() == ".conf") {
            auto config = app::config::load_pressure_config(first_arg);
            if (argc > 2) {
                config.port = static_cast<std::uint16_t>(std::atoi(argv[2]));
            }
            return config;
        }
    }

    app::config::PressureAppConfig config;
    config.host = argc > 1 ? argv[1] : config.host;
    config.port = static_cast<std::uint16_t>(argc > 2 ? std::atoi(argv[2]) : config.port);
    config.client_count = static_cast<std::size_t>(argc > 3 ? std::strtoull(argv[3], nullptr, 10) : config.client_count);
    config.echo_count_per_client =
        static_cast<std::size_t>(argc > 4 ? std::strtoull(argv[4], nullptr, 10) : config.echo_count_per_client);

    if (argc > 5) {
        if (const auto scenario = app::config::parse_pressure_scenario(argv[5])) {
            config.scenario = *scenario;
        }
    }

    if (argc > 6 && is_numeric_arg(argv[6])) {
        config.send_interval = std::chrono::milliseconds(std::strtoull(argv[6], nullptr, 10));
    }

    return config;
}

}  // namespace

class LoadController : public std::enable_shared_from_this<LoadController> {
public:
    LoadController(asio::io_context& io_context, std::size_t client_count)
        : io_context_(io_context), client_count_(client_count) {}

    void on_client_done(std::size_t completed_packets) {
        completed_clients_.fetch_add(1, std::memory_order_relaxed);
        completed_packets_.fetch_add(completed_packets, std::memory_order_relaxed);
        try_finish();
    }

    void on_client_rejected() {
        rejected_clients_.fetch_add(1, std::memory_order_relaxed);
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

    [[nodiscard]] std::size_t rejected_clients() const {
        return rejected_clients_.load(std::memory_order_relaxed);
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
                          rejected_clients_.load(std::memory_order_relaxed) +
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
    std::atomic<std::size_t> rejected_clients_{0};
    std::atomic<std::size_t> failed_clients_{0};
    std::atomic<std::size_t> completed_packets_{0};
    std::atomic<bool> finished_{false};
};

class LoadClient : public std::enable_shared_from_this<LoadClient> {
public:
    LoadClient(asio::io_context& io_context,
               std::shared_ptr<LoadController> controller,
               app::config::PressureAppConfig config,
               std::size_t client_index)
        : resolver_(io_context),
          socket_(io_context),
          send_timer_(io_context),
          controller_(std::move(controller)),
          config_(std::move(config)),
          client_index_(client_index),
          user_id_("load_user_" + std::to_string(client_index)) {}

    void start() {
        auto self = shared_from_this();
        resolver_.async_resolve(
            config_.host,
            std::to_string(config_.port),
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
    bool should_use_invalid_token() const {
        return config_.scenario == app::config::PressureScenario::kInvalidToken &&
               config_.invalid_token_every > 0 &&
               (client_index_ % config_.invalid_token_every == 0);
    }

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

                const auto token =
                    self->should_use_invalid_token() ? "invalid_token" : "token:" + self->user_id_;
                self->send_packet(net::protocol::kLoginRequest, self->user_id_ + "|" + token);
            });
    }

    void schedule_next_echo() {
        if (config_.send_interval.count() <= 0) {
            send_next_echo();
            return;
        }

        auto self = shared_from_this();
        send_timer_.expires_after(config_.send_interval);
        send_timer_.async_wait([self](const error_code& ec) {
            if (ec == asio::error::operation_aborted) {
                return;
            }
            if (ec) {
                LOG_ERROR("Send timer failed for {}: {}", self->user_id_, ec.message());
                self->fail();
                return;
            }

            self->send_next_echo();
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
                self->handle_packet(packet);
            });
    }

    void handle_packet(const net::packet::DecodedPacket& packet) {
        if (packet.message_id == net::protocol::kErrorResponse &&
            packet.error_code == static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken) &&
            should_use_invalid_token()) {
            finish_rejected();
            return;
        }

        if (packet.message_id == net::protocol::kLoginResponse) {
            if (should_use_invalid_token()) {
                LOG_ERROR("Unexpected login success for invalid token client {}", user_id_);
                fail();
                return;
            }

            if (config_.echo_count_per_client == 0) {
                finish();
                return;
            }

            schedule_next_echo();
            return;
        }

        if (packet.message_id == net::protocol::kEchoResponse) {
            ++completed_echo_requests_;
            if (completed_echo_requests_ >= config_.echo_count_per_client) {
                finish();
                return;
            }

            schedule_next_echo();
            return;
        }

        LOG_ERROR("Unexpected message {} for {} with body {}",
                  packet.message_id,
                  user_id_,
                  packet.body);
        fail();
    }

    void send_next_echo() {
        const auto echo_index = completed_echo_requests_;
        send_packet(net::protocol::kEchoRequest,
                    "load_message:" + user_id_ + ":" + std::to_string(echo_index));
    }

    void finish() {
        error_code ignored_ec;
        send_timer_.cancel();
        socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        socket_.close(ignored_ec);
        controller_->on_client_done(completed_echo_requests_);
    }

    void finish_rejected() {
        error_code ignored_ec;
        send_timer_.cancel();
        socket_.close(ignored_ec);
        controller_->on_client_rejected();
    }

    void fail() {
        error_code ignored_ec;
        send_timer_.cancel();
        socket_.close(ignored_ec);
        controller_->on_client_failed();
    }

    tcp::resolver resolver_;
    tcp::socket socket_;
    asio::steady_timer send_timer_;
    std::shared_ptr<LoadController> controller_;
    app::config::PressureAppConfig config_;
    std::size_t client_index_ = 0;
    std::string user_id_;
    std::size_t completed_echo_requests_ = 0;
    std::uint32_t next_request_id_ = 1;
    std::string outbound_packet_;
    net::packet::LengthHeader read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
};

int main(int argc, char* argv[]) {
    app::logging::init("gateway_pressure");

    const auto config = load_runtime_config(argc, argv);
    LOG_INFO("Pressure scenario={}, host={}, port={}, clients={}, echo_count={}, interval_ms={}, invalid_token_every={}",
             app::config::to_string(config.scenario),
             config.host,
             config.port,
             config.client_count,
             config.echo_count_per_client,
             config.send_interval.count(),
             config.invalid_token_every);

    asio::io_context io_context;
    auto controller = std::make_shared<LoadController>(io_context, config.client_count);

    std::vector<std::shared_ptr<LoadClient>> clients;
    clients.reserve(config.client_count);

    const auto started_at = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < config.client_count; ++i) {
        auto client = std::make_shared<LoadClient>(io_context, controller, config, i);
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
    LOG_INFO("Pressure finished: clients_ok={}, clients_rejected={}, clients_failed={}, echo_packets={}, elapsed_ms={}",
             controller->completed_clients(),
             controller->rejected_clients(),
             controller->failed_clients(),
             controller->completed_packets(),
             elapsed.count());
    return controller->failed_clients() == 0 ? 0 : 1;
}
