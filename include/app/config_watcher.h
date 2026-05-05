#pragma once

#include "app/config.h"

#include <boost/asio/steady_timer.hpp>

#include <chrono>
#include <filesystem>
#include <functional>
#include <string>

namespace app::config {

class ConfigWatcher {
public:
    using ReloadCallback = std::function<void(const GatewayAppConfig& new_config)>;

    ConfigWatcher(boost::asio::any_io_executor ex,
                  std::filesystem::path path,
                  ReloadCallback on_reload)
        : timer_(ex), path_(std::move(path)), on_reload_(std::move(on_reload)) {}

    void start(std::chrono::seconds interval = std::chrono::seconds(5)) {
        interval_ = interval;
        last_write_ = std::filesystem::last_write_time(path_);
        arm_timer();
    }

    void stop() { timer_.cancel(); }

private:
    void arm_timer() {
        timer_.expires_after(interval_);
        timer_.async_wait([this](boost::system::error_code ec) {
            if (ec) return;
            check_and_reload();
            arm_timer();
        });
    }

    void check_and_reload() {
        boost::system::error_code ec;
        const auto current = std::filesystem::last_write_time(path_, ec);
        if (ec) return;

        if (current > last_write_) {
            last_write_ = current;
            auto new_config = load_gateway_config(path_);
            if (on_reload_) on_reload_(new_config);
        }
    }

    boost::asio::steady_timer timer_;
    std::filesystem::path path_;
    std::filesystem::file_time_type last_write_;
    std::chrono::seconds interval_{5};
    ReloadCallback on_reload_;
};

}  // namespace app::config
