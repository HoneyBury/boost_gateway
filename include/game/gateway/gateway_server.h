#pragma once

#include "net/message_dispatcher.h"
#include "net/session.h"

#include <boost/asio.hpp>

#include <cstdint>
#include <map>
#include <memory>

namespace game::gateway {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;
using error_code = boost::system::error_code;

class GatewayServer {
public:
    GatewayServer(asio::io_context& io_context,
                  net::MessageDispatcher& dispatcher,
                  std::uint16_t port,
                  net::SessionOptions session_options = {});

    void start();
    void stop();
    [[nodiscard]] std::uint16_t local_port() const;

private:
    void do_accept();

    asio::io_context& io_context_;
    net::MessageDispatcher& dispatcher_;
    tcp::acceptor acceptor_;
    std::map<const net::Session*, std::shared_ptr<net::Session>> sessions_;
    net::SessionOptions session_options_;
};

}  // namespace game::gateway
