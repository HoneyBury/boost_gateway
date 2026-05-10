#include "net/http_manager.h"

#include "app/logging.h"

#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/http/message.hpp>
#include <boost/beast/http/read.hpp>
#include <boost/beast/http/string_body.hpp>
#include <boost/beast/http/write.hpp>

#include <memory>

namespace net {

namespace beast = boost::beast;
namespace http = beast::http;
using tcp = boost::asio::ip::tcp;

namespace {

http::response<http::string_body> build_response(http::status status,
                                                  std::string_view content_type,
                                                  std::string body) {
    http::response<http::string_body> res{status, 11};
    res.set(http::field::server, "boost-gateway");
    res.set(http::field::content_type, content_type);
    res.keep_alive(false);
    res.body() = std::move(body);
    res.prepare_payload();
    return res;
}

}  // namespace

HttpManager::HttpManager(boost::asio::any_io_executor ex, std::uint16_t port)
    : acceptor_(ex, tcp::endpoint(tcp::v4(), port)) {}

HttpManager::~HttpManager() {
    stop();
}

void HttpManager::set_metrics_provider(MetricsProvider provider) {
    metrics_provider_ = std::move(provider);
}

void HttpManager::start() {
    do_accept();
    LOG_INFO("HTTP management endpoint listening on port {}",
             acceptor_.local_endpoint().port());
}

void HttpManager::stop() {
    boost::system::error_code ec;
    acceptor_.close(ec);
}

void HttpManager::do_accept() {
    auto sock = std::make_shared<tcp::socket>(acceptor_.get_executor());
    acceptor_.async_accept(*sock,
        [this, sock](boost::system::error_code ec) {
            if (!ec) {
                auto req = std::make_shared<http::request<http::string_body>>();
                auto buf = std::make_shared<beast::flat_buffer>();

                http::async_read(*sock, *buf, *req,
                    [this, sock, req, buf](boost::system::error_code ec, std::size_t) {
                        if (ec) {
                            LOG_DEBUG("HTTP read error: {}", ec.message());
                            return;
                        }

                        http::response<http::string_body> res;

                        if (req->method() != http::verb::get) {
                            res = build_response(http::status::method_not_allowed,
                                                  "text/plain", "Method Not Allowed");
                        } else if (req->target() == "/health") {
                            res = build_response(http::status::ok,
                                                  "application/json", R"({"status":"ok"})");
                        } else if (req->target() == "/metrics") {
                            auto body = metrics_provider_ ? metrics_provider_().prometheus_text : "";
                            res = build_response(http::status::ok, "text/plain", std::move(body));
                        } else if (req->target() == "/metrics/json") {
                            auto body = metrics_provider_ ? metrics_provider_().json_text : "";
                            res = build_response(http::status::ok, "application/json", std::move(body));
                        } else if (req->target() == "/metrics/diagnostics") {
                            auto body = metrics_provider_ ? metrics_provider_().diagnostics_text : "";
                            res = build_response(http::status::ok, "text/plain", std::move(body));
                        } else if (req->target() == "/metrics/diagnostics/json") {
                            auto body = metrics_provider_ ? metrics_provider_().diagnostics_json_text : "";
                            res = build_response(http::status::ok, "application/json", std::move(body));
                        } else {
                            res = build_response(http::status::not_found,
                                                  "text/plain", "Not Found");
                        }

                        auto sp = std::make_shared<http::response<http::string_body>>(std::move(res));
                        http::async_write(*sock, *sp,
                            [sock, sp](boost::system::error_code ec, std::size_t) {
                                if (ec) {
                                    LOG_DEBUG("HTTP write error: {}", ec.message());
                                }
                            });
                    });
            }

            if (acceptor_.is_open()) {
                do_accept();
            }
        });
}

}  // namespace net
