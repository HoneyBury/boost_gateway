#include "net/session.h"

#include "app/logging.h"
#include "net/buffer_pool.h"
#include "net/packet_codec.h"

#include <atomic>
#include <utility>

namespace net {

namespace {
std::atomic<std::uint64_t> g_trace_id_counter{1};
}  // namespace

Session::Session(tcp::socket socket, SessionOptions options)
    : socket_(std::move(socket)),
      // strand 保证同一个连接上的回调串行执行，避免读写状态并发竞争。
      strand_(asio::make_strand(socket_.get_executor())),
      // 心跳定时器和 socket 共用同一个执行器，保证关闭顺序一致。
      heartbeat_timer_(socket_.get_executor()),
      last_activity_at_(std::chrono::steady_clock::now()),
      options_(options) {}

void Session::start() {
    // 连接进入工作态后，同时启动读包状态机和心跳巡检。
    arm_heartbeat_timer();
    do_read_header();
}

void Session::send(std::uint16_t message_id,
                   std::uint32_t request_id,
                   std::int32_t error_code,
                   std::string body,
                   std::uint8_t flags) {
    enqueue_write(PacketMessage{
        .message_id = message_id,
        .request_id = request_id,
        .error_code = error_code,
        .flags = flags,
        .body = std::move(body),
    });
}

void Session::send_batch(std::vector<PacketMessage> messages) {
    if (messages.empty()) return;

    auto self = shared_from_this();
    asio::post(strand_, [self, messages = std::move(messages)]() mutable {
        if (self->stopped_) return;

        // Encode all messages into a single contiguous buffer
        std::string combined;
        for (const auto& msg : messages) {
            auto pkt = packet::encode(msg.message_id, msg.request_id, msg.error_code, msg.body, msg.flags);
            combined.append(pkt);
        }

        if (combined.size() > packet::kLengthHeaderSize + self->options_.max_packet_size) {
            LOG_WARN("Session {} batch too large: {} bytes", self->remote_endpoint(), combined.size());
            self->handle_close(asio::error::message_size);
            return;
        }

        const auto new_total = self->queued_write_bytes_ + combined.size();
        if (new_total > self->options_.max_pending_write_bytes) {
            LOG_WARN("Session {} write queue overflow on batch", self->remote_endpoint());
            self->handle_close(asio::error::no_buffer_space);
            return;
        }

        self->queued_write_bytes_ = new_total;
        if (self->queued_write_bytes_ > self->peak_write_bytes_) {
            self->peak_write_bytes_ = self->queued_write_bytes_;
        }

        const bool write_in_progress = !self->write_queue_.empty();
        self->write_queue_.push_back(PendingWrite{
            .message = messages.back(),
            .packet = std::move(combined),
        });

        self->check_backpressure();

        if (!write_in_progress) {
            self->do_write();
        }
    });
}

void Session::stop() {
    auto self = shared_from_this();
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
    packet_handler_ = std::move(handler);
}

void Session::set_close_handler(CloseHandler handler) {
    close_handler_ = std::move(handler);
}

void Session::set_receive_observer(PacketObserver observer) {
    receive_observer_ = std::move(observer);
}

void Session::set_send_observer(PacketObserver observer) {
    send_observer_ = std::move(observer);
}

std::string Session::remote_endpoint() const {
    error_code ec;
    const auto endpoint = socket_.remote_endpoint(ec);
    if (ec) {
        return "<unknown>";
    }

    return endpoint.address().to_string() + ":" + std::to_string(endpoint.port());
}

std::size_t Session::pending_write_bytes() const {
    return queued_write_bytes_;
}

std::size_t Session::pending_write_count() const {
    return write_queue_.size();
}

tcp::socket& Session::socket() {
    return socket_;
}

void Session::do_read_header() {
    auto self = shared_from_this();
    // async_read 会一直读取到 4 字节长度头完整到达，因此天然支持半包。
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
                                self->expected_body_length_ = packet::decode_length(self->read_header_);

                                if (self->expected_body_length_ < packet::kFixedMetadataSize) {
                                    LOG_WARN("Session {} invalid packet length {}",
                                             self->remote_endpoint(),
                                             self->expected_body_length_);
                                    self->handle_close(asio::error::invalid_argument);
                                    return;
                                }

                                if (self->expected_body_length_ > self->options_.max_packet_size) {
                                    LOG_WARN("Session {} packet too large: {} bytes",
                                             self->remote_endpoint(),
                                             self->expected_body_length_);
                                    self->handle_close(asio::error::message_size);
                                    return;
                                }

                                // Acquire from buffer pool to reduce allocation churn
                                auto pooled = BufferPool::instance().acquire_vector(self->expected_body_length_);
                                self->read_body_ = std::move(pooled);
                                self->read_body_.assign(self->expected_body_length_, '\0');
                                self->do_read_body();
                            }));
}

void Session::do_read_body() {
    auto self = shared_from_this();
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

                                try {
                                    auto packet = packet::decode_payload(self->read_body_);

                                    const auto trace_id = g_trace_id_counter.fetch_add(1, std::memory_order_relaxed);

                                    LOG_INFO("Session {} received message {} with {} bytes body [trace={}]",
                                             self->remote_endpoint(),
                                             packet.message_id,
                                             packet.body.size(),
                                             trace_id);

                                    const PacketMessage message{
                                        .message_id = packet.message_id,
                                        .request_id = packet.request_id,
                                        .error_code = packet.error_code,
                                        .flags = packet.flags,
                                        .trace_id = trace_id,
                                        .body = std::move(packet.body),
                                    };

                                    if (self->receive_observer_) {
                                        self->receive_observer_(self, message);
                                    }

                                    if (self->packet_handler_) {
                                        self->packet_handler_(self, message);
                                    }
                                } catch (const std::exception& ex) {
                                    LOG_WARN("Session {} decode failed: {}",
                                             self->remote_endpoint(),
                                             ex.what());
                                    self->handle_close(asio::error::invalid_argument);
                                    return;
                                }

                                // Release read buffer back to pool before next read
                                if (!self->read_body_.empty()) {
                                    BufferPool::instance().release_vector(std::move(self->read_body_));
                                }

                                if (!self->stopped_ && !self->backpressure_active_) {
                                    self->do_read_header();
                                } else if (self->backpressure_active_) {
                                    LOG_DEBUG("Session {} read paused (backpressure)", self->remote_endpoint());
                                }
                            }));
}

void Session::enqueue_write(PacketMessage message) {
    auto self = shared_from_this();
    asio::post(strand_, [self, message = std::move(message)]() mutable {
        if (self->stopped_) {
            return;
        }

        auto packet_bytes =
            packet::encode(message.message_id, message.request_id, message.error_code, message.body, message.flags);

        if (packet_bytes.size() > packet::kLengthHeaderSize + self->options_.max_packet_size) {
            LOG_WARN("Session {} outgoing packet too large: {} bytes",
                     self->remote_endpoint(),
                     packet_bytes.size());
            self->handle_close(asio::error::message_size);
            return;
        }

        const auto new_total = self->queued_write_bytes_ + packet_bytes.size();
        if (new_total > self->options_.max_pending_write_bytes) {
            LOG_WARN("Session {} write queue overflow: pending={}, incoming={}",
                     self->remote_endpoint(),
                     self->queued_write_bytes_,
                     packet_bytes.size());
            self->handle_close(asio::error::no_buffer_space);
            return;
        }

        // Slow connection detection: log warning when backlog exceeds half the limit
        if (new_total > self->options_.max_pending_write_bytes / 2) {
            LOG_WARN("Session {} slow connection: write backlog {} bytes (limit={})",
                     self->remote_endpoint(),
                     new_total,
                     self->options_.max_pending_write_bytes);
        }

        const bool write_in_progress = !self->write_queue_.empty();
        self->queued_write_bytes_ = new_total;
        if (self->queued_write_bytes_ > self->peak_write_bytes_) {
            self->peak_write_bytes_ = self->queued_write_bytes_;
        }
        self->write_queue_.push_back(PendingWrite{
            .message = std::move(message),
            .packet = std::move(packet_bytes),
        });

        self->check_backpressure();

        if (!write_in_progress) {
            self->do_write();
        }
    });
}

void Session::check_backpressure() {
    const auto high_water = options_.max_pending_write_bytes * 3 / 4;
    if (queued_write_bytes_ > high_water && !backpressure_active_) {
        backpressure_active_ = true;
        LOG_WARN("Session {} backpressure ON: write queue {} bytes", remote_endpoint(), queued_write_bytes_);
    }
}

void Session::resume_if_paused() {
    if (!backpressure_active_) return;
    const auto low_water = options_.max_pending_write_bytes / 4;
    if (queued_write_bytes_ <= low_water) {
        backpressure_active_ = false;
        LOG_INFO("Session {} backpressure OFF: write queue {} bytes", remote_endpoint(), queued_write_bytes_);
        if (!stopped_) do_read_header();
    }
}

void Session::do_write() {
    auto self = shared_from_this();
    // async_write 会持续发送直到整包落到内核发送缓冲区，再回调完成处理逻辑。
    asio::async_write(
        socket_,
        asio::buffer(write_queue_.front().packet),
        asio::bind_executor(strand_,
                            [self](const error_code& ec, std::size_t bytes_transferred) {
                                if (ec) {
                                    self->handle_close(ec);
                                    return;
                                }

                                self->touch_activity();

                                const auto sent_message = self->write_queue_.front().message;
                                self->queued_write_bytes_ -= self->write_queue_.front().packet.size();

                                LOG_DEBUG("Session {} sent {} bytes",
                                          self->remote_endpoint(),
                                          bytes_transferred);

                                if (self->send_observer_) {
                                    self->send_observer_(self, sent_message);
                                }

                                self->write_queue_.pop_front();
                                self->resume_if_paused();

                                if (!self->write_queue_.empty()) {
                                    self->do_write();
                                }
                            }));
}

void Session::handle_close(const error_code& ec) {
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
    auto self = shared_from_this();
    heartbeat_timer_.expires_after(options_.heartbeat_check_interval);
    heartbeat_timer_.async_wait(asio::bind_executor(
        strand_,
        [self](const error_code& ec) {
            if (ec || self->stopped_) {
                return;
            }

            const auto now = std::chrono::steady_clock::now();
            if (now - self->last_activity_at_ > self->options_.heartbeat_timeout) {
                LOG_WARN("Session {} heartbeat timeout", self->remote_endpoint());
                self->handle_close(asio::error::timed_out);
                return;
            }

            self->arm_heartbeat_timer();
        }));
}

void Session::touch_activity() {
    // 任意一次成功收发包都会刷新活跃时间，用于心跳超时判定。
    last_activity_at_ = std::chrono::steady_clock::now();
}

}  // namespace net
