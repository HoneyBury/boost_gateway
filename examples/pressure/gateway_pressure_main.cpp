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
#include <functional>
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
    config.messages_per_client =
        static_cast<std::size_t>(argc > 4 ? std::strtoull(argv[4], nullptr, 10) : config.messages_per_client);

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

    void on_client_done(std::size_t completed_messages) {
        completed_clients_.fetch_add(1, std::memory_order_relaxed);
        completed_packets_.fetch_add(completed_messages, std::memory_order_relaxed);
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
               std::size_t client_index,
               std::shared_ptr<std::atomic<std::size_t>> room_done_counter = nullptr)
        : resolver_(io_context),
          socket_(io_context),
          send_timer_(io_context),
          controller_(std::move(controller)),
          config_(std::move(config)),
          client_index_(client_index),
          user_id_("load_user_" + std::to_string(client_index)),
          room_done_counter_(std::move(room_done_counter)) {}

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
    // -----------------------------------------------------------------------
    // Connection
    // -----------------------------------------------------------------------
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

                if (self->config_.scenario == app::config::PressureScenario::kMaliciousPacket) {
                    self->send_oversized_packet();
                    return;
                }

                const auto token =
                    self->should_use_invalid_token() ? "invalid_token" : "token:" + self->user_id_;
                self->send_packet(net::protocol::kLoginRequest, self->user_id_ + "|" + token);
            });
    }

    // -----------------------------------------------------------------------
    // Packet I/O
    // -----------------------------------------------------------------------
    void send_packet(std::uint16_t message_id, const std::string& body, std::int32_t ec_code = 0) {
        outbound_packet_ = net::packet::encode(message_id, next_request_id_++, ec_code, body);
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
                    if (self->expecting_disconnect_) {
                        self->finish_malicious_success();
                    } else {
                        LOG_ERROR("Read header failed for {}: {}", self->user_id_, ec.message());
                        self->fail();
                    }
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

    // -----------------------------------------------------------------------
    // Packet dispatch
    // -----------------------------------------------------------------------
    void handle_packet(const net::packet::DecodedPacket& packet) {
        using enum app::config::PressureScenario;

        // --- shared error handling ---
        if (packet.message_id == net::protocol::kErrorResponse) {
            if (packet.error_code == static_cast<std::int32_t>(net::protocol::ErrorCode::kInvalidToken) &&
                should_use_invalid_token()) {
                finish_rejected();
                return;
            }
            LOG_ERROR("Unexpected error for {}: code={}", user_id_, packet.error_code);
            fail();
            return;
        }

        // --- common push messages ---
        if (packet.message_id == net::protocol::kRoomStatePush) {
            ++received_room_pushes_;
            return;
        }
        if (packet.message_id == net::protocol::kBattleInputPush) {
            ++received_battle_inputs_;
            return;
        }

        switch (config_.scenario) {
        case kEcho:
        case kInvalidToken:
        case kSlowEcho:
        case kChaos:
        case kStability:
            handle_echo_state(packet);
            break;
        case kBroadcastStorm:
            handle_broadcast_storm_state(packet);
            break;
        case kBattleBroadcast:
            handle_battle_broadcast_state(packet);
            break;
        case kMaliciousPacket:
            // already handled — we close after sending oversized
            break;
        }
    }

    // -----------------------------------------------------------------------
    // Echo / slow_echo / invalid_token
    // -----------------------------------------------------------------------
    void handle_echo_state(const net::packet::DecodedPacket& packet) {
        if (packet.message_id == net::protocol::kLoginResponse) {
            if (should_use_invalid_token()) {
                LOG_ERROR("Unexpected login success for invalid token client {}", user_id_);
                fail();
                return;
            }

            if (config_.messages_per_client == 0) {
                finish();
                return;
            }

            schedule_next_message();
            return;
        }

        if (packet.message_id == net::protocol::kEchoResponse) {
            ++completed_messages_;
            if (completed_messages_ >= config_.messages_per_client) {
                finish();
                return;
            }

            schedule_next_message();
            return;
        }

        LOG_ERROR("Unexpected message {} for {} body={}", packet.message_id, user_id_, packet.body);
        fail();
    }

    // -----------------------------------------------------------------------
    // broadcast_storm
    //   All N clients login, join the same room, then continuously send echo
    //   requests. The server processes them concurrently under room load.
    // -----------------------------------------------------------------------
    void handle_broadcast_storm_state(const net::packet::DecodedPacket& packet) {
        if (packet.message_id == net::protocol::kLoginResponse) {
            schedule_room_join();
            return;
        }

        if (packet.message_id == net::protocol::kRoomCreateResponse ||
            packet.message_id == net::protocol::kRoomJoinResponse) {
            in_room_ = true;
            if (config_.messages_per_client == 0) {
                finish();
                return;
            }
            schedule_next_message();
            return;
        }

        if (packet.message_id == net::protocol::kEchoResponse) {
            ++completed_messages_;
            if (completed_messages_ >= config_.messages_per_client) {
                finish();
                return;
            }
            schedule_next_message();
            return;
        }

        LOG_ERROR("Unexpected message {} for {} body={}", packet.message_id, user_id_, packet.body);
        fail();
    }

    void schedule_room_join() {
        if (client_index_ == 0) {
            send_packet(net::protocol::kRoomCreateRequest, config_.room_name);
        } else {
            // Stagger join to avoid racing before room is created
            const auto stagger_ms = 20 * static_cast<int>(client_index_);
            schedule_delayed(std::chrono::milliseconds(stagger_ms), [this]() {
                send_packet(net::protocol::kRoomJoinRequest, config_.room_name);
            });
        }
    }

    // -----------------------------------------------------------------------
    // battle_broadcast
    //   Client 0 creates room, both join, ready up, client 0 starts battle,
    //   then both send battle inputs. Tests battle input fan-out under load.
    // -----------------------------------------------------------------------
    void handle_battle_broadcast_state(const net::packet::DecodedPacket& packet) {
        if (packet.message_id == net::protocol::kLoginResponse) {
            if (client_index_ == 0) {
                send_packet(net::protocol::kRoomCreateRequest, config_.room_name);
            } else {
                schedule_delayed(std::chrono::milliseconds(50), [this]() {
                    send_packet(net::protocol::kRoomJoinRequest, config_.room_name);
                });
            }
            return;
        }

        if (packet.message_id == net::protocol::kRoomCreateResponse ||
            packet.message_id == net::protocol::kRoomJoinResponse) {
            in_room_ = true;
            send_packet(net::protocol::kRoomReadyRequest, "ready");
            return;
        }

        if (packet.message_id == net::protocol::kRoomReadyResponse) {
            if (client_index_ == 0) {
                schedule_delayed(std::chrono::milliseconds(80), [this]() {
                    send_packet(net::protocol::kBattleStartRequest, config_.room_name);
                });
            } else {
                // Non-owner waits for server push indicating battle started
                wait_for_message();
            }
            return;
        }

        if (packet.message_id == net::protocol::kBattleStartResponse ||
            packet.message_id == net::protocol::kBattleStatePush) {
            if (in_battle_) return;
            in_battle_ = true;
            if (config_.messages_per_client > 0) {
                schedule_next_message();
            } else {
                finish();
            }
            return;
        }

        if (packet.message_id == net::protocol::kBattleInputResponse) {
            ++completed_messages_;
            if (completed_messages_ >= config_.messages_per_client) {
                finish();
                return;
            }
            schedule_next_message();
            return;
        }

        LOG_ERROR("Unexpected message {} for {} body={}", packet.message_id, user_id_, packet.body);
        fail();
    }

    // -----------------------------------------------------------------------
    // malicious_packet
    // -----------------------------------------------------------------------
    void send_oversized_packet() {
        // Build a length header claiming a huge body, followed by truncated data
        const auto claimed_size = config_.malicious_packet_size;
        std::string oversized(claimed_size + sizeof(net::packet::LengthHeader), '\xAB');
        // Write a valid-ish looking length prefix (server reads 4-byte LE length)
        oversized[0] = static_cast<char>(claimed_size & 0xFF);
        oversized[1] = static_cast<char>((claimed_size >> 8) & 0xFF);
        oversized[2] = static_cast<char>((claimed_size >> 16) & 0xFF);
        oversized[3] = static_cast<char>((claimed_size >> 24) & 0xFF);

        expecting_disconnect_ = true;

        auto self = shared_from_this();
        asio::async_write(
            socket_,
            asio::buffer(oversized),
            [self](const error_code& ec, std::size_t /*bytes*/) {
                if (ec) {
                    // Server already closed — expected
                    self->finish_malicious_success();
                    return;
                }
                // Sent successfully — now wait for server to close us
                self->read_header();
            });
    }

    void finish_malicious_success() {
        error_code ignored_ec;
        send_timer_.cancel();
        socket_.close(ignored_ec);
        controller_->on_client_done(1);
    }

    // -----------------------------------------------------------------------
    // Async helpers
    // -----------------------------------------------------------------------
    void wait_for_message() {
        auto self = shared_from_this();
        asio::async_read(
            socket_,
            asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t /*bytes*/) {
                if (ec) {
                    if (self->expecting_disconnect_) {
                        self->finish_malicious_success();
                    } else {
                        LOG_ERROR("Wait read failed for {}: {}", self->user_id_, ec.message());
                        self->fail();
                    }
                    return;
                }
                self->expected_body_length_ = net::packet::decode_length(self->read_header_);
                self->read_body_.assign(self->expected_body_length_, '\0');
                self->read_body();
            });
    }

    // -----------------------------------------------------------------------
    // Timer-driven message loop
    // -----------------------------------------------------------------------
    void schedule_delayed(std::chrono::milliseconds delay, std::function<void()> action) {
        auto self = shared_from_this();
        send_timer_.expires_after(delay);
        send_timer_.async_wait([self, action = std::move(action)](const error_code& ec) {
            if (ec == asio::error::operation_aborted) {
                return;
            }
            if (ec) {
                LOG_ERROR("Delayed timer failed for {}: {}", self->user_id_, ec.message());
                self->fail();
                return;
            }
            action();
        });
    }

    void schedule_next_message() {
        if (config_.send_interval.count() <= 0) {
            send_next_message();
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

            self->send_next_message();
        });
    }

    void send_next_message() {
        using enum app::config::PressureScenario;

        switch (config_.scenario) {
        case kEcho:
        case kInvalidToken:
        case kSlowEcho:
            send_packet(net::protocol::kEchoRequest,
                        "load_msg:" + user_id_ + ":" + std::to_string(completed_messages_));
            break;
        case kBroadcastStorm:
            send_packet(net::protocol::kEchoRequest,
                        "storm:" + user_id_ + ":" + std::to_string(completed_messages_));
            break;
        case kBattleBroadcast:
            send_packet(net::protocol::kBattleInputRequest,
                        "input:" + user_id_ + ":" + std::to_string(completed_messages_));
            break;
        case kChaos:
        case kStability:
            send_packet(net::protocol::kEchoRequest,
                        "load_msg:" + user_id_ + ":" + std::to_string(completed_messages_));
            break;
        case kMaliciousPacket:
            break;
        }

        // Chaos: randomly disconnect after some messages
        if (config_.scenario == app::config::PressureScenario::kChaos &&
            completed_messages_ > 0 && (client_index_ % 10 == completed_messages_ % 10)) {
            LOG_INFO("Chaos disconnect for {}", user_id_);
            fail();
        }
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------
    bool should_use_invalid_token() const {
        return config_.scenario == app::config::PressureScenario::kInvalidToken &&
               config_.invalid_token_every > 0 &&
               (client_index_ % config_.invalid_token_every == 0);
    }

    void finish() {
        error_code ignored_ec;
        send_timer_.cancel();

        // Room-based scenarios: client 0 stays connected until all members finish
        const bool is_room_scenario = config_.scenario == app::config::PressureScenario::kBroadcastStorm ||
                                      config_.scenario == app::config::PressureScenario::kBattleBroadcast;
        if (is_room_scenario && room_done_counter_) {
            const auto done = room_done_counter_->fetch_add(1, std::memory_order_relaxed) + 1;
            if (client_index_ == 0 && done < config_.client_count) {
                arm_room_wait_timer();
                return;
            }
        }

        socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        socket_.close(ignored_ec);
        controller_->on_client_done(completed_messages_);
    }

    void arm_room_wait_timer() {
        auto self = shared_from_this();
        send_timer_.expires_after(std::chrono::milliseconds(50));
        send_timer_.async_wait([self](const error_code& ec) {
            if (ec == asio::error::operation_aborted) return;
            const auto done = self->room_done_counter_->load(std::memory_order_relaxed);
            if (done >= self->config_.client_count) {
                error_code ignored;
                self->socket_.shutdown(tcp::socket::shutdown_both, ignored);
                self->socket_.close(ignored);
                self->controller_->on_client_done(self->completed_messages_);
                return;
            }
            self->arm_room_wait_timer();
        });
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
    std::size_t completed_messages_ = 0;
    std::uint32_t next_request_id_ = 1;
    std::string outbound_packet_;
    net::packet::LengthHeader read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;

    // scenario-specific state
    bool in_room_ = false;
    bool in_battle_ = false;
    bool expecting_disconnect_ = false;
    std::size_t ready_count_ = 0;
    std::size_t received_room_pushes_ = 0;
    std::size_t received_battle_inputs_ = 0;
    std::shared_ptr<std::atomic<std::size_t>> room_done_counter_;
};

int main(int argc, char* argv[]) {
    app::logging::init("gateway_pressure");

    const auto config = load_runtime_config(argc, argv);
    LOG_INFO("Pressure scenario={} host={} port={} clients={} msgs_per_client={} interval_ms={}",
             app::config::to_string(config.scenario),
             config.host,
             config.port,
             config.client_count,
             config.messages_per_client,
             config.send_interval.count());

    asio::io_context io_context;
    auto controller = std::make_shared<LoadController>(io_context, config.client_count);

    std::vector<std::shared_ptr<LoadClient>> clients;
    clients.reserve(config.client_count);

    auto room_done_counter = std::make_shared<std::atomic<std::size_t>>(0);

    const auto started_at = std::chrono::steady_clock::now();
    for (std::size_t i = 0; i < config.client_count; ++i) {
        auto client = std::make_shared<LoadClient>(io_context, controller, config, i, room_done_counter);
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
    LOG_INFO("Pressure finished: scenario={} clients_ok={} clients_rejected={} clients_failed={} msgs={} elapsed_ms={}",
             app::config::to_string(config.scenario),
             controller->completed_clients(),
             controller->rejected_clients(),
             controller->failed_clients(),
             controller->completed_packets(),
             elapsed.count());
    return controller->failed_clients() == 0 ? 0 : 1;
}
