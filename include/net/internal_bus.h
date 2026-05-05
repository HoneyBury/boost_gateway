#pragma once

#include "net/message_dispatcher.h"
#include "net/packet_codec.h"
#include "net/service_registry.h"
#include "net/service_router.h"

#include <boost/asio.hpp>

#include <deque>
#include <memory>
#include <string>
#include <vector>

namespace net {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

class InternalBus : public std::enable_shared_from_this<InternalBus> {
public:
    InternalBus(asio::io_context& io, ServiceRegistry& registry, MessageDispatcher& dispatcher)
        : io_(io), registry_(registry), dispatcher_(dispatcher) {}

    // Connect to all registered backend services for a given service type
    void connect_to_service(ServiceId service_id) {
        for (const auto& inst : registry_.healthy_instances(service_id)) {
            auto connector = std::make_shared<BackendConnector>(io_, inst.host, inst.port,
                                                                service_id, dispatcher_);
            connector->start();
            connectors_.push_back(connector);
        }
    }

    void stop_all() {
        for (auto& c : connectors_) c->stop();
        connectors_.clear();
    }

private:
    struct BackendConnector : std::enable_shared_from_this<BackendConnector> {
        BackendConnector(asio::io_context& io, std::string host, std::uint16_t port,
                        ServiceId sid, MessageDispatcher& disp)
            : resolver_(io), socket_(io), host_(std::move(host)),
              port_(std::to_string(port)), service_id_(sid), dispatcher_(disp) {}

        void start() {
            auto self = shared_from_this();
            resolver_.async_resolve(host_, port_,
                [self](boost::system::error_code ec, tcp::resolver::results_type eps) {
                    if (!ec) self->do_connect(*eps.begin());
                });
        }

        void stop() {
            boost::system::error_code ec;
            socket_.close(ec);
        }

        void send(std::uint16_t msg_id, std::uint32_t req_id, std::int32_t err, std::string body) {
            auto pkt = packet::encode(msg_id, req_id, err, body);
            bool writing = !write_queue_.empty();
            write_queue_.push_back(std::move(pkt));
            if (!writing) do_write();
        }

    private:
        void do_connect(const tcp::endpoint& ep) {
            auto self = shared_from_this();
            socket_.async_connect(ep, [self](boost::system::error_code ec) {
                if (!ec) self->do_read_header();
            });
        }

        void do_read_header() {
            auto self = shared_from_this();
            asio::async_read(socket_, asio::buffer(read_header_),
                [self](boost::system::error_code ec, std::size_t) {
                    if (ec) return;
                    auto len = packet::decode_length(self->read_header_);
                    self->read_body_.assign(len, '\0');
                    self->do_read_body(len);
                });
        }

        void do_read_body(std::uint32_t len) {
            auto self = shared_from_this();
            asio::async_read(socket_, asio::buffer(read_body_, len),
                [self](boost::system::error_code ec, std::size_t) {
                    if (ec) return;
                    auto decoded = packet::decode_payload(self->read_body_);
                    self->dispatcher_.dispatch(nullptr, decoded.message_id, decoded.request_id,
                                               decoded.error_code, std::move(decoded.body));
                    self->do_read_header();
                });
        }

        void do_write() {
            auto self = shared_from_this();
            asio::async_write(socket_, asio::buffer(write_queue_.front()),
                [self](boost::system::error_code ec, std::size_t) {
                    if (ec) return;
                    self->write_queue_.pop_front();
                    if (!self->write_queue_.empty()) self->do_write();
                });
        }

        tcp::resolver resolver_;
        tcp::socket socket_;
        std::string host_;
        std::string port_;
        ServiceId service_id_;
        MessageDispatcher& dispatcher_;
        std::array<unsigned char, 4> read_header_{};
        std::vector<char> read_body_;
        std::deque<std::string> write_queue_;
    };

    asio::io_context& io_;
    ServiceRegistry& registry_;
    MessageDispatcher& dispatcher_;
    std::vector<std::shared_ptr<BackendConnector>> connectors_;
};

}  // namespace net
