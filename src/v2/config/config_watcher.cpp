#include "v2/config/config_watcher.h"

#include <utility>

namespace v2::config {

ConfigWatcher::ConfigWatcher(std::filesystem::path path,
                             ReloadCallback on_reload)
    : path_(std::move(path)), on_reload_(std::move(on_reload)) {}

ConfigWatcher::~ConfigWatcher() { stop(); }

void ConfigWatcher::start(std::chrono::milliseconds interval) {
    if (running_) return;
    running_ = true;
    interval_ = interval;
    boost::system::error_code ec;
    last_write_ = std::filesystem::last_write_time(path_, ec);
    if (ec) {
        last_write_ = std::filesystem::file_time_type::min();
    }

    io_ = std::make_unique<boost::asio::io_context>();
    timer_ = std::make_unique<boost::asio::steady_timer>(io_->get_executor());

    arm_timer();

    thread_ = std::make_unique<std::thread>([this]() {
        io_->run();
    });
}

void ConfigWatcher::stop() {
    if (!running_) return;
    running_ = false;

    if (timer_) {
        timer_->cancel();
    }
    if (io_) {
        io_->stop();
    }
    if (thread_ && thread_->joinable()) {
        thread_->join();
    }
    thread_.reset();
    timer_.reset();
    io_.reset();
}

void ConfigWatcher::arm_timer() {
    if (!running_ || !timer_) return;
    timer_->expires_after(interval_);
    timer_->async_wait([this](boost::system::error_code ec) {
        if (ec) return;
        check_and_reload();
        arm_timer();
    });
}

void ConfigWatcher::check_and_reload() {
    boost::system::error_code ec;
    const auto current = std::filesystem::last_write_time(path_, ec);
    if (ec) return;

    if (current > last_write_) {
        last_write_ = current;
        if (on_reload_) on_reload_();
    }
}

}  // namespace v2::config
