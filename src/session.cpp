#include "net/session.h"

#include "app/logging.h"

#include <utility>

namespace net {

Session::Session(tcp::socket socket)
    : socket_(std::move(socket)),
      // strand_ 保证同一个 Session 的回调按顺序串行执行。
      strand_(asio::make_strand(socket_.get_executor())) {}

void Session::start() {
    // start() 是连接建立后的入口，职责是挂起第一次异步读。
    do_read();
}

void Session::send(std::string message) {
    // send() 对外暴露发送入口，允许业务层把消息交给 Session 排队发送。
    enqueue_write(std::move(message));
}

void Session::stop() {
    auto self = shared_from_this();  // 保证关闭动作执行期间对象不会被释放。

    asio::post(strand_, [self]() {
        if (self->stopped_) {
            return;
        }

        self->stopped_ = true;

        error_code ignored_ec;
        self->socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        self->socket_.close(ignored_ec);
    });
}

void Session::set_message_handler(MessageHandler handler) {
    // 设置业务层的消息处理回调，通常在 accept 成功后、start 前完成。
    message_handler_ = std::move(handler);
}

void Session::set_close_handler(CloseHandler handler) {
    // 设置关闭回调，便于上层在断线时清理 Session 容器。
    close_handler_ = std::move(handler);
}

std::string Session::remote_endpoint() const {
    error_code ec;
    const auto endpoint = socket_.remote_endpoint(ec);
    if (ec) {
        return "<unknown>";
    }

    return endpoint.address().to_string() + ":" + std::to_string(endpoint.port());
}

tcp::socket& Session::socket() {
    return socket_;
}

void Session::do_read() {
    auto self = shared_from_this();  // 保证本次异步读完成前 Session 一直存活。

    socket_.async_read_some(
        asio::buffer(read_buffer_),
        asio::bind_executor(strand_,
                            [self](const error_code& ec, std::size_t bytes_transferred) {
                                if (ec) {
                                    self->handle_close(ec);
                                    return;
                                }

                                std::string message(self->read_buffer_.data(), bytes_transferred);
                                LOG_INFO("Session {} received: {}", self->remote_endpoint(), message);

                                if (self->message_handler_) {
                                    self->message_handler_(self, std::move(message));
                                }

                                if (!self->stopped_) {
                                    self->do_read();
                                }
                            }));
}

void Session::enqueue_write(std::string message) {
    auto self = shared_from_this();  // 保证排队和启动异步写时对象有效。

    asio::post(strand_, [self, message = std::move(message)]() mutable {
        if (self->stopped_) {
            return;
        }

        const bool write_in_progress = !self->write_queue_.empty();
        self->write_queue_.push_back(std::move(message));

        if (!write_in_progress) {
            self->do_write();
        }
    });
}

void Session::do_write() {
    auto self = shared_from_this();  // 保证当前异步写完成前对象不会被销毁。

    asio::async_write(
        socket_,
        asio::buffer(write_queue_.front()),
        asio::bind_executor(strand_,
                            [self](const error_code& ec, std::size_t bytes_transferred) {
                                if (ec) {
                                    self->handle_close(ec);
                                    return;
                                }

                                LOG_DEBUG("Session {} sent {} bytes",
                                          self->remote_endpoint(),
                                          bytes_transferred);

                                self->write_queue_.pop_front();

                                if (!self->write_queue_.empty()) {
                                    self->do_write();
                                }
                            }));
}

void Session::handle_close(const error_code& ec) {
    // handle_close() 统一处理读写错误和对端断开，避免关闭逻辑散落在多个回调里。
    if (stopped_) {
        return;
    }

    stopped_ = true;

    LOG_INFO("Session {} closed: {}", remote_endpoint(), ec.message());

    error_code ignored_ec;
    socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
    socket_.close(ignored_ec);

    if (close_handler_) {
        close_handler_(shared_from_this(), ec);
    }
}

}  // namespace net
