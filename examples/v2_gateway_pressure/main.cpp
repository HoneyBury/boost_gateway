#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/benchmark/latency_histogram.h"
#include "v2/benchmark/throughput_tracker.h"

#include <boost/asio.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <functional>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;
using boost::system::error_code;
using tcp = asio::ip::tcp;

namespace {

// ---------------------------------------------------------------------------
// Scenario enum
// ---------------------------------------------------------------------------

enum class BenchScenario { kEcho, kBattle, kStability };

std::string to_string(BenchScenario s) {
    switch (s) {
    case BenchScenario::kEcho:      return "echo";
    case BenchScenario::kBattle:    return "battle";
    case BenchScenario::kStability: return "stability";
    }
    return "unknown";
}

std::optional<BenchScenario> parse_scenario(const std::string& s) {
    if (s == "echo")      return BenchScenario::kEcho;
    if (s == "battle")    return BenchScenario::kBattle;
    if (s == "stability") return BenchScenario::kStability;
    return std::nullopt;
}

// ---------------------------------------------------------------------------
// Runtime config
// ---------------------------------------------------------------------------

struct BenchConfig {
    std::string host = "127.0.0.1";
    std::uint16_t port = 9201;
    BenchScenario scenario = BenchScenario::kEcho;
    std::size_t client_count = 100;
    std::size_t messages_per_client = 0;  // 0 = unlimited (use duration)
    std::chrono::seconds duration{30};
    std::chrono::milliseconds send_interval{0};
    std::string room_name = "bench_room";
};

BenchConfig parse_args(int argc, char* argv[]) {
    BenchConfig cfg;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--host" && i + 1 < argc)          cfg.host = argv[++i];
        else if (arg == "--port" && i + 1 < argc)     cfg.port = static_cast<std::uint16_t>(std::atoi(argv[++i]));
        else if (arg == "--scenario" && i + 1 < argc) {
            if (auto s = parse_scenario(argv[++i])) cfg.scenario = *s;
        }
        else if (arg == "--clients" && i + 1 < argc)  cfg.client_count = std::strtoull(argv[++i], nullptr, 10);
        else if (arg == "--duration" && i + 1 < argc) cfg.duration = std::chrono::seconds(std::atoi(argv[++i]));
        else if (arg == "--messages" && i + 1 < argc) cfg.messages_per_client = std::strtoull(argv[++i], nullptr, 10);
        else if (arg == "--interval" && i + 1 < argc) cfg.send_interval = std::chrono::milliseconds(std::atoi(argv[++i]));
        else if (arg == "--room" && i + 1 < argc)     cfg.room_name = argv[++i];
    }
    return cfg;
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

struct BenchResult {
    BenchScenario scenario;
    std::size_t target_clients = 0;
    std::size_t connected_clients = 0;
    std::size_t failed_clients = 0;
    std::size_t rejected_clients = 0;
    std::uint64_t total_messages = 0;
    double elapsed_seconds = 0.0;
    double throughput_msg_per_sec = 0.0;
    double latency_p50_ms = 0.0;
    double latency_p90_ms = 0.0;
    double latency_p99_ms = 0.0;
    double latency_min_ms = 0.0;
    double latency_max_ms = 0.0;
};

// ---------------------------------------------------------------------------
// LoadController
// ---------------------------------------------------------------------------

class LoadController : public std::enable_shared_from_this<LoadController> {
public:
    LoadController(asio::io_context& io, std::size_t client_count, BenchScenario scenario)
        : io_context_(io), client_count_(client_count), scenario_(scenario) {}

    void on_client_done(std::size_t msgs) {
        completed_clients_.fetch_add(1, std::memory_order_relaxed);
        completed_packets_.fetch_add(msgs, std::memory_order_relaxed);
        try_stop();
    }

    void on_client_rejected() {
        rejected_clients_.fetch_add(1, std::memory_order_relaxed);
        try_stop();
    }

    void on_client_failed() {
        failed_clients_.fetch_add(1, std::memory_order_relaxed);
        try_stop();
    }

    void on_time_expired() {
        bool expected = false;
        if (time_expired_.compare_exchange_strong(expected, true, std::memory_order_relaxed)) {
            io_context_.stop();
        }
    }

    [[nodiscard]] std::size_t completed_clients() const { return completed_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t rejected_clients()  const { return rejected_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t failed_clients()    const { return failed_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t completed_packets() const { return completed_packets_.load(std::memory_order_relaxed); }
    [[nodiscard]] BenchScenario scenario()        const { return scenario_; }

private:
    void try_stop() {
        const auto done = completed_clients_.load(std::memory_order_relaxed) +
                          rejected_clients_.load(std::memory_order_relaxed) +
                          failed_clients_.load(std::memory_order_relaxed);
        if (done >= client_count_) {
            bool expected = false;
            if (finished_.compare_exchange_strong(expected, true, std::memory_order_relaxed)) {
                io_context_.stop();
            }
        }
    }

    asio::io_context& io_context_;
    std::size_t client_count_ = 0;
    BenchScenario scenario_;
    std::atomic<std::size_t> completed_clients_{0};
    std::atomic<std::size_t> rejected_clients_{0};
    std::atomic<std::size_t> failed_clients_{0};
    std::atomic<std::size_t> completed_packets_{0};
    std::atomic<bool> finished_{false};
    std::atomic<bool> time_expired_{false};
};

// ---------------------------------------------------------------------------
// LoadClient
// ---------------------------------------------------------------------------

class LoadClient : public std::enable_shared_from_this<LoadClient> {
public:
    LoadClient(asio::io_context& io,
               std::shared_ptr<LoadController> ctl,
               BenchConfig cfg,
               std::size_t idx,
               v2::benchmark::ThroughputTracker* throughput,
               std::shared_ptr<std::atomic<std::size_t>> room_done = nullptr)
        : resolver_(io), socket_(io), send_timer_(io),
          controller_(std::move(ctl)), config_(std::move(cfg)),
          client_index_(idx),
          user_id_("bench_user_" + std::to_string(idx)),
          throughput_(throughput),
          room_done_counter_(std::move(room_done)) {}

    void start() {
        auto self = shared_from_this();
        resolver_.async_resolve(
            config_.host, std::to_string(config_.port),
            [self](const error_code& ec, const tcp::resolver::results_type& eps) {
                if (ec) { self->fail(); return; }
                self->do_connect(eps);
            });
    }

    [[nodiscard]] const v2::benchmark::LatencyHistogram& histogram() const { return histogram_; }

private:
    void do_connect(const tcp::resolver::results_type& eps) {
        auto self = shared_from_this();
        asio::async_connect(socket_, eps, [self](const error_code& ec, const tcp::endpoint&) {
            if (ec) { self->fail(); return; }
            self->send_login();
        });
    }

    void send_login() {
        send_packet(net::protocol::kLoginRequest,
                    user_id_ + "|token:" + user_id_ + "|" + user_id_);
    }

    void send_packet(std::uint16_t msg_id, const std::string& body, std::int32_t ec = 0) {
        outbound_packet_ = net::packet::encode(msg_id, next_request_id_++, ec, body);
        if (config_.scenario == BenchScenario::kEcho) {
            send_timestamp_ = std::chrono::steady_clock::now();
        }
        auto self = shared_from_this();
        asio::async_write(socket_, asio::buffer(outbound_packet_),
            [self](const error_code& ec, std::size_t) {
                if (ec) { self->fail(); return; }
                self->read_header();
            });
    }

    void read_header() {
        auto self = shared_from_this();
        asio::async_read(socket_, asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t) {
                if (ec) { self->fail(); return; }
                self->expected_body_length_ = net::packet::decode_length(self->read_header_);
                self->read_body_.assign(self->expected_body_length_, '\0');
                self->read_body();
            });
    }

    void read_body() {
        auto self = shared_from_this();
        asio::async_read(socket_, asio::buffer(read_body_),
            [self](const error_code& ec, std::size_t) {
                if (ec) { self->fail(); return; }
                auto packet = net::packet::decode_payload(self->read_body_);
                self->dispatch(packet);
            });
    }

    void dispatch(const net::packet::DecodedPacket& pkt) {
        if (pkt.message_id == net::protocol::kErrorResponse) {
            finish_rejected();
            return;
        }

        switch (config_.scenario) {
        case BenchScenario::kEcho:
        case BenchScenario::kStability:
            handle_echo_flow(pkt);
            break;
        case BenchScenario::kBattle:
            handle_battle_flow(pkt);
            break;
        }
    }

    // -------------------------------------------------------------------
    // Echo / Stability flow
    // -------------------------------------------------------------------

    void handle_echo_flow(const net::packet::DecodedPacket& pkt) {
        if (pkt.message_id == net::protocol::kLoginResponse) {
            if (config_.messages_per_client == 0 && config_.duration.count() == 0) {
                finish();
                return;
            }
            schedule_next();
            return;
        }

        if (pkt.message_id == net::protocol::kEchoResponse) {
            ++completed_messages_;
            throughput_->record();

            auto now = std::chrono::steady_clock::now();
            auto us = std::chrono::duration_cast<std::chrono::microseconds>(now - send_timestamp_).count();
            histogram_.record_us(static_cast<std::uint64_t>(us));

            if (config_.messages_per_client > 0 &&
                completed_messages_ >= config_.messages_per_client) {
                finish();
                return;
            }
            schedule_next();
            return;
        }
    }

    // -------------------------------------------------------------------
    // Battle flow: login → join room → ready → exchange inputs
    // -------------------------------------------------------------------

    void handle_battle_flow(const net::packet::DecodedPacket& pkt) {
        if (pkt.message_id == net::protocol::kLoginResponse) {
            if (client_index_ == 0) {
                send_packet(net::protocol::kRoomCreateRequest, config_.room_name);
            } else {
                schedule_delayed(std::chrono::milliseconds(20 * client_index_), [this]() {
                    send_packet(net::protocol::kRoomJoinRequest, config_.room_name);
                });
            }
            return;
        }

        if (pkt.message_id == net::protocol::kRoomCreateResponse ||
            pkt.message_id == net::protocol::kRoomJoinResponse) {
            in_room_ = true;
            schedule_delayed(std::chrono::milliseconds(50), [this]() {
                send_packet(net::protocol::kRoomReadyRequest, "true");
            });
            return;
        }

        if (pkt.message_id == net::protocol::kRoomReadyResponse) {
            ++ready_count_;
            return;
        }

        if (pkt.message_id == net::protocol::kBattleStartResponse) {
            in_battle_ = true;
            if (config_.messages_per_client == 0 && config_.duration.count() == 0) {
                finish();
                return;
            }
            schedule_next();
            return;
        }

        // Battle push (input broadcast)
        if (pkt.message_id == net::protocol::kBattleInputPush) {
            ++received_battle_inputs_;
            throughput_->record();
            return;
        }

        // Battle input response
        if (pkt.message_id == net::protocol::kBattleInputResponse) {
            ++completed_messages_;
            throughput_->record();

            auto now = std::chrono::steady_clock::now();
            auto us = std::chrono::duration_cast<std::chrono::microseconds>(now - send_timestamp_).count();
            histogram_.record_us(static_cast<std::uint64_t>(us));

            if (config_.messages_per_client > 0 &&
                completed_messages_ >= config_.messages_per_client) {
                finish();
                return;
            }
            schedule_next();
            return;
        }
    }

    // -------------------------------------------------------------------
    // Scheduling
    // -------------------------------------------------------------------

    void schedule_next() {
        if (config_.send_interval.count() > 0) {
            auto self = shared_from_this();
            send_timer_.expires_after(config_.send_interval);
            send_timer_.async_wait([self](const error_code& ec) {
                if (ec) return;
                self->send_next_message();
            });
        } else {
            send_next_message();
        }
    }

    void send_next_message() {
        if (config_.scenario == BenchScenario::kBattle && in_battle_) {
            send_timestamp_ = std::chrono::steady_clock::now();
            send_packet(net::protocol::kBattleInputRequest,
                        "move:" + std::to_string(client_index_) + "," +
                        std::to_string(completed_messages_));
        } else {
            send_timestamp_ = std::chrono::steady_clock::now();
            send_packet(net::protocol::kEchoRequest, "bench_echo_" + std::to_string(completed_messages_));
        }
    }

    void schedule_delayed(std::chrono::milliseconds delay, std::function<void()> fn) {
        auto self = shared_from_this();
        send_timer_.expires_after(delay);
        send_timer_.async_wait([self, fn = std::move(fn)](const error_code& ec) {
            if (ec) return;
            fn();
        });
    }

    // -------------------------------------------------------------------
    // Termination
    // -------------------------------------------------------------------

    void finish() {
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.shutdown(tcp::socket::shutdown_both, ignored);
        socket_.close(ignored);
        controller_->on_client_done(completed_messages_);
    }

    void finish_rejected() {
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.close(ignored);
        controller_->on_client_rejected();
    }

    void fail() {
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.close(ignored);
        controller_->on_client_failed();
    }

    // -------------------------------------------------------------------
    // Members
    // -------------------------------------------------------------------

    tcp::resolver resolver_;
    tcp::socket socket_;
    asio::steady_timer send_timer_;
    std::shared_ptr<LoadController> controller_;
    BenchConfig config_;
    std::size_t client_index_ = 0;
    std::string user_id_;
    std::size_t completed_messages_ = 0;
    std::uint32_t next_request_id_ = 1;
    std::string outbound_packet_;
    net::packet::LengthHeader read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
    std::chrono::steady_clock::time_point send_timestamp_;
    v2::benchmark::LatencyHistogram histogram_;
    v2::benchmark::ThroughputTracker* throughput_;

    // scenario state
    bool in_room_ = false;
    bool in_battle_ = false;
    std::size_t ready_count_ = 0;
    std::size_t received_battle_inputs_ = 0;
    std::shared_ptr<std::atomic<std::size_t>> room_done_counter_;
};

// ---------------------------------------------------------------------------
// JSON output
// ---------------------------------------------------------------------------

nlohmann::json to_json(const BenchResult& r) {
    return {
        {"scenario", to_string(r.scenario)},
        {"target_clients", r.target_clients},
        {"connected_clients", r.connected_clients},
        {"failed_clients", r.failed_clients},
        {"rejected_clients", r.rejected_clients},
        {"total_messages", r.total_messages},
        {"elapsed_seconds", r.elapsed_seconds},
        {"throughput_msg_per_sec", r.throughput_msg_per_sec},
        {"latency_p50_ms", r.latency_p50_ms},
        {"latency_p90_ms", r.latency_p90_ms},
        {"latency_p99_ms", r.latency_p99_ms},
        {"latency_min_ms", r.latency_min_ms},
        {"latency_max_ms", r.latency_max_ms},
    };
}

}  // namespace

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    app::logging::init("v2_gateway_pressure");

    const auto config = parse_args(argc, argv);
    LOG_INFO("v2_gateway_pressure: scenario={} host={} port={} clients={} msgs={} duration={}s",
             to_string(config.scenario), config.host, config.port,
             config.client_count, config.messages_per_client, config.duration.count());

    asio::io_context io;
    auto controller = std::make_shared<LoadController>(io, config.client_count, config.scenario);
    v2::benchmark::ThroughputTracker throughput(5, 10);

    std::vector<std::shared_ptr<LoadClient>> clients;
    clients.reserve(config.client_count);

    auto room_done_counter = std::make_shared<std::atomic<std::size_t>>(0);

    const auto started_at = std::chrono::steady_clock::now();

    // Duration-based timer
    asio::steady_timer duration_timer(io);
    if (config.messages_per_client == 0 && config.duration.count() > 0) {
        duration_timer.expires_after(config.duration);
        duration_timer.async_wait([controller](const error_code& ec) {
            if (!ec) controller->on_time_expired();
        });
    }

    for (std::size_t i = 0; i < config.client_count; ++i) {
        auto client = std::make_shared<LoadClient>(
            io, controller, config, i, &throughput, room_done_counter);
        clients.push_back(client);
        client->start();
    }

    const auto thread_count = std::max(2u, std::thread::hardware_concurrency());
    std::vector<std::thread> workers;
    workers.reserve(thread_count);
    for (unsigned int i = 0; i < thread_count; ++i) {
        workers.emplace_back([&io]() { io.run(); });
    }
    for (auto& w : workers) w.join();

    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started_at);

    // Aggregate results
    BenchResult result;
    result.scenario = config.scenario;
    result.target_clients = config.client_count;
    result.connected_clients = controller->completed_clients();
    result.failed_clients = controller->failed_clients();
    result.rejected_clients = controller->rejected_clients();
    result.total_messages = controller->completed_packets();
    result.elapsed_seconds = static_cast<double>(elapsed.count()) / 1'000'000.0;
    result.throughput_msg_per_sec = result.elapsed_seconds > 0.0
        ? static_cast<double>(result.total_messages) / result.elapsed_seconds
        : 0.0;

    // Merge per-client histograms
    v2::benchmark::LatencyHistogram merged;
    for (const auto& c : clients) {
        const auto snap = c->histogram().snapshot();
        for (std::size_t i = 0; i < snap.bucket_counts.size(); ++i) {
            for (std::size_t j = 0; j < snap.bucket_counts[i]; ++j) {
                double upper = 0.0;
                if (i < v2::benchmark::kLatencyBucketBoundariesMs.size()) {
                    upper = v2::benchmark::kLatencyBucketBoundariesMs[i];
                } else {
                    upper = v2::benchmark::kLatencyBucketBoundariesMs.back() * 2.0;
                }
                double lower = (i == 0) ? 0.0
                    : v2::benchmark::kLatencyBucketBoundariesMs[i - 1];
                double mid = (lower + upper) / 2.0;
                merged.record_ms(mid);
            }
        }
    }

    const auto lat_snap = merged.snapshot();
    result.latency_p50_ms = lat_snap.p50_ms;
    result.latency_p90_ms = lat_snap.p90_ms;
    result.latency_p99_ms = lat_snap.p99_ms;
    result.latency_min_ms = lat_snap.min_ms;
    result.latency_max_ms = lat_snap.max_ms;

    // Output JSON
    LOG_INFO("v2_gateway_pressure done: connected={} failed={} rejected={} msgs={} "
             "elapsed={:.3f}s throughput={:.1f}/s p50={:.3f}ms p99={:.3f}ms",
             result.connected_clients, result.failed_clients, result.rejected_clients,
             result.total_messages, result.elapsed_seconds, result.throughput_msg_per_sec,
             result.latency_p50_ms, result.latency_p99_ms);

    fmt::print("{}\n", to_json(result).dump());

    return result.failed_clients > 0 ? 1 : 0;
}
