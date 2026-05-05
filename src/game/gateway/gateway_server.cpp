#include "game/gateway/gateway_server.h"

#include "app/logging.h"

#include <utility>

namespace game::gateway {

GatewayServer::GatewayServer(asio::io_context& io_context,
                             net::MessageDispatcher& dispatcher,
                             std::uint16_t port,
                             net::SessionOptions session_options)
    : io_context_(io_context),
      dispatcher_(dispatcher),
      acceptor_(io_context, tcp::endpoint(tcp::v4(), port)),
      session_options_(std::move(session_options)) {}

void GatewayServer::start() {
    LOG_INFO("Gateway server listening on 0.0.0.0:{}", acceptor_.local_endpoint().port());
    do_accept();
}

void GatewayServer::stop() {
    error_code ignored_ec;
    acceptor_.close(ignored_ec);

    for (auto& [_, session] : sessions_) {
        session->stop();
    }
    sessions_.clear();
}

std::uint16_t GatewayServer::local_port() const {
    return acceptor_.local_endpoint().port();
}

void GatewayServer::do_accept() {
    acceptor_.async_accept([this](const error_code& ec, tcp::socket socket) {
        if (ec) {
            if (ec != asio::error::operation_aborted) {
                LOG_ERROR("Accept failed: {}", ec.message());
            }
            return;
        }

        auto session = std::make_shared<net::Session>(std::move(socket), session_options_);
        const auto key = session.get();

        LOG_INFO("Accepted client {}", session->remote_endpoint());

        session->set_packet_handler(
            [this](const std::shared_ptr<net::Session>& session_ptr,
                   std::uint16_t message_id,
                   std::string body) {
                // Session 只负责拿到完整包，业务路由交给消息分发器和业务线程池。
                dispatcher_.dispatch(session_ptr, message_id, std::move(body));
            });

        session->set_close_handler(
            [this, key](const std::shared_ptr<net::Session>&, const error_code&) {
                sessions_.erase(key);
            });

        sessions_.emplace(key, session);
        session->start();

        do_accept();
    });
}

}  // namespace game::gateway
