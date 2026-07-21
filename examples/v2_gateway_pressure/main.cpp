// windows.h NOMINMAX guard — must precede any include that pulls in windows.h
#include "v2/platform/highres_timer.h"

#include "load_evidence.h"

#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/benchmark/latency_histogram.h"
#include "v2/benchmark/throughput_tracker.h"
#include "final_message_counts.h"
#include "v2/gateway/battle_wire_parser.h"

#include <boost/asio.hpp>

#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
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
    std::size_t room_group_size = 2;
    std::size_t messages_per_client = 0;  // 0 = unlimited (use duration)
    std::chrono::seconds duration{30};
    std::chrono::milliseconds send_interval{0};
    std::string room_name = "bench_room";
    std::string output_path;
    unsigned int io_threads = 0;
    std::size_t ramp_clients_per_second = 200;
    std::chrono::seconds ramp_timeout{60};
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
        else if (arg == "--room-group-size" && i + 1 < argc) {
            cfg.room_group_size = std::max<std::size_t>(2, std::strtoull(argv[++i], nullptr, 10));
        }
        else if (arg == "--duration" && i + 1 < argc) cfg.duration = std::chrono::seconds(std::atoi(argv[++i]));
        else if (arg == "--messages" && i + 1 < argc) cfg.messages_per_client = std::strtoull(argv[++i], nullptr, 10);
        else if (arg == "--interval" && i + 1 < argc) cfg.send_interval = std::chrono::milliseconds(std::atoi(argv[++i]));
        else if (arg == "--room" && i + 1 < argc)     cfg.room_name = argv[++i];
        else if (arg == "--output" && i + 1 < argc)   cfg.output_path = argv[++i];
        else if (arg == "--io-threads" && i + 1 < argc) {
            cfg.io_threads = static_cast<unsigned int>(std::max(1, std::atoi(argv[++i])));
        }
        else if (arg == "--ramp-clients-per-second" && i + 1 < argc) {
            cfg.ramp_clients_per_second = std::max<std::size_t>(
                1, std::strtoull(argv[++i], nullptr, 10));
        }
        else if (arg == "--ramp-timeout" && i + 1 < argc) {
            cfg.ramp_timeout = std::chrono::seconds(std::max(1, std::atoi(argv[++i])));
        }
    }
    return cfg;
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

struct BenchResult {
    BenchScenario scenario;
    std::size_t target_clients = 0;
    std::size_t started_clients = 0;
    std::size_t tcp_connected_clients = 0;
    std::size_t authenticated_clients = 0;
    std::size_t active_clients = 0;
    std::size_t peak_active_clients = 0;
    std::size_t cancelled_clients = 0;
    std::size_t cancelled_before_connect = 0;
    std::uint64_t business_send_attempts = 0;
    std::uint64_t business_send_successes = 0;
    std::size_t connected_clients = 0;
    std::size_t failed_clients = 0;
    std::size_t rejected_clients = 0;
    std::uint64_t total_messages = 0;
    std::uint64_t response_messages = 0;
    std::uint64_t push_messages = 0;
    double elapsed_seconds = 0.0;
    double total_elapsed_seconds = 0.0;
    double ramp_up_seconds = 0.0;
    double ramp_timeout_seconds = 0.0;
    bool ramp_completed = false;
    bool measurement_started = false;
    double steady_state_target_seconds = 0.0;
    double steady_state_elapsed_seconds = 0.0;
    bool steady_state_completed = false;
    std::string termination_reason;
    std::string load_model = "closed_loop_one_in_flight_per_client";
    double configured_request_rate_ceiling_ops_per_sec = 0.0;
    bool configured_request_rate_is_bounded = false;
    double achieved_send_rate_ops_per_sec = 0.0;
    double achieved_response_rate_ops_per_sec = 0.0;
    double throughput_msg_per_sec = 0.0;
    double latency_p50_ms = 0.0;
    double latency_p90_ms = 0.0;
    double latency_p99_ms = 0.0;
    double latency_min_ms = 0.0;
    double latency_max_ms = 0.0;
    std::array<std::size_t, v2::benchmark::kLatencyBucketCount> latency_bucket_counts{};
};

// ---------------------------------------------------------------------------
// LoadController
// ---------------------------------------------------------------------------

class LoadController : public std::enable_shared_from_this<LoadController> {
public:
    LoadController(asio::io_context& io,
                   std::size_t client_count,
                   BenchScenario scenario,
                   std::chrono::steady_clock::time_point started_at)
        : io_context_(io), client_count_(client_count), scenario_(scenario),
          evidence_(client_count, started_at) {}

    void on_client_started() { evidence_.on_started(); }
    void on_tcp_connected() { evidence_.on_tcp_connected(); }
    void on_client_authenticated() { (void)evidence_.on_authenticated(); }

    void on_client_done(std::size_t msgs, bool was_authenticated) {
        evidence_.on_terminal(was_authenticated, false, false);
        completed_clients_.fetch_add(1, std::memory_order_relaxed);
        completed_packets_.fetch_add(msgs, std::memory_order_relaxed);
        try_stop();
    }

    void record_push_message() {
        push_packets_.fetch_add(1, std::memory_order_relaxed);
    }

    void record_business_send_attempt() {
        business_send_attempts_.fetch_add(1, std::memory_order_relaxed);
    }

    void record_business_send_success() {
        business_send_successes_.fetch_add(1, std::memory_order_relaxed);
    }

    void on_client_rejected(bool was_authenticated) {
        evidence_.on_terminal(was_authenticated, false, false);
        rejected_clients_.fetch_add(1, std::memory_order_relaxed);
        try_stop();
    }

    void on_client_failed(bool was_authenticated) {
        evidence_.on_terminal(was_authenticated, false, false);
        failed_clients_.fetch_add(1, std::memory_order_relaxed);
        try_stop();
    }

    void on_client_cancelled(bool was_authenticated, bool before_connect) {
        evidence_.on_terminal(was_authenticated, true, before_connect);
        cancelled_clients_.fetch_add(1, std::memory_order_relaxed);
        try_stop();
    }

    void on_time_expired() {
        bool expected = false;
        (void)time_expired_.compare_exchange_strong(expected, true, std::memory_order_relaxed);
    }

    void stop_now() {
        io_context_.stop();
    }

    void mark_global_completion() {
        bool expected = false;
        (void)global_completion_.compare_exchange_strong(expected, true, std::memory_order_relaxed);
    }

    void finish_measurement() { evidence_.finish_measurement(); }

    void stop_if_done() {
        if (done_clients() >= client_count_) {
            io_context_.stop();
        }
    }

    [[nodiscard]] std::size_t completed_clients() const { return completed_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t rejected_clients()  const { return rejected_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t failed_clients()    const { return failed_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t cancelled_clients() const { return cancelled_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t completed_packets() const { return completed_packets_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t push_packets() const { return push_packets_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::uint64_t business_send_attempts() const {
        return business_send_attempts_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint64_t business_send_successes() const {
        return business_send_successes_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::size_t done_clients() const {
        return completed_clients_.load(std::memory_order_relaxed) +
               rejected_clients_.load(std::memory_order_relaxed) +
               failed_clients_.load(std::memory_order_relaxed) +
               cancelled_clients_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] BenchScenario scenario()        const { return scenario_; }
    [[nodiscard]] bool time_expired() const { return time_expired_.load(std::memory_order_relaxed); }
    [[nodiscard]] bool global_completion() const { return global_completion_.load(std::memory_order_relaxed); }
    [[nodiscard]] bool measurement_started() const { return evidence_.measurement_started(); }
    [[nodiscard]] v2::gateway_pressure::LoadEvidenceSnapshot evidence_snapshot() const {
        return evidence_.snapshot();
    }

private:
    void try_stop() {
        const auto done = completed_clients_.load(std::memory_order_relaxed) +
                          rejected_clients_.load(std::memory_order_relaxed) +
                          failed_clients_.load(std::memory_order_relaxed) +
                          cancelled_clients_.load(std::memory_order_relaxed);
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
    std::atomic<std::size_t> cancelled_clients_{0};
    std::atomic<std::size_t> completed_packets_{0};
    std::atomic<std::size_t> push_packets_{0};
    std::atomic<std::uint64_t> business_send_attempts_{0};
    std::atomic<std::uint64_t> business_send_successes_{0};
    std::atomic<bool> finished_{false};
    std::atomic<bool> time_expired_{false};
    std::atomic<bool> global_completion_{false};
    v2::gateway_pressure::LoadEvidence evidence_;
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
               std::shared_ptr<std::vector<std::shared_ptr<std::atomic<std::size_t>>>> room_done = nullptr,
               std::shared_ptr<std::vector<std::shared_ptr<std::atomic<std::size_t>>>> room_ready = nullptr,
               std::shared_ptr<std::atomic<std::uint64_t>> progress_tick = nullptr)
        : strand_(asio::make_strand(io)),
          resolver_(strand_), socket_(strand_), send_timer_(strand_),
          controller_(std::move(ctl)), config_(std::move(cfg)),
          client_index_(idx),
          user_id_("bench_user_" + std::to_string(idx)),
          throughput_(throughput),
          room_done_counters_(std::move(room_done)),
          room_ready_counters_(std::move(room_ready)),
          progress_tick_(std::move(progress_tick)) {}

    void start() {
        auto self = shared_from_this();
        const auto stagger = std::chrono::milliseconds(
            static_cast<std::int64_t>((client_index_ * 1000) /
                                      config_.ramp_clients_per_second));
        send_timer_.expires_after(stagger);
        send_timer_.async_wait([self](const error_code& ec) {
            if (ec) {
                return;
            }
            self->controller_->on_client_started();
            self->touch_progress();
            self->resolver_.async_resolve(
                self->config_.host, std::to_string(self->config_.port),
                [self](const error_code& resolve_ec, const tcp::resolver::results_type& eps) {
                    if (resolve_ec) { self->fail("resolve", resolve_ec); return; }
                    self->do_connect(eps);
                });
        });
    }

    void force_complete(bool cancelled) {
        auto self = shared_from_this();
        asio::post(strand_, [self, cancelled]() {
            if (cancelled) {
                self->finish_cancelled();
            } else {
                self->finish();
            }
        });
    }

    [[nodiscard]] const v2::benchmark::LatencyHistogram& histogram() const { return histogram_; }

private:
    void do_connect(const tcp::resolver::results_type& eps) {
        auto self = shared_from_this();
        asio::async_connect(socket_, eps, [self](const error_code& ec, const tcp::endpoint&) {
            if (ec) {
                self->retry_connect_or_fail(ec);
                return;
            }
            self->tcp_connected_ = true;
            self->controller_->on_tcp_connected();
            self->send_login();
        });
    }

    void retry_connect_or_fail(const error_code& ec) {
        if (connect_retry_attempts_ >= kMaxConnectRetries ||
            controller_->time_expired() ||
            controller_->global_completion()) {
            fail("connect", ec);
            return;
        }

        ++connect_retry_attempts_;
        LOG_WARN("pressure client {} connect attempt {} failed: {}; retrying",
                 user_id_, connect_retry_attempts_, ec.message());
        error_code ignored;
        socket_.close(ignored);
        resolver_.async_resolve(
            config_.host, std::to_string(config_.port),
            [self = shared_from_this()](const error_code& resolve_ec,
                                        const tcp::resolver::results_type& eps) {
                if (resolve_ec) {
                    self->fail("resolve", resolve_ec);
                    return;
                }
                const auto delay = bounded_retry_delay(
                    self->connect_retry_attempts_, std::chrono::milliseconds(100));
                self->schedule_delayed(delay, [self, eps]() {
                    self->do_connect(eps);
                });
            });
    }

    void send_login() {
        touch_progress();
        send_packet(net::protocol::kLoginRequest,
                    user_id_ + "|token:" + user_id_ + "|" + user_id_);
    }

    void send_packet(std::uint16_t msg_id,
                     const std::string& body,
                     std::int32_t ec = 0,
                     bool business_message = false) {
        touch_progress();
        outbound_packet_ = net::packet::encode(msg_id, next_request_id_++, ec, body);
        if (config_.scenario == BenchScenario::kEcho) {
            send_timestamp_ = std::chrono::steady_clock::now();
        }
        auto self = shared_from_this();
        asio::async_write(socket_, asio::buffer(outbound_packet_),
            [self, business_message](const error_code& ec, std::size_t) {
                if (ec) { self->fail("write", ec); return; }
                if (business_message) {
                    self->controller_->record_business_send_success();
                }
                self->read_header();
            });
    }

    void read_header() {
        if (read_pending_ || finished_.load(std::memory_order_relaxed)) {
            return;
        }
        read_pending_ = true;
        auto self = shared_from_this();
        asio::async_read(socket_, asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t) {
                self->read_pending_ = false;
                if (ec) { self->fail("read_header", ec); return; }
                self->expected_body_length_ = net::packet::decode_length(self->read_header_);
                self->read_body_.assign(self->expected_body_length_, '\0');
                self->read_body();
            });
    }

    void read_body() {
        auto self = shared_from_this();
        asio::async_read(socket_, asio::buffer(read_body_),
            [self](const error_code& ec, std::size_t) {
                if (ec) { self->fail("read_body", ec); return; }
                auto packet = net::packet::decode_payload(self->read_body_);
                self->dispatch(packet);
            });
    }

    void wait_for_next_message() {
        read_header();
    }

    void handle_login_response() {
        if (!authenticated_) {
            authenticated_ = true;
            controller_->on_client_authenticated();
        }
        wait_for_measurement_start();
    }

    void wait_for_measurement_start() {
        if (controller_->measurement_started()) {
            if (config_.scenario == BenchScenario::kBattle) {
                if (is_room_owner()) {
                    send_packet(net::protocol::kRoomCreateRequest, room_name_for_client());
                } else {
                    schedule_room_join_when_created();
                }
            } else {
                schedule_next();
            }
            return;
        }
        schedule_delayed(std::chrono::milliseconds(5), [this]() {
            wait_for_measurement_start();
        });
    }

    void dispatch(const net::packet::DecodedPacket& pkt) {
        if (finished_.load(std::memory_order_relaxed)) {
            return;
        }
        touch_progress();
        if (pkt.message_id == net::protocol::kErrorResponse) {
            LOG_WARN("pressure client {} received error: code={} body={}",
                     user_id_, pkt.error_code, pkt.body);
            if (config_.scenario == BenchScenario::kBattle && handle_recoverable_battle_error(pkt)) {
                wait_for_next_message();
                return;
            }
            if (config_.scenario == BenchScenario::kBattle &&
                pkt.error_code == static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted)) {
                battle_finished_ = true;
                controller_->mark_global_completion();
                finish();
                return;
            }
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
            handle_login_response();
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

        LOG_WARN("pressure client {} ignoring unexpected echo-flow msg={} body={}",
                 user_id_, pkt.message_id, pkt.body);
    }

    // -------------------------------------------------------------------
    // Battle flow: login → join room → ready → exchange inputs
    // -------------------------------------------------------------------

    void handle_battle_flow(const net::packet::DecodedPacket& pkt) {
        if (pkt.message_id == net::protocol::kLoginResponse) {
            handle_login_response();
            return;
        }

        if (pkt.message_id == net::protocol::kRoomCreateResponse ||
            pkt.message_id == net::protocol::kRoomJoinResponse) {
            in_room_ = true;
            if (const auto joined_counter = room_join_counter()) {
                joined_counter->fetch_add(1, std::memory_order_relaxed);
            }
            schedule_delayed(std::chrono::milliseconds(50), [this]() {
                send_packet(net::protocol::kRoomReadyRequest, "true");
            });
            return;
        }

        if (pkt.message_id == net::protocol::kRoomStatePush) {
            LOG_DEBUG("pressure client {} room_state push: {}", user_id_, pkt.body);
            if (!battle_start_requested_ &&
                is_room_owner() &&
                room_state_all_ready(pkt.body)) {
                LOG_DEBUG("pressure client {} detected all-ready room state, scheduling battle start",
                          user_id_);
                battle_start_requested_ = true;
                schedule_delayed(std::chrono::milliseconds(50), [this]() {
                    LOG_DEBUG("pressure client {} sending battle start for room {}",
                              user_id_, room_name_for_client());
                    send_packet(net::protocol::kBattleStartRequest, room_name_for_client());
                });
            }
            wait_for_next_message();
            return;
        }

        if (pkt.message_id == net::protocol::kRoomReadyResponse) {
            ++ready_count_;
            if (const auto ready_counter = room_ready_counter()) {
                ready_counter->fetch_add(1, std::memory_order_relaxed);
            }
            maybe_schedule_battle_start_retry();
            wait_for_next_message();
            return;
        }

        if (pkt.message_id == net::protocol::kBattleStartResponse ||
            pkt.message_id == net::protocol::kBattleStatePush) {
            LOG_DEBUG("pressure client {} battle-start related msg={} body={}",
                      user_id_, pkt.message_id, pkt.body);
            const bool scheduled_send = maybe_enter_battle(pkt);
            if (battle_finished_ || controller_->global_completion()) {
                finish();
                return;
            }
            if (!scheduled_send) {
                wait_for_next_message();
            }
            return;
        }

        // Battle push (input broadcast)
        if (pkt.message_id == net::protocol::kBattleInputPush) {
            ++received_battle_inputs_;
            throughput_->record();
            controller_->record_push_message();
            if (battle_finished_) {
                controller_->mark_global_completion();
                finish();
                return;
            }
            wait_for_next_message();
            return;
        }

        // Battle input response
        if (pkt.message_id == net::protocol::kBattleInputResponse) {
            ++completed_messages_;
            throughput_->record();
            battle_input_in_flight_ = false;

            auto now = std::chrono::steady_clock::now();
            auto us = std::chrono::duration_cast<std::chrono::microseconds>(now - send_timestamp_).count();
            histogram_.record_us(static_cast<std::uint64_t>(us));

            if (config_.messages_per_client > 0 &&
                completed_messages_ >= config_.messages_per_client) {
                controller_->mark_global_completion();
                finish();
                return;
            }
            if (battle_finished_ || controller_->global_completion()) {
                controller_->mark_global_completion();
                finish();
                return;
            }
            schedule_next();
            return;
        }

        LOG_WARN("pressure client {} ignoring unexpected battle-flow msg={} body={}",
                 user_id_, pkt.message_id, pkt.body);
        wait_for_next_message();
    }

    bool handle_recoverable_battle_error(const net::packet::DecodedPacket& pkt) {
        const auto is_room_backend_error =
            pkt.error_code == static_cast<std::int32_t>(net::protocol::ErrorCode::kRoomBackendUnavailable);
        const auto room_not_ready =
            pkt.body == "room_not_found" ||
            pkt.body == "not_all_ready" ||
            pkt.body == "not_enough_players";
        const auto already_started = pkt.body == "battle_already_started";

        if (!is_room_backend_error && !room_not_ready && !already_started) {
            return false;
        }

        if (!in_room_) {
            if (pkt.body != "room_not_found") {
                return false;
            }
            schedule_room_join_when_created();
            return true;
        }

        if (is_room_owner() && !in_battle_ && (room_not_ready || already_started)) {
            if (already_started) {
                battle_start_requested_ = true;
            } else if (battle_start_error_retries_ >= kMaxBattleStartErrorRetries) {
                return false;
            } else {
                ++battle_start_error_retries_;
                battle_start_requested_ = false;
            }
            const auto delay = already_started
                ? std::chrono::milliseconds(100)
                : bounded_retry_delay(battle_start_error_retries_, std::chrono::milliseconds(100));
            schedule_delayed(delay, [this]() {
                maybe_schedule_battle_start_retry();
            });
            return true;
        }

        if (!is_room_owner() && !in_battle_ && (room_not_ready || already_started)) {
            if (passive_wait_retries_ >= kMaxPassiveBattleWaitRetries) {
                return false;
            }
            ++passive_wait_retries_;
            battle_start_requested_ = false;
            schedule_delayed(bounded_retry_delay(passive_wait_retries_, std::chrono::milliseconds(100)), [this]() {
                wait_for_next_message();
            });
            return true;
        }

        return false;
    }

    // -------------------------------------------------------------------
    // Scheduling
    // -------------------------------------------------------------------

    void schedule_next() {
        if (config_.send_interval.count() > 0) {
            auto self = shared_from_this();
            send_timer_.expires_after(next_send_delay());
            send_timer_.async_wait([self](const error_code& ec) {
                if (ec) return;
                self->send_next_message();
            });
            wait_for_next_message();
        } else {
            send_next_message();
        }
    }

    void send_next_message() {
        if (config_.scenario == BenchScenario::kBattle && in_battle_) {
            if (battle_finished_ || controller_->global_completion()) {
                controller_->mark_global_completion();
                finish();
                return;
            }
            if (battle_input_in_flight_) {
                return;
            }
            battle_input_in_flight_ = true;
            controller_->record_business_send_attempt();
            send_timestamp_ = std::chrono::steady_clock::now();
            send_packet(net::protocol::kBattleInputRequest,
                        "move:" + std::to_string(client_index_) + "," +
                        std::to_string(completed_messages_), 0, true);
        } else {
            controller_->record_business_send_attempt();
            send_timestamp_ = std::chrono::steady_clock::now();
            send_packet(net::protocol::kEchoRequest,
                        "bench_echo_" + std::to_string(completed_messages_), 0, true);
        }
    }

    std::chrono::milliseconds next_send_delay() noexcept {
        auto delay = config_.send_interval;
        if (config_.scenario == BenchScenario::kBattle &&
            completed_messages_ == 0 &&
            config_.send_interval.count() > 0) {
            const auto group_size = std::max<std::size_t>(2, config_.room_group_size);
            const auto group_index = client_index_ / group_size;
            const auto room_count = std::max<std::size_t>(
                1, (config_.client_count + group_size - 1) / group_size);
            const auto phase_ms = static_cast<int>(
                (group_index * static_cast<std::size_t>(config_.send_interval.count())) / room_count);
            delay += std::chrono::milliseconds(phase_ms);
        }
        return delay;
    }

    bool maybe_enter_battle(const net::packet::DecodedPacket& pkt) {
        if (in_battle_) {
            if (pkt.message_id == net::protocol::kBattleStatePush) {
                handle_battle_state_push(pkt.body);
            }
            return false;
        }

        if (pkt.message_id == net::protocol::kBattleStartResponse) {
            in_battle_ = true;
            if (!battle_loop_started_) {
                battle_loop_started_ = true;
                schedule_next();
                return true;
            }
            return false;
        }

        const auto parsed = v2::gateway::parse_battle_wire_body(pkt.body);
        if (!parsed.has_value()) {
            if (pkt.message_id == net::protocol::kBattleStatePush) {
                return handle_battle_state_push(pkt.body);
            }
            return false;
        }

        const auto kind = v2::gateway::battle_wire_body_kind(*parsed);
        if (kind == v2::gateway::BattleWireBodyKind::kStarted ||
            kind == v2::gateway::BattleWireBodyKind::kState) {
            if (kind == v2::gateway::BattleWireBodyKind::kState) {
                const auto& state = std::get<v2::gateway::ParsedBattleStateBody>(*parsed);
                if (state.kind == "settlement" || state.kind == "finished") {
                    battle_finished_ = true;
                    controller_->mark_global_completion();
                    return false;
                }
            }
            in_battle_ = true;
            if (!battle_loop_started_) {
                battle_loop_started_ = true;
                schedule_next();
                return true;
            }
        }
        return false;
    }

    bool handle_battle_state_push(std::string_view body) {
        if (body.empty() || body.front() != '{') {
            return false;
        }

        nlohmann::json doc = nlohmann::json::parse(body, nullptr, false);
        if (doc.is_discarded() || !doc.contains("kind")) {
            return false;
        }

        const auto kind = doc.value("kind", "");
        if (kind == "frame_advanced") {
            ++received_battle_inputs_;
            throughput_->record();
            controller_->record_push_message();
            touch_progress();
            return false;
        }

        if (kind == "battle_finished") {
            battle_finished_ = true;
            controller_->mark_global_completion();
            finish();
            return true;
        }

        return false;
    }

    static bool room_state_all_ready(std::string_view body) {
        constexpr std::string_view marker = ":members=";
        const auto members_pos = body.find(marker);
        if (members_pos == std::string_view::npos) {
            return false;
        }

        const auto members_begin = members_pos + marker.size();
        const auto members = body.substr(members_begin);
        if (members.empty()) {
            return false;
        }

        std::size_t start = 0;
        bool found_member = false;
        while (start < members.size()) {
            const auto end = members.find(';', start);
            const auto item = members.substr(start, end == std::string_view::npos
                                                        ? members.size() - start
                                                        : end - start);
            if (!item.empty()) {
                found_member = true;
                const auto sep = item.rfind(':');
                if (sep == std::string_view::npos || sep + 1 >= item.size() ||
                    item.substr(sep + 1) != "1") {
                    return false;
                }
            }
            if (end == std::string_view::npos) {
                break;
            }
            start = end + 1;
        }
        return found_member;
    }

    [[nodiscard]] std::size_t room_group_index() const noexcept {
        const auto group_size = std::max<std::size_t>(2, config_.room_group_size);
        return client_index_ / group_size;
    }

    [[nodiscard]] bool is_room_owner() const noexcept {
        const auto group_size = std::max<std::size_t>(2, config_.room_group_size);
        return (client_index_ % group_size) == 0;
    }

    [[nodiscard]] std::string room_name_for_client() const {
        return config_.room_name + "_" + std::to_string(room_group_index());
    }

    [[nodiscard]] std::shared_ptr<std::atomic<std::size_t>> room_join_counter() const {
        if (!room_done_counters_) {
            return nullptr;
        }
        const auto group_index = room_group_index();
        if (group_index >= room_done_counters_->size()) {
            return nullptr;
        }
        return (*room_done_counters_)[group_index];
    }

    [[nodiscard]] std::shared_ptr<std::atomic<std::size_t>> room_ready_counter() const {
        if (!room_ready_counters_) {
            return nullptr;
        }
        const auto group_index = room_group_index();
        if (group_index >= room_ready_counters_->size()) {
            return nullptr;
        }
        return (*room_ready_counters_)[group_index];
    }

    void maybe_schedule_battle_start_retry() {
        if (!is_room_owner() || battle_start_requested_ || in_battle_ || battle_finished_) {
            return;
        }

        const auto ready_counter = room_ready_counter();
        const auto ready_clients = ready_counter
            ? ready_counter->load(std::memory_order_relaxed)
            : ready_count_;
        const auto joined_counter = room_join_counter();
        const auto joined_clients = joined_counter
            ? joined_counter->load(std::memory_order_relaxed)
            : config_.room_group_size;

        if (joined_clients >= config_.room_group_size &&
            ready_clients >= config_.room_group_size) {
            battle_start_requested_ = true;
            schedule_delayed(std::chrono::milliseconds(150), [this]() {
                LOG_DEBUG("pressure client {} sending delayed battle start for room {}",
                          user_id_, room_name_for_client());
                send_packet(net::protocol::kBattleStartRequest, room_name_for_client());
            });
            return;
        }

        if (battle_start_retry_attempts_ >= kMaxBattleStartWaitRetries) {
            LOG_WARN("pressure client {} timed out waiting for grouped battle start in room {}",
                     user_id_, room_name_for_client());
            finish_rejected();
            return;
        }

        ++battle_start_retry_attempts_;
        schedule_delayed(std::chrono::milliseconds(100), [this]() {
            maybe_schedule_battle_start_retry();
        });
    }

    void schedule_room_join_when_created() {
        if (is_room_owner() || in_room_ || finished_.load(std::memory_order_relaxed)) {
            return;
        }

        const auto joined_counter = room_join_counter();
        const auto joined_clients = joined_counter
            ? joined_counter->load(std::memory_order_relaxed)
            : 0U;
        if (joined_clients > 0) {
            const auto stagger_ms = std::min<std::size_t>(room_group_index() * 2, 500);
            schedule_delayed(std::chrono::milliseconds(stagger_ms), [this]() {
                send_packet(net::protocol::kRoomJoinRequest, room_name_for_client());
            });
            return;
        }

        if (join_wait_attempts_ >= kMaxRoomCreateWaitRetries) {
            LOG_WARN("pressure client {} timed out waiting for room creation in room {}",
                     user_id_, room_name_for_client());
            finish_rejected();
            return;
        }

        ++join_wait_attempts_;
        schedule_delayed(std::chrono::milliseconds(50), [this]() {
            schedule_room_join_when_created();
        });
    }

    static std::chrono::milliseconds bounded_retry_delay(std::size_t attempt,
                                                         std::chrono::milliseconds base) {
        const auto multiplier = std::min<std::size_t>(attempt, 8);
        return std::min(std::chrono::milliseconds(base.count() * static_cast<int>(multiplier)),
                        std::chrono::milliseconds(1000));
    }

    void touch_progress() {
        if (progress_tick_) {
            progress_tick_->fetch_add(1, std::memory_order_relaxed);
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
        if (finished_.exchange(true, std::memory_order_relaxed)) {
            return;
        }
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.cancel(ignored);
        socket_.close(ignored);
        controller_->on_client_done(completed_messages_, authenticated_);
    }

    void finish_rejected() {
        if (finished_.exchange(true, std::memory_order_relaxed)) {
            return;
        }
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.cancel(ignored);
        socket_.close(ignored);
        controller_->on_client_rejected(authenticated_);
    }

    void finish_cancelled() {
        if (finished_.exchange(true, std::memory_order_relaxed)) {
            return;
        }
        error_code ignored;
        (void)send_timer_.cancel();
        resolver_.cancel();
        socket_.cancel(ignored);
        socket_.close(ignored);
        controller_->on_client_cancelled(authenticated_, !tcp_connected_);
    }

    void fail(std::string_view stage = "unknown", error_code ec = {}) {
        if (finished_.exchange(true, std::memory_order_relaxed)) {
            return;
        }
        if (ec) {
            LOG_WARN("pressure client {} failed at {}: {}", user_id_, stage, ec.message());
        } else {
            LOG_WARN("pressure client {} failed at {}", user_id_, stage);
        }
        if (config_.scenario == BenchScenario::kBattle &&
            (battle_finished_ || controller_->global_completion())) {
            error_code ignored;
            (void)send_timer_.cancel();
            socket_.cancel(ignored);
            socket_.close(ignored);
            controller_->on_client_done(completed_messages_, authenticated_);
            return;
        }
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.cancel(ignored);
        socket_.close(ignored);
        controller_->on_client_failed(authenticated_);
    }

    // -------------------------------------------------------------------
    // Members
    // -------------------------------------------------------------------

    asio::strand<asio::io_context::executor_type> strand_;
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
    bool battle_loop_started_ = false;
    bool battle_finished_ = false;
    bool battle_input_in_flight_ = false;
    std::size_t battle_start_retry_attempts_ = 0;
    std::size_t battle_start_error_retries_ = 0;
    std::size_t connect_retry_attempts_ = 0;
    std::size_t join_wait_attempts_ = 0;
    std::size_t passive_wait_retries_ = 0;
    std::size_t ready_count_ = 0;
    std::size_t received_battle_inputs_ = 0;
    bool battle_start_requested_ = false;
    bool read_pending_ = false;
    bool tcp_connected_ = false;
    bool authenticated_ = false;
    std::atomic<bool> finished_{false};
    std::shared_ptr<std::vector<std::shared_ptr<std::atomic<std::size_t>>>> room_done_counters_;
    std::shared_ptr<std::vector<std::shared_ptr<std::atomic<std::size_t>>>> room_ready_counters_;
    std::shared_ptr<std::atomic<std::uint64_t>> progress_tick_;
    static constexpr std::size_t kMaxRoomCreateWaitRetries = 600;
    static constexpr std::size_t kMaxBattleStartWaitRetries = 300;
    static constexpr std::size_t kMaxBattleStartErrorRetries = 12;
    static constexpr std::size_t kMaxPassiveBattleWaitRetries = 20;
    static constexpr std::size_t kMaxConnectRetries = 8;
};

// ---------------------------------------------------------------------------
// JSON output
// ---------------------------------------------------------------------------

nlohmann::json to_json(const BenchResult& r) {
    return {
        {"scenario", to_string(r.scenario)},
        {"target_clients", r.target_clients},
        {"started_clients", r.started_clients},
        {"tcp_connected_clients", r.tcp_connected_clients},
        {"authenticated_clients", r.authenticated_clients},
        {"active_clients", r.active_clients},
        {"peak_active_clients", r.peak_active_clients},
        {"cancelled_clients", r.cancelled_clients},
        {"cancelled_before_connect", r.cancelled_before_connect},
        {"business_send_attempts", r.business_send_attempts},
        {"business_send_successes", r.business_send_successes},
        {"connected_clients", r.connected_clients},
        {"failed_clients", r.failed_clients},
        {"rejected_clients", r.rejected_clients},
        {"total_messages", r.total_messages},
        {"response_messages", r.response_messages},
        {"push_messages", r.push_messages},
        {"elapsed_seconds", r.elapsed_seconds},
        {"total_elapsed_seconds", r.total_elapsed_seconds},
        {"ramp_up_seconds", r.ramp_up_seconds},
        {"ramp_timeout_seconds", r.ramp_timeout_seconds},
        {"ramp_completed", r.ramp_completed},
        {"measurement_started", r.measurement_started},
        {"steady_state_target_seconds", r.steady_state_target_seconds},
        {"steady_state_elapsed_seconds", r.steady_state_elapsed_seconds},
        {"steady_state_completed", r.steady_state_completed},
        {"termination_reason", r.termination_reason},
        {"load_model", r.load_model},
        {"configured_request_rate_ceiling_ops_per_sec", r.configured_request_rate_ceiling_ops_per_sec},
        {"configured_request_rate_is_bounded", r.configured_request_rate_is_bounded},
        {"achieved_send_rate_ops_per_sec", r.achieved_send_rate_ops_per_sec},
        {"achieved_response_rate_ops_per_sec", r.achieved_response_rate_ops_per_sec},
        {"throughput_msg_per_sec", r.throughput_msg_per_sec},
        {"latency_p50_ms", r.latency_p50_ms},
        {"latency_p90_ms", r.latency_p90_ms},
        {"latency_p99_ms", r.latency_p99_ms},
        {"latency_min_ms", r.latency_min_ms},
        {"latency_max_ms", r.latency_max_ms},
        {"latency_buckets_ms", v2::benchmark::kLatencyBucketBoundariesMs},
        {"latency_bucket_counts", r.latency_bucket_counts},
    };
}

}  // namespace

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    const v2::platform::HighResTimer hi_res_timer;
    app::logging::init("v2_gateway_pressure");

    const auto config = parse_args(argc, argv);
    LOG_INFO("v2_gateway_pressure: scenario={} host={} port={} clients={} msgs={} duration={}s",
             to_string(config.scenario), config.host, config.port,
             config.client_count, config.messages_per_client, config.duration.count());

    asio::io_context io;
    const auto started_at = std::chrono::steady_clock::now();
    auto controller = std::make_shared<LoadController>(
        io, config.client_count, config.scenario, started_at);
    v2::benchmark::ThroughputTracker throughput(5, 10);

    std::vector<std::shared_ptr<LoadClient>> clients;
    clients.reserve(config.client_count);
    std::atomic<bool> stop_requested{false};
    std::atomic<bool> run_finished{false};
    std::mutex termination_mutex;
    std::string termination_reason;
    auto stop_poll_timer = std::make_shared<asio::steady_timer>(io);
    auto stop_grace_timer = std::make_shared<asio::steady_timer>(io);
    std::function<void()> poll_stop_done;
    poll_stop_done = [&]() {
        if (controller->done_clients() >= config.client_count) {
            io.stop();
            return;
        }
        stop_poll_timer->expires_after(std::chrono::milliseconds(50));
        stop_poll_timer->async_wait([&](const error_code& ec) {
            if (!ec) {
                poll_stop_done();
            }
        });
    };
    auto request_stop = [&](std::string_view reason, bool cancelled) {
        bool expected = false;
        if (!stop_requested.compare_exchange_strong(expected, true, std::memory_order_relaxed)) {
            return;
        }
        controller->finish_measurement();
        const std::string reason_text(reason);
        {
            std::lock_guard lock(termination_mutex);
            termination_reason = reason_text;
        }
        asio::post(io, [&, reason_text, cancelled]() {
            controller->mark_global_completion();
            LOG_WARN("v2_gateway_pressure forcing stop: {}", reason_text);
            for (const auto& client : clients) {
                client->force_complete(cancelled);
            }
            LOG_WARN("v2_gateway_pressure stop requested: done={} completed={} failed={} rejected={} cancelled={}",
                     controller->done_clients(),
                     controller->completed_clients(),
                     controller->failed_clients(),
                     controller->rejected_clients(),
                     controller->cancelled_clients());
            poll_stop_done();
            stop_grace_timer->expires_after(std::chrono::seconds(5));
            stop_grace_timer->async_wait([&](const error_code& ec) {
                if (!ec && !run_finished.load(std::memory_order_relaxed)) {
                    io.stop();
                }
            });
        });
    };

    const auto room_group_size = std::max<std::size_t>(2, config.room_group_size);
    const auto room_group_count =
        config.scenario == BenchScenario::kBattle
            ? std::max<std::size_t>(1, (config.client_count + room_group_size - 1) / room_group_size)
            : 1U;
    auto room_done_counters =
        std::make_shared<std::vector<std::shared_ptr<std::atomic<std::size_t>>>>();
    room_done_counters->reserve(room_group_count);
    auto room_ready_counters =
        std::make_shared<std::vector<std::shared_ptr<std::atomic<std::size_t>>>>();
    room_ready_counters->reserve(room_group_count);
    for (std::size_t i = 0; i < room_group_count; ++i) {
        room_done_counters->push_back(std::make_shared<std::atomic<std::size_t>>(0));
        room_ready_counters->push_back(std::make_shared<std::atomic<std::size_t>>(0));
    }
    auto progress_tick = std::make_shared<std::atomic<std::uint64_t>>(0);

    auto write_emergency_result = [&](std::string_view reason) {
        if (config.output_path.empty()) {
            return;
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started_at);
        const auto evidence = controller->evidence_snapshot();
        nlohmann::json result = {
            {"scenario", to_string(config.scenario)},
            {"target_clients", config.client_count},
            {"started_clients", evidence.started_clients},
            {"tcp_connected_clients", evidence.tcp_connected_clients},
            {"authenticated_clients", evidence.authenticated_clients},
            {"active_clients", evidence.active_clients},
            {"peak_active_clients", evidence.peak_active_clients},
            {"cancelled_clients", evidence.cancelled_clients},
            {"cancelled_before_connect", evidence.cancelled_before_connect},
            {"business_send_attempts", controller->business_send_attempts()},
            {"business_send_successes", controller->business_send_successes()},
            {"connected_clients", evidence.tcp_connected_clients},
            {"failed_clients", controller->failed_clients()},
            {"rejected_clients", controller->rejected_clients()},
            {"total_messages", throughput.total_count()},
            {"elapsed_seconds", evidence.steady_state_elapsed_seconds},
            {"total_elapsed_seconds", static_cast<double>(elapsed.count()) / 1'000'000.0},
            {"ramp_up_seconds", evidence.ramp_up_seconds},
            {"ramp_timeout_seconds", config.ramp_timeout.count()},
            {"ramp_completed", evidence.ramp_completed},
            {"measurement_started", evidence.measurement_started},
            {"steady_state_target_seconds", config.duration.count()},
            {"steady_state_elapsed_seconds", evidence.steady_state_elapsed_seconds},
            {"steady_state_completed", false},
            {"termination_reason", std::string(reason)},
            {"load_model", "closed_loop_one_in_flight_per_client"},
            {"configured_request_rate_ceiling_ops_per_sec",
             config.send_interval.count() > 0
                 ? static_cast<double>(config.client_count) * 1000.0 /
                       static_cast<double>(config.send_interval.count())
                 : 0.0},
            {"configured_request_rate_is_bounded", config.send_interval.count() > 0},
            {"achieved_send_rate_ops_per_sec", evidence.steady_state_elapsed_seconds > 0.0
                 ? static_cast<double>(controller->business_send_successes()) /
                       evidence.steady_state_elapsed_seconds
                 : 0.0},
            {"achieved_response_rate_ops_per_sec", evidence.steady_state_elapsed_seconds > 0.0
                 ? static_cast<double>(controller->completed_packets()) /
                       evidence.steady_state_elapsed_seconds
                 : 0.0},
            {"throughput_msg_per_sec", evidence.steady_state_elapsed_seconds > 0.0
                ? static_cast<double>(throughput.total_count()) /
                    evidence.steady_state_elapsed_seconds
                : 0.0},
            {"latency_p50_ms", 0.0},
            {"latency_p90_ms", 0.0},
            {"latency_p99_ms", 0.0},
            {"latency_min_ms", 0.0},
            {"latency_max_ms", 0.0},
            {"forced_timeout", true},
            {"timeout_reason", std::string(reason)},
            {"completed_clients", controller->completed_clients()},
            {"done_clients", controller->done_clients()},
        };
        std::ofstream output(config.output_path, std::ios::binary | std::ios::trunc);
        if (output) {
            output << result.dump() << '\n';
            output.flush();
        }
    };

    asio::steady_timer stall_timer(io);
    std::uint64_t last_progress_value = 0;
    std::function<void()> arm_stall_watchdog;
    arm_stall_watchdog = [&]() {
        stall_timer.expires_after(std::chrono::seconds(3));
        stall_timer.async_wait([&](const error_code& ec) {
            if (ec || controller->time_expired() || controller->global_completion()) {
                return;
            }
            const auto current = progress_tick->load(std::memory_order_relaxed);
            if (current == last_progress_value) {
                LOG_WARN("v2_gateway_pressure stalled without progress for 3s; forcing completion");
                controller->mark_global_completion();
                request_stop("stall_watchdog", true);
                return;
            }
            last_progress_value = current;
            arm_stall_watchdog();
        });
    };
    arm_stall_watchdog();

    for (std::size_t i = 0; i < config.client_count; ++i) {
        auto client = std::make_shared<LoadClient>(
            io, controller, config, i, &throughput, room_done_counters, room_ready_counters, progress_tick);
        clients.push_back(client);
        client->start();
    }

    std::thread duration_thread([&]() {
        const auto ramp_deadline = started_at + config.ramp_timeout;
        while (!run_finished.load(std::memory_order_relaxed) &&
               !controller->measurement_started() &&
               std::chrono::steady_clock::now() < ramp_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        if (run_finished.load(std::memory_order_relaxed)) {
            return;
        }
        if (!controller->measurement_started()) {
            request_stop("ramp_timeout", true);
            return;
        }
        if (config.messages_per_client > 0 || config.duration.count() <= 0) {
            return;
        }
        const auto steady_deadline = std::chrono::steady_clock::now() + config.duration;
        while (!run_finished.load(std::memory_order_relaxed) &&
               std::chrono::steady_clock::now() < steady_deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        if (!run_finished.load(std::memory_order_relaxed)) {
            controller->on_time_expired();
            request_stop("steady_duration_elapsed", false);
        }
    });

    const auto hard_timeout = config.ramp_timeout +
        (config.messages_per_client == 0 && config.duration.count() > 0
            ? config.duration
            : std::chrono::seconds(0)) +
        std::chrono::seconds(30);
    std::thread hard_stop_thread([&]() {
        const auto deadline = std::chrono::steady_clock::now() + hard_timeout;
        while (!run_finished.load(std::memory_order_relaxed) &&
               std::chrono::steady_clock::now() < deadline) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        if (run_finished.load(std::memory_order_relaxed)) {
            return;
        }
        std::cerr << "v2_gateway_pressure hard timeout; terminating process\n";
        write_emergency_result("hard_timeout");
        std::quick_exit(2);
    });

    const auto thread_count = config.io_threads > 0
        ? config.io_threads
        : std::max(2u, std::thread::hardware_concurrency());
    std::vector<std::thread> workers;
    workers.reserve(thread_count);
    for (unsigned int i = 0; i < thread_count; ++i) {
        workers.emplace_back([&io]() { io.run(); });
    }
    for (auto& w : workers) w.join();
    run_finished.store(true, std::memory_order_relaxed);
    duration_thread.join();
    if (hard_stop_thread.joinable()) {
        hard_stop_thread.join();
    }

    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started_at);

    // Aggregate results
    BenchResult result;
    result.scenario = config.scenario;
    result.target_clients = config.client_count;
    controller->finish_measurement();
    const auto evidence = controller->evidence_snapshot();
    result.started_clients = evidence.started_clients;
    result.tcp_connected_clients = evidence.tcp_connected_clients;
    result.authenticated_clients = evidence.authenticated_clients;
    result.active_clients = evidence.active_clients;
    result.peak_active_clients = evidence.peak_active_clients;
    result.cancelled_clients = evidence.cancelled_clients;
    result.cancelled_before_connect = evidence.cancelled_before_connect;
    result.business_send_attempts = controller->business_send_attempts();
    result.business_send_successes = controller->business_send_successes();
    result.connected_clients = evidence.tcp_connected_clients;
    result.failed_clients = controller->failed_clients();
    result.rejected_clients = controller->rejected_clients();
    result.elapsed_seconds = evidence.steady_state_elapsed_seconds;
    result.total_elapsed_seconds = static_cast<double>(elapsed.count()) / 1'000'000.0;
    result.ramp_up_seconds = evidence.ramp_up_seconds;
    result.ramp_timeout_seconds = static_cast<double>(config.ramp_timeout.count());
    result.ramp_completed = evidence.ramp_completed;
    result.measurement_started = evidence.measurement_started;
    result.steady_state_target_seconds = static_cast<double>(config.duration.count());
    result.steady_state_elapsed_seconds = evidence.steady_state_elapsed_seconds;
    result.steady_state_completed = controller->time_expired() ||
        (config.scenario == BenchScenario::kBattle && controller->global_completion());
    {
        std::lock_guard lock(termination_mutex);
        result.termination_reason = termination_reason.empty()
            ? (controller->global_completion() ? "natural_completion" : "clients_completed")
            : termination_reason;
    }
    result.configured_request_rate_is_bounded = config.send_interval.count() > 0;
    result.configured_request_rate_ceiling_ops_per_sec =
        result.configured_request_rate_is_bounded
            ? static_cast<double>(config.client_count) * 1000.0 /
                  static_cast<double>(config.send_interval.count())
            : 0.0;
    result.achieved_send_rate_ops_per_sec = result.steady_state_elapsed_seconds > 0.0
        ? static_cast<double>(result.business_send_successes) /
              result.steady_state_elapsed_seconds
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
    result.latency_bucket_counts = lat_snap.bucket_counts;
    const auto message_counts = v2::gateway_pressure::final_message_counts(
        lat_snap.total_count, controller->push_packets());
    result.response_messages = message_counts.response_messages;
    result.push_messages = message_counts.push_messages;
    result.total_messages = message_counts.total_messages;
    result.throughput_msg_per_sec = result.steady_state_elapsed_seconds > 0.0
        ? static_cast<double>(result.total_messages) / result.steady_state_elapsed_seconds
        : 0.0;
    result.achieved_response_rate_ops_per_sec = result.steady_state_elapsed_seconds > 0.0
        ? static_cast<double>(result.response_messages) /
              result.steady_state_elapsed_seconds
        : 0.0;

    const auto result_json = to_json(result).dump();

    if (!config.output_path.empty()) {
        std::ofstream output(config.output_path, std::ios::binary | std::ios::trunc);
        if (!output) {
            LOG_ERROR("v2_gateway_pressure failed to open output file: {}", config.output_path);
            return 2;
        }
        output << result_json << '\n';
    }

    // Output JSON
    LOG_INFO("v2_gateway_pressure done: connected={} failed={} rejected={} msgs={} "
             "elapsed={:.3f}s throughput={:.1f}/s p50={:.3f}ms p99={:.3f}ms",
             result.connected_clients, result.failed_clients, result.rejected_clients,
             result.total_messages, result.elapsed_seconds, result.throughput_msg_per_sec,
             result.latency_p50_ms, result.latency_p99_ms);

    fmt::print("{}\n", result_json);

    const bool valid_evidence = result.ramp_completed && result.measurement_started &&
        result.started_clients == result.target_clients &&
        result.tcp_connected_clients == result.target_clients &&
        result.authenticated_clients == result.target_clients &&
        result.peak_active_clients == result.target_clients &&
        result.cancelled_clients == 0;
    return (!valid_evidence || (result.failed_clients > 0 && !controller->time_expired())) ? 1 : 0;
}
