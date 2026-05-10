#pragma once

#include "net/session.h"

#include <boost/asio.hpp>

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <vector>

namespace v2::io {

struct IoListenOptions {
    std::optional<std::uint32_t> fixed_core_id;
};

class IoSession {
public:
    using PacketMessage = net::Session::PacketMessage;
    using PacketHandler = std::function<void(PacketMessage)>;
    using CloseHandler = std::function<void()>;

    virtual ~IoSession() = default;
    virtual void start() = 0;
    virtual void set_packet_handler(PacketHandler handler) = 0;
    virtual void set_close_handler(CloseHandler handler) = 0;
    virtual void send(std::uint16_t message_id,
                      std::uint32_t request_id,
                      std::int32_t error_code,
                      std::string body,
                      std::uint8_t flags = 0) = 0;
    [[nodiscard]] virtual std::uint32_t owning_core_id() const noexcept = 0;
    virtual void send(const void* data, std::size_t len) = 0;
    virtual void close() = 0;
};

class IoAcceptor {
public:
    using NativeSessionAcceptHandler = std::function<void(std::shared_ptr<net::Session>)>;

    virtual ~IoAcceptor() = default;
    virtual void async_accept(
        std::function<void(std::unique_ptr<IoSession>)> on_accept) = 0;
    virtual void async_accept_native(NativeSessionAcceptHandler on_accept) = 0;
    [[nodiscard]] virtual std::uint16_t local_port() const = 0;
    [[nodiscard]] virtual std::uint32_t owning_core_id() const noexcept = 0;
};

class IoEngine {
public:
    virtual ~IoEngine() = default;

    // One io_context per core, each run by a dedicated thread.
    [[nodiscard]] virtual std::uint32_t num_io_cores() const noexcept = 0;

    // Strand-per-core: session dispatched to the core it was accepted on.
    virtual void dispatch_to_core(std::uint32_t core_id,
                                  std::function<void()> task) = 0;
    virtual void dispatch_to_all_cores(std::function<void(std::uint32_t core_id)> task) = 0;
    [[nodiscard]] virtual std::optional<std::uint32_t> current_core_id() const noexcept = 0;

    // Listen on address, calling on_accept on the accepting core.
    virtual std::unique_ptr<IoAcceptor> listen(
        const char* address,
        std::uint16_t port,
        net::SessionOptions session_options = {},
        IoListenOptions options = {}) = 0;

    // Run all io_contexts on their dedicated threads.
    virtual void run() = 0;
    virtual void stop() = 0;
};

class AsioIoEngine final : public IoEngine {
public:
    explicit AsioIoEngine(std::uint32_t io_cores = 1);
    ~AsioIoEngine() override;

    [[nodiscard]] std::uint32_t num_io_cores() const noexcept override;
    void dispatch_to_core(std::uint32_t core_id,
                          std::function<void()> task) override;
    void dispatch_to_all_cores(std::function<void(std::uint32_t core_id)> task) override;
    [[nodiscard]] std::optional<std::uint32_t> current_core_id() const noexcept override;
    std::unique_ptr<IoAcceptor> listen(
        const char* address,
        std::uint16_t port,
        net::SessionOptions session_options = {},
        IoListenOptions options = {}) override;
    void run() override;
    void stop() override;

private:
    struct Core {
        boost::asio::io_context io_context;
        boost::asio::executor_work_guard<boost::asio::io_context::executor_type> work_guard{
            boost::asio::make_work_guard(io_context)};
    };

    std::vector<std::unique_ptr<Core>> cores_;
    std::vector<std::thread> threads_;
    std::atomic<std::size_t> next_listen_core_{0};
    std::mutex run_mutex_;
    bool running_ = false;
};

}  // namespace v2::io
