#pragma once

#include <boost/asio.hpp>
#include <boost/system/error_code.hpp>

#include <array>
#include <deque>
#include <functional>
#include <memory>
#include <string>

namespace net {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;
using error_code = boost::system::error_code;

class Session : public std::enable_shared_from_this<Session> {
public:
    using MessageHandler = std::function<void(const std::shared_ptr<Session>&, std::string)>;
    using CloseHandler = std::function<void(const std::shared_ptr<Session>&, const error_code&)>;

    explicit Session(tcp::socket socket);

    void start();
    void send(std::string message);
    void stop();

    void set_message_handler(MessageHandler handler);
    void set_close_handler(CloseHandler handler);

    std::string remote_endpoint() const;
    tcp::socket& socket();

private:
    void do_read();
    void enqueue_write(std::string message);
    void do_write();
    void handle_close(const error_code& ec);

    tcp::socket socket_;
    asio::strand<asio::any_io_executor> strand_;
    std::array<char, 1024> read_buffer_{};
    std::deque<std::string> write_queue_;
    MessageHandler message_handler_;
    CloseHandler close_handler_;
    bool stopped_ = false;
};

}  // namespace net
