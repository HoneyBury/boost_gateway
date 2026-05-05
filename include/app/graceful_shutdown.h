#pragma once

#include <boost/asio/signal_set.hpp>

#include <chrono>
#include <functional>

namespace app {

class GracefulShutdown {
public:
    using ShutdownCallback = std::function<void()>;

    GracefulShutdown(boost::asio::any_io_executor ex, ShutdownCallback on_shutdown)
        : signals_(ex, SIGINT, SIGTERM), on_shutdown_(std::move(on_shutdown)) {}

    void start() {
        signals_.async_wait([this](boost::system::error_code ec, int sig) {
            if (ec) return;
            LOG_WARN("Received signal {}, initiating graceful shutdown", sig);
            if (on_shutdown_) on_shutdown_();
        });
    }

    void stop() { signals_.cancel(); }

private:
    boost::asio::signal_set signals_;
    ShutdownCallback on_shutdown_;
};

}  // namespace app
