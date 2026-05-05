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
    std::chrono::milliseconds heartbeat_check_interval{5000};
    std::chrono::milliseconds heartbeat_timeout{30000};
};

class Session : public std::enable_shared_from_this<Session> {
public:
    using PacketHandler =
        std::function<void(const std::shared_ptr<Session>&, std::uint16_t, std::string)>;
    using CloseHandler = std::function<void(const std::shared_ptr<Session>&, const error_code&)>;

    explicit Session(tcp::socket socket, SessionOptions options = {});

    void start();
    void send(std::uint16_t message_id, std::string body);
    void stop();

    void set_packet_handler(PacketHandler handler);
    void set_close_handler(CloseHandler handler);

    std::string remote_endpoint() const;
    tcp::socket& socket();

private:
    void do_read_header();
    void do_read_body();
    void enqueue_write(std::uint16_t message_id, std::string body);
    void do_write();
    void handle_close(const error_code& ec);
    void arm_heartbeat_timer();
    void touch_activity();

    tcp::socket socket_;
    asio::strand<asio::any_io_executor> strand_;
    asio::steady_timer heartbeat_timer_;
    std::array<unsigned char, 4> read_header_{};
    std::vector<char> read_body_;
    std::uint32_t expected_body_length_ = 0;
    std::deque<std::string> write_queue_;
    std::size_t queued_write_bytes_ = 0;
    PacketHandler packet_handler_;
    CloseHandler close_handler_;
    std::chrono::steady_clock::time_point last_activity_at_;
    bool stopped_ = false;
    SessionOptions options_;
};

}  // namespace net
