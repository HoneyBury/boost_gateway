#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/session.h"

#include <boost/asio.hpp>
#include <gtest/gtest.h>

#include <chrono>
#include <future>
#include <memory>
#include <string>
#include <thread>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

TEST(NetSessionTest, StartIsIdempotentAndKeepsFrameAlignment) {
    app::logging::init("project_tests");

    asio::io_context server_io;
    tcp::acceptor acceptor(server_io, tcp::endpoint(tcp::v4(), 0));
    std::promise<std::shared_ptr<net::Session>> accepted_promise;
    std::promise<std::string> packet_promise;

    acceptor.async_accept([&](const boost::system::error_code& ec, tcp::socket socket) {
        if (ec) {
            accepted_promise.set_exception(
                std::make_exception_ptr(boost::system::system_error(ec)));
            return;
        }
        auto session = std::make_shared<net::Session>(std::move(socket));
        session->set_packet_handler(
            [&](const std::shared_ptr<net::Session>&, net::Session::PacketMessage message) {
                packet_promise.set_value(std::move(message.body));
            });
        session->start();
        session->start();
        accepted_promise.set_value(std::move(session));
    });

    std::thread server_thread([&]() { server_io.run(); });
    asio::io_context client_io;
    tcp::socket client(client_io);

    try {
        client.connect(
            tcp::endpoint(asio::ip::make_address("127.0.0.1"), acceptor.local_endpoint().port()));
        auto session = accepted_promise.get_future().get();
        const auto packet = net::packet::encode(2001, 1, 0, "frame-aligned");
        asio::write(client, asio::buffer(packet));

        auto packet_future = packet_promise.get_future();
        const auto packet_status = packet_future.wait_for(std::chrono::seconds(2));
        std::string packet_body;
        if (packet_status == std::future_status::ready) {
            packet_body = packet_future.get();
        }
        session->stop();

        EXPECT_EQ(packet_status, std::future_status::ready);
        if (packet_status == std::future_status::ready) {
            EXPECT_EQ(packet_body, "frame-aligned");
        }
    } catch (...) {
        server_io.stop();
        if (server_thread.joinable()) {
            server_thread.join();
        }
        throw;
    }

    boost::system::error_code ignored;
    client.close(ignored);
    server_io.stop();
    if (server_thread.joinable()) {
        server_thread.join();
    }
}

} // namespace
