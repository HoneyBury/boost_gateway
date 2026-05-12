#pragma once

#include <boost/asio/io_context.hpp>
#include <boost/asio/steady_timer.hpp>

#include <chrono>
#include <filesystem>
#include <functional>
#include <memory>
#include <thread>

namespace v2::config {

class ConfigWatcher {
public:
    using ReloadCallback = std::function<void()>;

    ConfigWatcher(std::filesystem::path path, ReloadCallback on_reload);
    ~ConfigWatcher();

    ConfigWatcher(const ConfigWatcher&) = delete;
    ConfigWatcher& operator=(const ConfigWatcher&) = delete;
    ConfigWatcher(ConfigWatcher&&) = delete;
    ConfigWatcher& operator=(ConfigWatcher&&) = delete;

    void start(std::chrono::milliseconds interval = std::chrono::milliseconds(5000));
    void stop();

private:
    void arm_timer();
    void check_and_reload();

    std::unique_ptr<boost::asio::io_context> io_;
    std::unique_ptr<boost::asio::steady_timer> timer_;
    std::unique_ptr<std::thread> thread_;
    std::filesystem::path path_;
    std::filesystem::file_time_type last_write_;
    std::chrono::milliseconds interval_{5000};
    ReloadCallback on_reload_;
    bool running_ = false;
};

}  // namespace v2::config
