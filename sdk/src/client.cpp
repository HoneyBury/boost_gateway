// BoostGateway SDK: SdkClient implementation.
// Provides high-level game operations over the BoostGateway protocol.

#include "boost_gateway/sdk/client.h"

#include "net/packet_codec.h"
#include "net/protocol.h"

#include <boost/asio.hpp>

#include <atomic>
#include <chrono>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace boost_gateway {
namespace sdk {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

// ── TcpConnection (internal) ──────────────────────────────────────────

class TcpConnection {
public:
    TcpConnection() : socket_(io_context_) {}

    bool connect(const std::string& host, std::uint16_t port,
                 std::chrono::milliseconds timeout) {
        boost::system::error_code ec;
        auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            socket_.connect(tcp::endpoint(asio::ip::make_address(host), port), ec);
            if (!ec) { io_thread_ = std::thread([this]() { io_context_.run(); }); return true; }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        return false;
    }

    void disconnect() {
        boost::system::error_code ec;
        socket_.shutdown(tcp::socket::shutdown_both, ec);
        socket_.close(ec);
        io_context_.stop();
        if (io_thread_.joinable()) io_thread_.join();
    }

    bool is_connected() const { return socket_.is_open(); }

    bool send(std::uint16_t msg_id, std::uint32_t request_id,
              const std::string& body) {
        auto encoded = net::packet::encode(msg_id, request_id, 0, body, 0);
        boost::system::error_code ec;
        asio::write(socket_, asio::buffer(encoded), ec);
        return !ec;
    }

    net::packet::DecodedPacket read(std::chrono::milliseconds /*timeout*/) {
        net::packet::LengthHeader header{};
        boost::system::error_code ec;
        asio::read(socket_, asio::buffer(header.data(), header.size()), ec);
        if (ec) return {};
        std::uint32_t total_len = net::packet::decode_length(header);
        std::vector<char> payload(total_len);
        asio::read(socket_, asio::buffer(payload.data(), payload.size()), ec);
        if (ec) return {};
        return net::packet::decode_payload(payload);
    }

    bool has_data() {
        boost::system::error_code ec;
        socket_.available(ec);
        return !ec && socket_.available() > 0;
    }

private:
    boost::asio::io_context io_context_;
    tcp::socket socket_;
    std::thread io_thread_;
};

using namespace std::chrono_literals;

// ── Impl ──────────────────────────────────────────────────────────────

class SdkClient::Impl {
public:
    bool connect(const std::string& host, std::uint16_t port,
                 std::chrono::milliseconds timeout) {
        return conn_.connect(host, port, timeout);
    }

    void disconnect() { conn_.disconnect(); }
    bool is_connected() const { return conn_.is_connected(); }

    LoginResult login(const std::string& user_id, const std::string& token,
                      std::chrono::milliseconds timeout) {
        std::string body = user_id + "|token:" + token + "|" + user_id;
        auto resp = send_and_read(net::protocol::kLoginRequest, body, timeout);
        LoginResult result;
        if (resp.message_id == net::protocol::kLoginResponse) {
            result.ok = true;
            result.user_id = user_id;
            result.display_name = user_id;
        } else if (resp.message_id == net::protocol::kErrorResponse) {
            result.ok = false;
            result.error_code = resp.error_code;
            result.error_message = resp.body;
        } else {
            result.ok = false;
            result.error_code = static_cast<std::int32_t>(SdkError::kInvalidResponse);
        }
        return result;
    }

    RoomResult create_room(const std::string& room_id,
                           std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kRoomCreateRequest, room_id, timeout);
        RoomResult result;
        result.room_id = room_id;
        result.ok = (resp.message_id == net::protocol::kRoomCreateResponse);
        if (!result.ok) result.error_code = resp.error_code;
        return result;
    }

    RoomResult join_room(const std::string& room_id,
                         std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kRoomJoinRequest, room_id, timeout);
        RoomResult result;
        result.room_id = room_id;
        result.ok = (resp.message_id == net::protocol::kRoomJoinResponse);
        if (!result.ok) result.error_code = resp.error_code;
        return result;
    }

    RoomResult leave_room(const std::string& room_id,
                          std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kRoomLeaveRequest, room_id, timeout);
        RoomResult result;
        result.room_id = room_id;
        result.ok = (resp.message_id == net::protocol::kRoomLeaveResponse);
        if (!result.ok) result.error_code = resp.error_code;
        return result;
    }

    RoomResult set_ready(bool ready, std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kRoomReadyRequest,
                                   ready ? "true" : "false", timeout);
        RoomResult result;
        result.ok = (resp.message_id == net::protocol::kRoomReadyResponse);
        if (!result.ok) result.error_code = resp.error_code;
        return result;
    }

    BattleStartResult start_battle(const std::string& room_id,
                                    std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kBattleStartRequest, room_id, timeout);
        BattleStartResult result;
        result.ok = (resp.message_id == net::protocol::kBattleStartResponse);
        if (!result.ok) result.error_code = resp.error_code;
        return result;
    }

    BattleInputResult send_battle_input(const std::string& input_data,
                                         std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kBattleInputRequest,
                                   input_data, timeout);
        BattleInputResult result;
        result.ok = (resp.message_id == net::protocol::kBattleInputResponse);
        if (!result.ok) result.error_code = resp.error_code;
        return result;
    }

    EchoResult echo(const std::string& body, std::chrono::milliseconds timeout) {
        auto resp = send_and_read(net::protocol::kEchoRequest, body, timeout);
        EchoResult result;
        result.ok = (resp.message_id == net::protocol::kEchoResponse);
        if (result.ok) result.echo_body = resp.body;
        return result;
    }

    /// Drain any pending push messages and invoke callback
    void drain_pushes(PushCallback& cb) {
        while (conn_.has_data()) {
            auto packet = conn_.read(0ms);
            if (packet.message_id == 0) break;

            // Push messages: kicked, resumed, room_state, battle_state
            if (packet.message_id == net::protocol::kSessionKickedPush ||
                packet.message_id == net::protocol::kSessionResumedPush ||
                packet.message_id == net::protocol::kRoomStatePush ||
                packet.message_id == net::protocol::kBattleStatePush ||
                packet.message_id == net::protocol::kBattleInputPush) {
                if (cb) {
                    cb(PushMessage{packet.message_id, packet.body});
                }
            }
        }
    }

private:
    TcpConnection conn_;
    std::atomic<std::uint32_t> next_request_id_{1};

    net::packet::DecodedPacket send_and_read(
        std::uint16_t msg_id, const std::string& body,
        std::chrono::milliseconds timeout) {
        auto req_id = next_request_id_++;
        if (!conn_.send(msg_id, req_id, body)) return {};
        return conn_.read(timeout);
    }
};

// ── PIMPL forwarding ──────────────────────────────────────────────────

SdkClient::SdkClient() : impl_(std::make_unique<Impl>()) {}
SdkClient::~SdkClient() = default;

bool SdkClient::connect(const std::string& host, std::uint16_t port,
                         std::chrono::milliseconds timeout) {
    return impl_->connect(host, port, timeout);
}

void SdkClient::disconnect() { impl_->disconnect(); }
bool SdkClient::is_connected() const { return impl_->is_connected(); }

LoginResult SdkClient::login(const std::string& user_id, const std::string& token,
                              std::chrono::milliseconds timeout) {
    auto result = impl_->login(user_id, token, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

RoomResult SdkClient::create_room(const std::string& room_id,
                                   std::chrono::milliseconds timeout) {
    auto result = impl_->create_room(room_id, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

RoomResult SdkClient::join_room(const std::string& room_id,
                                 std::chrono::milliseconds timeout) {
    auto result = impl_->join_room(room_id, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

RoomResult SdkClient::leave_room(const std::string& room_id,
                                  std::chrono::milliseconds timeout) {
    auto result = impl_->leave_room(room_id, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

RoomResult SdkClient::set_ready(bool ready, std::chrono::milliseconds timeout) {
    auto result = impl_->set_ready(ready, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

BattleStartResult SdkClient::start_battle(const std::string& room_id,
                                           std::chrono::milliseconds timeout) {
    auto result = impl_->start_battle(room_id, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

BattleInputResult SdkClient::send_battle_input(const std::string& input_data,
                                                std::chrono::milliseconds timeout) {
    auto result = impl_->send_battle_input(input_data, timeout);
    impl_->drain_pushes(push_callback_);
    return result;
}

EchoResult SdkClient::echo(const std::string& body,
                            std::chrono::milliseconds timeout) {
    return impl_->echo(body, timeout);
}

void SdkClient::start_heartbeat(std::chrono::seconds interval) {
    // Heartbeat: periodic kHeartbeatRequest with empty body
    // For simplicity, heartbeat is handled at application level
}

void SdkClient::stop_heartbeat() {}

}  // namespace sdk
}  // namespace boost_gateway
