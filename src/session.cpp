#include "net/session.h"

#include "app/logging.h"

#include <algorithm>
#include <utility>

namespace net {

Session::Session(tcp::socket socket)
    : socket_(std::move(socket)),
      // strand_ 保证同一个连接上的回调串行执行，避免状态并发竞争。
      strand_(asio::make_strand(socket_.get_executor())),
      // 心跳定时器和 socket 使用同一个执行器，确保时序和连接状态一致。
      heartbeat_timer_(socket_.get_executor()),
      last_activity_at_(std::chrono::steady_clock::now()) {}

void Session::start() {
    // start() 是连接正式进入工作态的入口，会同时启动心跳检测和收包状态机。
    arm_heartbeat_timer();
    do_read_header();
}

void Session::send(std::uint16_t message_id, std::string body) {
    // send() 是业务层的发包入口，负责把消息号和包体交给 Session 封包发送。
    enqueue_write(message_id, std::move(body));
}

void Session::stop() {
    auto self = shared_from_this();  // 保证关闭动作执行期间对象不会被提前释放。

    asio::post(strand_, [self]() {
        if (self->stopped_) {
            return;
        }

        self->stopped_ = true;
        self->heartbeat_timer_.cancel();

        error_code ignored_ec;
        self->socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        self->socket_.close(ignored_ec);
    });
}

void Session::set_packet_handler(PacketHandler handler) {
    // 上层在 Session 启动前注册消息处理器，之后拿到的永远是完整包。
    packet_handler_ = std::move(handler);
}

void Session::set_close_handler(CloseHandler handler) {
    // 上层通常在断线回调里清理 Session 容器和在线状态。
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

void Session::do_read_header() {
    auto self = shared_from_this();  // 保证 4 字节长度头读取完成前对象一直存活。

    // async_read 会持续读，直到长度头完整到达，因此天然支持半包。
    asio::async_read(
        socket_,
        asio::buffer(read_header_),
        asio::bind_executor(strand_,
                            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                                if (ec) {
                                    self->handle_close(ec);
                                    return;
                                }

                                self->touch_activity();
                                self->expected_body_length_ = decode_length(self->read_header_);

                                if (self->expected_body_length_ < sizeof(std::uint16_t)) {
                                    LOG_WARN("Session {} invalid packet length {}",
                                             self->remote_endpoint(),
                                             self->expected_body_length_);
                                    self->handle_close(asio::error::invalid_argument);
                                    return;
                                }

                                if (self->expected_body_length_ > kMaxPacketSize) {
                                    LOG_WARN("Session {} packet too large: {} bytes",
                                             self->remote_endpoint(),
                                             self->expected_body_length_);
                                    self->handle_close(asio::error::message_size);
                                    return;
                                }

                                self->read_body_.assign(self->expected_body_length_, '\0');
                                self->do_read_body();
                            }));
}

void Session::do_read_body() {
    auto self = shared_from_this();  // 保证完整包体读取结束前对象不会被释放。

    // async_read 会一直读满包体长度，因此天然支持粘包拆分。
    asio::async_read(
        socket_,
        asio::buffer(read_body_),
        asio::bind_executor(strand_,
                            [self](const error_code& ec, std::size_t /*bytes_transferred*/) {
                                if (ec) {
                                    self->handle_close(ec);
                                    return;
                                }

                                self->touch_activity();

                                const auto message_id = decode_message_id(self->read_body_);
                                std::string body(self->read_body_.begin() + static_cast<std::ptrdiff_t>(sizeof(std::uint16_t)),
                                                 self->read_body_.end());

                                LOG_INFO("Session {} received message {} with {} bytes body",
                                         self->remote_endpoint(),
                                         message_id,
                                         body.size());

                                if (self->packet_handler_) {
                                    self->packet_handler_(self, message_id, std::move(body));
                                }

                                if (!self->stopped_) {
                                    self->do_read_header();
                                }
                            }));
}

void Session::enqueue_write(std::uint16_t message_id, std::string body) {
    auto self = shared_from_this();  // 保证排队和启动异步写期间对象有效。

    asio::post(strand_,
               [self, message_id, body = std::move(body)]() mutable {
                   if (self->stopped_) {
                       return;
                   }

                   std::string packet = self->encode_packet(message_id, body);

                   if (packet.size() > kMaxPacketSize + sizeof(std::uint32_t)) {
                       LOG_WARN("Session {} outgoing packet too large: {} bytes",
                                self->remote_endpoint(),
                                packet.size());
                       self->handle_close(asio::error::message_size);
                       return;
                   }

                   if (self->queued_write_bytes_ + packet.size() > kMaxPendingWriteBytes) {
                       LOG_WARN("Session {} write queue overflow: pending={}, incoming={}",
                                self->remote_endpoint(),
                                self->queued_write_bytes_,
                                packet.size());
                       self->handle_close(asio::error::no_buffer_space);
                       return;
                   }

                   const bool write_in_progress = !self->write_queue_.empty();
                   self->queued_write_bytes_ += packet.size();
                   self->write_queue_.push_back(std::move(packet));

                   if (!write_in_progress) {
                       self->do_write();
                   }
               });
}

void Session::do_write() {
    auto self = shared_from_this();  // 保证当前异步写完成前对象不会被销毁。

    // async_write 会把完整封包写完，保证一个逻辑包不会只发一半就交回业务层。
    asio::async_write(
        socket_,
        asio::buffer(write_queue_.front()),
        asio::bind_executor(strand_,
                            [self](const error_code& ec, std::size_t bytes_transferred) {
                                if (ec) {
                                    self->handle_close(ec);
                                    return;
                                }

                                self->touch_activity();
                                self->queued_write_bytes_ -= self->write_queue_.front().size();

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
    // handle_close() 统一处理读写错误、心跳超时和主动关闭后的资源回收。
    if (stopped_) {
        return;
    }

    stopped_ = true;
    heartbeat_timer_.cancel();

    LOG_INFO("Session {} closed: {}", remote_endpoint(), ec.message());

    error_code ignored_ec;
    socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
    socket_.close(ignored_ec);

    if (close_handler_) {
        close_handler_(shared_from_this(), ec);
    }
}

void Session::arm_heartbeat_timer() {
    auto self = shared_from_this();  // 保证定时器回调触发时 Session 仍然有效。

    heartbeat_timer_.expires_after(kHeartbeatCheckInterval);
    heartbeat_timer_.async_wait(asio::bind_executor(
        strand_,
        [self](const error_code& ec) {
            if (ec || self->stopped_) {
                return;
            }

            const auto now = std::chrono::steady_clock::now();
            if (now - self->last_activity_at_ > kHeartbeatTimeout) {
                LOG_WARN("Session {} heartbeat timeout", self->remote_endpoint());
                self->handle_close(asio::error::timed_out);
                return;
            }

            self->arm_heartbeat_timer();
        }));
}

void Session::touch_activity() {
    // 任何成功的收发包都会刷新活跃时间，用于心跳超时判断。
    last_activity_at_ = std::chrono::steady_clock::now();
}

std::string Session::encode_packet(std::uint16_t message_id, const std::string& body) const {
    const auto body_length = static_cast<std::uint32_t>(sizeof(message_id) + body.size());

    std::string packet;
    packet.resize(sizeof(std::uint32_t) + body_length);

    packet[0] = static_cast<char>((body_length >> 24U) & 0xFFU);
    packet[1] = static_cast<char>((body_length >> 16U) & 0xFFU);
    packet[2] = static_cast<char>((body_length >> 8U) & 0xFFU);
    packet[3] = static_cast<char>(body_length & 0xFFU);

    packet[4] = static_cast<char>((message_id >> 8U) & 0xFFU);
    packet[5] = static_cast<char>(message_id & 0xFFU);

    if (!body.empty()) {
        std::copy(body.begin(), body.end(), packet.begin() + 6);
    }

    return packet;
}

std::uint32_t Session::decode_length(const std::array<unsigned char, 4>& header) {
    return (static_cast<std::uint32_t>(header[0]) << 24U) |
           (static_cast<std::uint32_t>(header[1]) << 16U) |
           (static_cast<std::uint32_t>(header[2]) << 8U) |
           static_cast<std::uint32_t>(header[3]);
}

std::uint16_t Session::decode_message_id(const std::vector<char>& body) {
    return (static_cast<std::uint16_t>(static_cast<unsigned char>(body[0])) << 8U) |
           static_cast<std::uint16_t>(static_cast<unsigned char>(body[1]));
}

}  // namespace net
