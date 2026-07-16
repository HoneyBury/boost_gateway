#pragma once

#include <boost/asio.hpp>
#include <boost/system/error_code.hpp>

#include <array>
#include <chrono>
#include <cstdint>
#include <deque>
#include <functional>
#include <memory>
#include <string>
#include <vector>

namespace net {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;
using error_code = boost::system::error_code;

struct SessionOptions {
    std::uint32_t max_packet_size = 1024 * 1024;
    std::size_t max_pending_write_bytes = 256 * 1024;
    double backpressure_high_watermark = 0.75;
    double backpressure_low_watermark = 0.25;
    std::chrono::milliseconds heartbeat_check_interval{5000};
    std::chrono::milliseconds heartbeat_timeout{30000};
};

class Session : public std::enable_shared_from_this<Session> {
public:
    struct PacketMessage {
        std::uint16_t message_id = 0;
        std::uint32_t request_id = 0;
        std::int32_t error_code = 0;
        std::uint8_t flags = 0;
        std::uint64_t trace_id = 0;
        std::string body;
    };

    using PacketHandler =
        std::function<void(const std::shared_ptr<Session>&, PacketMessage)>;
    using CloseHandler = std::function<void(const std::shared_ptr<Session>&, const error_code&)>;
    using PacketObserver =
        std::function<void(const std::shared_ptr<Session>&, const PacketMessage&)>;

    explicit Session(tcp::socket socket, SessionOptions options = {});

    void start();
    void send(std::uint16_t message_id,
              std::uint32_t request_id,
              std::int32_t error_code,
              std::string body,
              std::uint8_t flags = 0,
              bool high_priority = false);
    void send_batch(std::vector<PacketMessage> messages);
    void stop();

    [[nodiscard]] std::size_t pending_write_bytes() const;
    [[nodiscard]] std::size_t pending_write_count() const;
    [[nodiscard]] std::size_t backpressure_activate_count() const { return backpressure_activate_count_; }
    [[nodiscard]] bool backpressure_active() const { return backpressure_active_; }

    void set_packet_handler(PacketHandler handler);
    void set_close_handler(CloseHandler handler);
    void set_receive_observer(PacketObserver observer);
    void set_send_observer(PacketObserver observer);

    std::string remote_endpoint() const;
    tcp::socket& socket();

private:
    enum class ReadPhase : std::uint8_t {
        kIdle,
        kHeader,
        kBody,
    };

    void do_read_header();
    void do_read_body();
    void enqueue_write(PacketMessage message, bool high_priority = false);
    void do_write();
    void handle_close(const error_code& ec);
    void arm_heartbeat_timer();
    void touch_activity();

    struct PendingWrite {
        PacketMessage message;
        std::string packet;
        bool high_priority = false;
    };

    tcp::socket socket_;
    asio::strand<asio::any_io_executor> strand_;
    asio::steady_timer heartbeat_timer_;
    std::array<unsigned char, 4> read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
    std::deque<PendingWrite> write_queue_;
    std::size_t queued_write_bytes_ = 0;
    std::size_t peak_write_bytes_ = 0;
    PacketHandler packet_handler_;
    CloseHandler close_handler_;
    PacketObserver receive_observer_;
    PacketObserver send_observer_;
    void check_backpressure();
    void resume_if_paused();

    std::chrono::steady_clock::time_point last_activity_at_;
    ReadPhase read_phase_ = ReadPhase::kIdle;
    bool started_ = false;
    bool stopped_ = false;
    bool backpressure_active_ = false;
    std::size_t backpressure_activate_count_ = 0;
    SessionOptions options_;
};

}  // namespace net
