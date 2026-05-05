#pragma once

#include <boost/asio/ip/tcp.hpp>

#include <cstdint>
#include <functional>
#include <string>

namespace net {

struct HttpMetricsSnapshot {
    std::string prometheus_text;
    std::string json_text;
};

class HttpManager {
public:
    using MetricsProvider = std::function<HttpMetricsSnapshot()>;
    using HealthProvider = std::function<bool()>;

    HttpManager(boost::asio::any_io_executor ex, std::uint16_t port);
    ~HttpManager();

    HttpManager(const HttpManager&) = delete;
    HttpManager& operator=(const HttpManager&) = delete;

    void set_metrics_provider(MetricsProvider provider);
    void start();
    void stop();

private:
    void do_accept();

    boost::asio::ip::tcp::acceptor acceptor_;
    MetricsProvider metrics_provider_;
};

}  // namespace net
