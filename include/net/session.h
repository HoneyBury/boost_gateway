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

class Session : public std::enable_shared_from_this<Session> {
public:
    using PacketHandler =
        std::function<void(const std::shared_ptr<Session>&, std::uint16_t, std::string)>;
    using CloseHandler = std::function<void(const std::shared_ptr<Session>&, const error_code&)>;

    explicit Session(tcp::socket socket);

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
    std::string encode_packet(std::uint16_t message_id, const std::string& body) const;
    static std::uint32_t decode_length(const std::array<unsigned char, 4>& header);
    static std::uint16_t decode_message_id(const std::vector<char>& body);

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

    static constexpr std::uint32_t kMaxPacketSize = 1024 * 1024;
    static constexpr std::size_t kMaxPendingWriteBytes = 256 * 1024;
    static constexpr std::chrono::seconds kHeartbeatCheckInterval{5};
    static constexpr std::chrono::seconds kHeartbeatTimeout{30};
};

}  // namespace net
