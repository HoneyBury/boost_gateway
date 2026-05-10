#include "v2/io/io_engine.h"

#include <utility>

namespace v2::io {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

namespace {

thread_local std::optional<std::uint32_t> g_current_io_core_id;

class AsioIoSession final : public IoSession {
public:
    AsioIoSession(std::shared_ptr<net::Session> session, std::uint32_t core_id)
        : session_(std::move(session)),
          core_id_(core_id) {}

    void start() override {
        session_->start();
    }

    void set_packet_handler(PacketHandler handler) override {
        session_->set_packet_handler(
            [handler = std::move(handler)](const std::shared_ptr<net::Session>&,
                                           net::Session::PacketMessage message) mutable {
                if (handler) {
                    handler(std::move(message));
                }
            });
    }

    void set_close_handler(CloseHandler handler) override {
        session_->set_close_handler(
            [handler = std::move(handler)](const std::shared_ptr<net::Session>&,
                                           const net::error_code&) mutable {
                if (handler) {
                    handler();
                }
            });
    }

    void send(std::uint16_t message_id,
              std::uint32_t request_id,
              std::int32_t error_code,
              std::string body,
              std::uint8_t flags) override {
        session_->send(message_id, request_id, error_code, std::move(body), flags);
    }

    [[nodiscard]] std::uint32_t owning_core_id() const noexcept override {
        return core_id_;
    }

    void send(const void* data, std::size_t len) override {
        session_->send(0, 0, 0, std::string(static_cast<const char*>(data), len));
    }

    void close() override {
        session_->stop();
    }

private:
    std::shared_ptr<net::Session> session_;
    std::uint32_t core_id_ = 0;
};

class AsioIoAcceptor final : public IoAcceptor {
public:
    AsioIoAcceptor(asio::io_context& io_context,
                   std::uint32_t core_id,
                   std::string address,
                   std::uint16_t port,
                   net::SessionOptions session_options)
        : acceptor_(io_context),
          core_id_(core_id),
          session_options_(std::move(session_options)) {
        const auto endpoint = tcp::endpoint(asio::ip::make_address(address), port);
        acceptor_.open(endpoint.protocol());
        acceptor_.set_option(tcp::acceptor::reuse_address(true));
        acceptor_.bind(endpoint);
        acceptor_.listen();
    }

    void async_accept(std::function<void(std::unique_ptr<IoSession>)> on_accept) override {
        async_accept_native(
            [this, on_accept = std::move(on_accept)](std::shared_ptr<net::Session> session) mutable {
                if (!session) {
                    on_accept(nullptr);
                    return;
                }
                on_accept(std::make_unique<AsioIoSession>(std::move(session), core_id_));
            });
    }

    void async_accept_native(NativeSessionAcceptHandler on_accept) override {
        acceptor_.async_accept(
            [this, on_accept = std::move(on_accept)](const boost::system::error_code& ec, tcp::socket socket) mutable {
                if (ec) {
                    on_accept(nullptr);
                    return;
                }
                on_accept(std::make_shared<net::Session>(std::move(socket), session_options_));
            });
    }

    [[nodiscard]] std::uint16_t local_port() const override {
        return acceptor_.local_endpoint().port();
    }

    [[nodiscard]] std::uint32_t owning_core_id() const noexcept override {
        return core_id_;
    }

private:
    tcp::acceptor acceptor_;
    std::uint32_t core_id_ = 0;
    net::SessionOptions session_options_;
};

}  // namespace

AsioIoEngine::AsioIoEngine(std::uint32_t io_cores) {
    if (io_cores == 0) {
        io_cores = 1;
    }
    cores_.reserve(io_cores);
    for (std::uint32_t i = 0; i < io_cores; ++i) {
        cores_.push_back(std::make_unique<Core>());
    }
}

AsioIoEngine::~AsioIoEngine() {
    stop();
}

std::uint32_t AsioIoEngine::num_io_cores() const noexcept {
    return static_cast<std::uint32_t>(cores_.size());
}

void AsioIoEngine::dispatch_to_core(std::uint32_t core_id,
                                    std::function<void()> task) {
    if (cores_.empty() || !task) {
        return;
    }
    const auto index = static_cast<std::size_t>(core_id) % cores_.size();
    asio::post(cores_[index]->io_context, std::move(task));
}

void AsioIoEngine::dispatch_to_all_cores(std::function<void(std::uint32_t core_id)> task) {
    if (cores_.empty() || !task) {
        return;
    }

    for (std::size_t index = 0; index < cores_.size(); ++index) {
        asio::post(
            cores_[index]->io_context,
            [task, index]() mutable {
                task(static_cast<std::uint32_t>(index));
            });
    }
}

std::optional<std::uint32_t> AsioIoEngine::current_core_id() const noexcept {
    return g_current_io_core_id;
}

std::unique_ptr<IoAcceptor> AsioIoEngine::listen(
    const char* address,
    std::uint16_t port,
    net::SessionOptions session_options,
    IoListenOptions options) {
    const auto index = options.fixed_core_id.has_value()
        ? static_cast<std::size_t>(*options.fixed_core_id) % cores_.size()
        : next_listen_core_.fetch_add(1, std::memory_order_relaxed) % cores_.size();
    return std::make_unique<AsioIoAcceptor>(
        cores_[index]->io_context,
        static_cast<std::uint32_t>(index),
        address == nullptr ? "0.0.0.0" : std::string(address),
        port,
        std::move(session_options));
}

void AsioIoEngine::run() {
    std::scoped_lock lock(run_mutex_);
    if (running_) {
        return;
    }
    running_ = true;
    threads_.reserve(cores_.size());
    for (std::size_t index = 0; index < cores_.size(); ++index) {
        auto* io_context = &cores_[index]->io_context;
        threads_.emplace_back([io_context, index]() {
            g_current_io_core_id = static_cast<std::uint32_t>(index);
            io_context->run();
            g_current_io_core_id.reset();
        });
    }
}

void AsioIoEngine::stop() {
    std::scoped_lock lock(run_mutex_);
    if (!running_ && threads_.empty()) {
        return;
    }
    for (auto& core : cores_) {
        core->work_guard.reset();
        core->io_context.stop();
    }
    for (auto& thread : threads_) {
        if (thread.joinable()) {
            thread.join();
        }
    }
    threads_.clear();
    running_ = false;
}

}  // namespace v2::io
