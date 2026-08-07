#include "v2/io/io_engine.h"
#include "v2/runtime/actor_system.h"

#ifdef __unix__
#include <sys/socket.h>
#endif
#include <utility>

#include <spdlog/spdlog.h>

namespace v2::io {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

namespace {

thread_local std::optional<std::uint32_t> g_current_io_core_id;

class AsioIoSession final : public IoSession {
public:
    AsioIoSession(std::shared_ptr<net::Session> session,
                  std::uint32_t core_id,
                  AsioIoEngine* engine)
        : session_(std::move(session)),
          core_id_(core_id),
          engine_(engine) {
        if (engine_) {
            engine_->register_session(core_id_);
        }
    }

    ~AsioIoSession() override {
        if (engine_) {
            engine_->unregister_session(core_id_);
        }
    }

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
              std::uint8_t flags,
              bool high_priority) override {
        session_->send(message_id,
                       request_id,
                       error_code,
                       std::move(body),
                       flags,
                       high_priority);
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
    AsioIoEngine* engine_ = nullptr;
};

class AsioIoAcceptor final : public IoAcceptor {
public:
    AsioIoAcceptor(asio::io_context& io_context,
                   std::uint32_t core_id,
                   std::string address,
                   std::uint16_t port,
                   net::SessionOptions session_options,
                   AcceptPolicy accept_policy,
                   AsioIoEngine* engine,
                   bool reuse_port = false)
        : acceptor_(io_context),
          core_id_(core_id),
          session_options_(std::move(session_options)),
          accept_policy_(accept_policy),
          engine_(engine) {
        const auto endpoint = tcp::endpoint(asio::ip::make_address(address), port);
        acceptor_.open(endpoint.protocol());
        acceptor_.set_option(tcp::acceptor::reuse_address(true));
        if (reuse_port) {
#ifdef SO_REUSEPORT
            using reuse_port_opt = boost::asio::detail::socket_option::boolean<
                SOL_SOCKET, SO_REUSEPORT>;
            acceptor_.set_option(reuse_port_opt(true));
#endif
        }
        acceptor_.bind(endpoint);
        acceptor_.listen(boost::asio::socket_base::max_listen_connections);
    }

    void async_accept(std::function<void(std::unique_ptr<IoSession>)> on_accept) override {
        async_accept_native(
            [this, on_accept = std::move(on_accept)](std::shared_ptr<net::Session> session) mutable {
                if (!session) {
                    on_accept(nullptr);
                    return;
                }
                on_accept(std::make_unique<AsioIoSession>(std::move(session), core_id_, engine_));
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

    void close() override {
        boost::system::error_code ec;
        acceptor_.cancel(ec);
        acceptor_.close(ec);
    }

    [[nodiscard]] std::uint16_t local_port() const override {
        return acceptor_.local_endpoint().port();
    }

    [[nodiscard]] std::uint32_t owning_core_id() const noexcept override {
        return core_id_;
    }

    [[nodiscard]] AcceptPolicy accept_policy() const noexcept override {
        return accept_policy_;
    }

private:
    tcp::acceptor acceptor_;
    std::uint32_t core_id_ = 0;
    net::SessionOptions session_options_;
    AcceptPolicy accept_policy_ = AcceptPolicy::kRoundRobin;
    AsioIoEngine* engine_ = nullptr;
};

class MultiIoAcceptor final : public IoAcceptor {
public:
    MultiIoAcceptor(std::vector<std::unique_ptr<IoAcceptor>> acceptors,
                    AsioIoEngine* engine)
        : acceptors_(std::move(acceptors)), engine_(engine) {}

    void async_accept(
        std::function<void(std::unique_ptr<IoSession>)> on_accept) override {
        std::scoped_lock lock(mutex_);
        wrapped_accept_handler_ = std::move(on_accept);
        if (wrapped_accept_started_) {
            return;
        }
        wrapped_accept_started_ = true;
        arm_wrapped_acceptors_locked();
    }

    void async_accept_native(NativeSessionAcceptHandler on_accept) override {
        std::scoped_lock lock(mutex_);
        native_accept_handler_ = std::move(on_accept);
        if (native_accept_started_) {
            return;
        }
        native_accept_started_ = true;
        arm_native_acceptors_locked();
    }

    [[nodiscard]] std::uint16_t local_port() const override {
        for (const auto& a : acceptors_) {
            auto port = a->local_port();
            if (port != 0) return port;
        }
        return 0;
    }

    [[nodiscard]] std::uint32_t owning_core_id() const noexcept override {
        auto core_id = engine_->current_core_id();
        return core_id.has_value() ? *core_id : 0;
    }

    [[nodiscard]] AcceptPolicy accept_policy() const noexcept override {
        return acceptors_.empty() ? AcceptPolicy::kRoundRobin
                                  : acceptors_[0]->accept_policy();
    }

    [[nodiscard]] bool is_multi_core() const noexcept override { return true; }

    void close() override {
        std::scoped_lock lock(mutex_);
        closing_ = true;
        wrapped_accept_handler_ = nullptr;
        native_accept_handler_ = nullptr;
        for (auto& acceptor : acceptors_) {
            if (acceptor) {
                acceptor->close();
            }
        }
    }

private:
    void arm_wrapped_acceptor(std::size_t index) {
        auto loop = std::make_shared<std::function<void()>>();
        *loop = [this, index, loop]() {
            acceptors_[index]->async_accept(
                [this, loop](std::unique_ptr<IoSession> session) {
                    std::function<void(std::unique_ptr<IoSession>)> handler;
                    {
                        std::scoped_lock lock(mutex_);
                        if (closing_) {
                            return;
                        }
                        handler = wrapped_accept_handler_;
                    }
                    if (handler) {
                        handler(std::move(session));
                    }
                    {
                        std::scoped_lock lock(mutex_);
                        if (closing_) {
                            return;
                        }
                    }
                    (*loop)();
                });
        };
        (*loop)();
    }

    void arm_native_acceptor(std::size_t index) {
        auto loop = std::make_shared<std::function<void()>>();
        *loop = [this, index, loop]() {
            acceptors_[index]->async_accept_native(
                [this, loop](std::shared_ptr<net::Session> session) {
                    NativeSessionAcceptHandler handler;
                    {
                        std::scoped_lock lock(mutex_);
                        if (closing_) {
                            return;
                        }
                        handler = native_accept_handler_;
                    }
                    if (handler) {
                        handler(std::move(session));
                    }
                    {
                        std::scoped_lock lock(mutex_);
                        if (closing_) {
                            return;
                        }
                    }
                    (*loop)();
                });
        };
        (*loop)();
    }

    void arm_wrapped_acceptors_locked() {
        for (std::size_t index = 0; index < acceptors_.size(); ++index) {
            arm_wrapped_acceptor(index);
        }
    }

    void arm_native_acceptors_locked() {
        for (std::size_t index = 0; index < acceptors_.size(); ++index) {
            arm_native_acceptor(index);
        }
    }

    std::vector<std::unique_ptr<IoAcceptor>> acceptors_;
    AsioIoEngine* engine_ = nullptr;
    std::mutex mutex_;
    std::function<void(std::unique_ptr<IoSession>)> wrapped_accept_handler_;
    NativeSessionAcceptHandler native_accept_handler_;
    bool wrapped_accept_started_ = false;
    bool native_accept_started_ = false;
    bool closing_ = false;
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
    session_counts_ = std::make_unique<std::atomic<std::uint32_t>[]>(io_cores);
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

std::uint32_t AsioIoEngine::pick_core_for_listen(const IoListenOptions& options) noexcept {
    const auto n_cores = static_cast<std::uint32_t>(cores_.size());

    // If fixed_core_id is provided, honor it as kFixed regardless of explicit policy.
    if (options.fixed_core_id.has_value()) {
        return *options.fixed_core_id % n_cores;
    }

    if (options.accept_policy == AcceptPolicy::kLeastLoaded) {
        std::uint32_t best_core = 0;
        std::uint32_t lowest = std::numeric_limits<std::uint32_t>::max();
        for (std::uint32_t i = 0; i < n_cores; ++i) {
            const auto count = session_counts_[i].load(std::memory_order_relaxed);
            if (count < lowest) {
                lowest = count;
                best_core = i;
            }
        }
        return best_core;
    }

    // kRoundRobin and kFixed without core_id.
    return static_cast<std::uint32_t>(
        next_listen_core_.fetch_add(1, std::memory_order_relaxed) % n_cores);
}

std::unique_ptr<IoAcceptor> AsioIoEngine::listen(
    const char* address,
    std::uint16_t port,
    net::SessionOptions session_options,
    IoListenOptions options) {
    const auto addr_str =
        address == nullptr ? "0.0.0.0" : std::string(address);

    if (options.reuse_port) {
        std::vector<std::unique_ptr<IoAcceptor>> acceptors;
        acceptors.reserve(cores_.size());
        for (std::size_t i = 0; i < cores_.size(); ++i) {
            acceptors.push_back(std::make_unique<AsioIoAcceptor>(
                cores_[i]->io_context,
                static_cast<std::uint32_t>(i),
                addr_str,
                port,
                session_options,
                options.accept_policy,
                this,
                /*reuse_port=*/true));
        }
        return std::make_unique<MultiIoAcceptor>(std::move(acceptors), this);
    }

    const auto core_id = pick_core_for_listen(options);
    return std::make_unique<AsioIoAcceptor>(
        cores_[core_id]->io_context,
        core_id,
        addr_str,
        port,
        std::move(session_options),
        options.accept_policy,
        this);
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
    for (auto& core : cores_) {
        core->mailbox.close();
    }
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

void AsioIoEngine::register_session(std::uint32_t core_id) {
    if (core_id >= num_io_cores()) {
        return;
    }
    session_counts_[core_id].fetch_add(1, std::memory_order_relaxed);
}

void AsioIoEngine::unregister_session(std::uint32_t core_id) {
    if (core_id >= num_io_cores()) {
        return;
    }
    auto& count = session_counts_[core_id];
    std::uint32_t prev = count.load(std::memory_order_relaxed);
    while (prev > 0 && !count.compare_exchange_weak(prev, prev - 1,
                                                     std::memory_order_relaxed)) {
        // Retry.
    }
}

std::uint32_t AsioIoEngine::session_count(std::uint32_t core_id) const noexcept {
    if (core_id >= num_io_cores()) {
        return 0;
    }
    return session_counts_[core_id].load(std::memory_order_relaxed);
}

std::uint32_t AsioIoEngine::total_session_count() const noexcept {
    std::uint32_t total = 0;
    const auto n = num_io_cores();
    for (std::uint32_t i = 0; i < n; ++i) {
        total += session_counts_[i].load(std::memory_order_relaxed);
    }
    return total;
}

bool AsioIoEngine::post_mailbox(std::uint32_t core_id, v2::actor::Message&& message) {
    if (core_id >= cores_.size()) {
        const auto rejected =
            mailbox_rejected_invalid_core_.fetch_add(1, std::memory_order_relaxed) + 1;
        if ((rejected & (rejected - 1)) == 0) {
            SPDLOG_ERROR("Mailbox rejected message for invalid core {} (count={})", core_id, rejected);
        }
        return false;
    }
    const auto result = cores_[core_id]->mailbox.try_enqueue_result(std::move(message));
    switch (result) {
    case EnqueueResult::kSuccess:
        mailbox_accepted_.fetch_add(1, std::memory_order_relaxed);
        return true;
    case EnqueueResult::kFull:
        {
            const auto rejected = mailbox_rejected_full_.fetch_add(1, std::memory_order_relaxed) + 1;
            if ((rejected & (rejected - 1)) == 0) {
                SPDLOG_ERROR(
                    "Mailbox for core {} is full; cross-core message rejected (count={})",
                    core_id,
                    rejected);
            }
        }
        return false;
    case EnqueueResult::kClosed:
        {
            const auto rejected = mailbox_rejected_closed_.fetch_add(1, std::memory_order_relaxed) + 1;
            if ((rejected & (rejected - 1)) == 0) {
                SPDLOG_ERROR(
                    "Mailbox for core {} is closed; cross-core message rejected (count={})",
                    core_id,
                    rejected);
            }
        }
        return false;
    }
    return false;
}

std::vector<v2::actor::Message> AsioIoEngine::drain_mailbox(std::uint32_t core_id) {
    if (core_id >= cores_.size()) {
        return {};
    }
    return cores_[core_id]->mailbox.drain();
}

MailboxMetrics AsioIoEngine::mailbox_metrics() const noexcept {
    return {
        .accepted = mailbox_accepted_.load(std::memory_order_relaxed),
        .rejected_full = mailbox_rejected_full_.load(std::memory_order_relaxed),
        .rejected_closed = mailbox_rejected_closed_.load(std::memory_order_relaxed),
        .rejected_invalid_core = mailbox_rejected_invalid_core_.load(std::memory_order_relaxed),
    };
}

void AsioIoEngine::set_actor_system(v2::runtime::ActorSystem* actor_system) {
    actor_system_ = actor_system;
}

}  // namespace v2::io
