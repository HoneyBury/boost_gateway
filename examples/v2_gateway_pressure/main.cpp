#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/benchmark/latency_histogram.h"
#include "v2/benchmark/throughput_tracker.h"
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
        (void)time_expired_.compare_exchange_strong(expected, true, std::memory_order_relaxed);
    }

    void stop_now() {
        io_context_.stop();
    }

    void mark_global_completion() {
        bool expected = false;
        (void)global_completion_.compare_exchange_strong(expected, true, std::memory_order_relaxed);
    }

    void stop_if_done() {
        if (done_clients() >= client_count_) {
            io_context_.stop();
        }
    }

    [[nodiscard]] std::size_t completed_clients() const { return completed_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t rejected_clients()  const { return rejected_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t failed_clients()    const { return failed_clients_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t completed_packets() const { return completed_packets_.load(std::memory_order_relaxed); }
    [[nodiscard]] std::size_t done_clients() const {
        return completed_clients_.load(std::memory_order_relaxed) +
               rejected_clients_.load(std::memory_order_relaxed) +
               failed_clients_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] BenchScenario scenario()        const { return scenario_; }
    [[nodiscard]] bool time_expired() const { return time_expired_.load(std::memory_order_relaxed); }
    [[nodiscard]] bool global_completion() const { return global_completion_.load(std::memory_order_relaxed); }

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
    std::atomic<bool> global_completion_{false};
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
               std::shared_ptr<std::atomic<std::uint64_t>> progress_tick = nullptr)
        : strand_(asio::make_strand(io)),
          resolver_(strand_), socket_(strand_), send_timer_(strand_),
          controller_(std::move(ctl)), config_(std::move(cfg)),
          client_index_(idx),
          user_id_("bench_user_" + std::to_string(idx)),
          throughput_(throughput),
          room_done_counters_(std::move(room_done)),
          progress_tick_(std::move(progress_tick)) {}

    void start() {
        auto self = shared_from_this();
        const auto stagger = std::chrono::milliseconds(
            static_cast<int>((client_index_ % 8) * 20));
        send_timer_.expires_after(stagger);
        send_timer_.async_wait([self](const error_code& ec) {
            if (ec) {
                return;
            }
            self->resolver_.async_resolve(
                self->config_.host, std::to_string(self->config_.port),
                [self](const error_code& resolve_ec, const tcp::resolver::results_type& eps) {
                    if (resolve_ec) { self->fail(); return; }
                    self->do_connect(eps);
                });
        });
    }

    void force_complete() {
        auto self = shared_from_this();
        asio::post(strand_, [self]() {
            self->finish();
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
        touch_progress();
        send_packet(net::protocol::kLoginRequest,
                    user_id_ + "|token:" + user_id_ + "|" + user_id_);
    }

    void send_packet(std::uint16_t msg_id, const std::string& body, std::int32_t ec = 0) {
        touch_progress();
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
        if (read_pending_ || finished_.load(std::memory_order_relaxed)) {
            return;
        }
        read_pending_ = true;
        auto self = shared_from_this();
        asio::async_read(socket_, asio::buffer(read_header_),
            [self](const error_code& ec, std::size_t) {
                self->read_pending_ = false;
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

    void wait_for_next_message() {
        read_header();
    }

    void dispatch(const net::packet::DecodedPacket& pkt) {
        touch_progress();
        if (pkt.message_id == net::protocol::kErrorResponse) {
            LOG_WARN("pressure client {} received error: code={} body={}",
                     user_id_, pkt.error_code, pkt.body);
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

        LOG_WARN("pressure client {} ignoring unexpected echo-flow msg={} body={}",
                 user_id_, pkt.message_id, pkt.body);
    }

    // -------------------------------------------------------------------
    // Battle flow: login → join room → ready → exchange inputs
    // -------------------------------------------------------------------

    void handle_battle_flow(const net::packet::DecodedPacket& pkt) {
        if (pkt.message_id == net::protocol::kLoginResponse) {
            if (is_room_owner()) {
                send_packet(net::protocol::kRoomCreateRequest, room_name_for_client());
            } else {
                schedule_delayed(std::chrono::milliseconds(20 * client_index_), [this]() {
                    send_packet(net::protocol::kRoomJoinRequest, room_name_for_client());
                });
            }
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
            LOG_INFO("pressure client {} room_state push: {}", user_id_, pkt.body);
            if (!battle_start_requested_ &&
                is_room_owner() &&
                room_state_all_ready(pkt.body)) {
                LOG_INFO("pressure client {} detected all-ready room state, scheduling battle start",
                         user_id_);
                battle_start_requested_ = true;
                schedule_delayed(std::chrono::milliseconds(50), [this]() {
                    LOG_INFO("pressure client {} sending battle start for room {}",
                             user_id_, room_name_for_client());
                    send_packet(net::protocol::kBattleStartRequest, room_name_for_client());
                });
            }
            wait_for_next_message();
            return;
        }

        if (pkt.message_id == net::protocol::kRoomReadyResponse) {
            ++ready_count_;
            maybe_schedule_battle_start_retry();
            wait_for_next_message();
            return;
        }

        if (pkt.message_id == net::protocol::kBattleStartResponse ||
            pkt.message_id == net::protocol::kBattleStatePush) {
            LOG_INFO("pressure client {} battle-start related msg={} body={}",
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
            if (battle_finished_ || controller_->global_completion()) {
                controller_->mark_global_completion();
                finish();
                return;
            }
            send_timestamp_ = std::chrono::steady_clock::now();
            send_packet(net::protocol::kBattleInputRequest,
                        "move:" + std::to_string(client_index_) + "," +
                        std::to_string(completed_messages_));
        } else {
            send_timestamp_ = std::chrono::steady_clock::now();
            send_packet(net::protocol::kEchoRequest, "bench_echo_" + std::to_string(completed_messages_));
        }
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
            touch_progress();
            if (!battle_finished_ && !controller_->global_completion()) {
                schedule_next();
                return true;
            }
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

    void maybe_schedule_battle_start_retry() {
        if (!is_room_owner() || battle_start_requested_ || in_battle_ || battle_finished_) {
            return;
        }

        const auto joined_counter = room_join_counter();
        const auto joined_clients = joined_counter
            ? joined_counter->load(std::memory_order_relaxed)
            : config_.room_group_size;

        if (joined_clients >= config_.room_group_size) {
            battle_start_requested_ = true;
            schedule_delayed(std::chrono::milliseconds(150), [this]() {
                LOG_INFO("pressure client {} sending delayed battle start for room {}",
                         user_id_, room_name_for_client());
                send_packet(net::protocol::kBattleStartRequest, room_name_for_client());
            });
            return;
        }

        if (battle_start_retry_attempts_ >= 50) {
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
        controller_->on_client_done(completed_messages_);
    }

    void finish_rejected() {
        if (finished_.exchange(true, std::memory_order_relaxed)) {
            return;
        }
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.cancel(ignored);
        socket_.close(ignored);
        controller_->on_client_rejected();
    }

    void fail() {
        if (finished_.exchange(true, std::memory_order_relaxed)) {
            return;
        }
        if (config_.scenario == BenchScenario::kBattle &&
            (battle_finished_ || controller_->global_completion())) {
            error_code ignored;
            (void)send_timer_.cancel();
            socket_.cancel(ignored);
            socket_.close(ignored);
            controller_->on_client_done(completed_messages_);
            return;
        }
        error_code ignored;
        (void)send_timer_.cancel();
        socket_.cancel(ignored);
        socket_.close(ignored);
        controller_->on_client_failed();
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
    std::size_t battle_start_retry_attempts_ = 0;
    std::size_t ready_count_ = 0;
    std::size_t received_battle_inputs_ = 0;
    bool battle_start_requested_ = false;
    bool read_pending_ = false;
    std::atomic<bool> finished_{false};
    std::shared_ptr<std::vector<std::shared_ptr<std::atomic<std::size_t>>>> room_done_counters_;
    std::shared_ptr<std::atomic<std::uint64_t>> progress_tick_;
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
    std::atomic<bool> stop_requested{false};
    std::atomic<bool> run_finished{false};
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
    auto request_stop = [&](std::string_view reason) {
        bool expected = false;
        if (!stop_requested.compare_exchange_strong(expected, true, std::memory_order_relaxed)) {
            return;
        }
        const std::string reason_text(reason);
        asio::post(io, [&, reason_text]() {
            controller->mark_global_completion();
            LOG_WARN("v2_gateway_pressure forcing stop: {}", reason_text);
            for (const auto& client : clients) {
                client->force_complete();
            }
            LOG_WARN("v2_gateway_pressure stop requested: done={} completed={} failed={} rejected={}",
                     controller->done_clients(),
                     controller->completed_clients(),
                     controller->failed_clients(),
                     controller->rejected_clients());
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
    for (std::size_t i = 0; i < room_group_count; ++i) {
        room_done_counters->push_back(std::make_shared<std::atomic<std::size_t>>(0));
    }
    auto progress_tick = std::make_shared<std::atomic<std::uint64_t>>(0);

    const auto started_at = std::chrono::steady_clock::now();
    auto write_emergency_result = [&](std::string_view reason) {
        if (config.output_path.empty()) {
            return;
        }
        const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - started_at);
        nlohmann::json result = {
            {"scenario", to_string(config.scenario)},
            {"target_clients", config.client_count},
            {"connected_clients", config.client_count
                - controller->failed_clients()
                - controller->rejected_clients()},
            {"failed_clients", controller->failed_clients()},
            {"rejected_clients", controller->rejected_clients()},
            {"total_messages", throughput.total_count()},
            {"elapsed_seconds", static_cast<double>(elapsed.count()) / 1'000'000.0},
            {"throughput_msg_per_sec", elapsed.count() > 0
                ? static_cast<double>(throughput.total_count()) /
                    (static_cast<double>(elapsed.count()) / 1'000'000.0)
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
                request_stop("stall watchdog");
                return;
            }
            last_progress_value = current;
            arm_stall_watchdog();
        });
    };
    arm_stall_watchdog();

    for (std::size_t i = 0; i < config.client_count; ++i) {
        auto client = std::make_shared<LoadClient>(
            io, controller, config, i, &throughput, room_done_counters, progress_tick);
        clients.push_back(client);
        client->start();
    }

    std::thread duration_thread;
    if (config.messages_per_client == 0 && config.duration.count() > 0) {
        duration_thread = std::thread([&, duration = config.duration]() {
            const auto deadline = std::chrono::steady_clock::now() + duration;
            while (!run_finished.load(std::memory_order_relaxed) &&
                   std::chrono::steady_clock::now() < deadline) {
                std::this_thread::sleep_for(std::chrono::milliseconds(50));
            }
            if (!run_finished.load(std::memory_order_relaxed)) {
                controller->on_time_expired();
                request_stop("duration elapsed");
            }
        });
    }

    const auto hard_timeout = config.messages_per_client == 0 && config.duration.count() > 0
        ? config.duration + std::chrono::seconds(30)
        : std::chrono::seconds(30);
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
        std::quick_exit(controller->failed_clients() == 0 ? 0 : 2);
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
    if (duration_thread.joinable()) {
        duration_thread.join();
    }
    if (hard_stop_thread.joinable()) {
        hard_stop_thread.join();
    }

    const auto elapsed = std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now() - started_at);

    // Aggregate results
    BenchResult result;
    result.scenario = config.scenario;
    result.target_clients = config.client_count;
    result.connected_clients = config.client_count
        - controller->failed_clients()
        - controller->rejected_clients();
    result.failed_clients = controller->failed_clients();
    result.rejected_clients = controller->rejected_clients();
    result.total_messages = throughput.total_count();
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

    return (result.failed_clients > 0 && !controller->time_expired()) ? 1 : 0;
}
