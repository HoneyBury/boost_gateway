#pragma once

#include <boost/asio/ip/tcp.hpp>

#include <cstdint>
#include <functional>
#include <string>

namespace net {

// L2 HTTP 管理面（/health、/metrics*）；与 ingress / 二进制 admin 的分层见 docs/v1-governance-layers.md。

struct HttpMetricsSnapshot {
    std::string prometheus_text;
    std::string json_text;
    std::string diagnostics_text;
    std::string diagnostics_json_text;
};

class HttpManager {
public:
    using MetricsProvider = std::function<HttpMetricsSnapshot()>;
    using HealthProvider = std::function<std::string()>;
    using ReadyProvider = std::function<std::string()>;

    HttpManager(boost::asio::any_io_executor ex, std::uint16_t port);
    ~HttpManager();

    HttpManager(const HttpManager&) = delete;
    HttpManager& operator=(const HttpManager&) = delete;

    void set_metrics_provider(MetricsProvider provider);
    void set_health_provider(HealthProvider provider);
    void set_ready_provider(ReadyProvider provider);
    void start();
    void stop();
    [[nodiscard]] std::uint16_t local_port() const;

private:
    void do_accept();

    boost::asio::ip::tcp::acceptor acceptor_;
    MetricsProvider metrics_provider_;
    HealthProvider health_provider_;
    ReadyProvider ready_provider_;
};

}  // namespace net
