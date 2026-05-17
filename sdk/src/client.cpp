// SDK v4.1.0: Standalone client — zero server dependencies.
#include "boost_gateway/sdk/client.h"
#include "boost_gateway/sdk/protocol/codec.h"
#include "boost_gateway/sdk/protocol/message.h"
#include <boost/asio.hpp>
#include <atomic>
#include <chrono>
#include <string>
#include <thread>
#include <vector>

namespace boost_gateway { namespace sdk {
namespace asio = boost::asio; using tcp = asio::ip::tcp; namespace msg = protocol;
using namespace std::chrono_literals;

class TcpConnection {
public:
    TcpConnection() : socket_(io_context_) {}
    bool connect(const std::string& host, std::uint16_t port, std::chrono::milliseconds timeout) {
        boost::system::error_code ec;
        auto dl = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < dl) {
            socket_.connect(tcp::endpoint(asio::ip::make_address(host), port), ec);
            if (!ec) { connected_ = true; io_thread_ = std::thread([this]{io_context_.run();}); return true; }
            std::this_thread::sleep_for(50ms);
        }
        return false;
    }
    void disconnect() { connected_ = false; boost::system::error_code ec; socket_.close(ec); io_context_.stop(); if (io_thread_.joinable()) io_thread_.join(); }
    bool is_connected() const { return connected_; }
    bool send(std::uint16_t mid, std::uint32_t rid, const std::string& body) {
        auto e = protocol::encode(mid, rid, 0, body); boost::system::error_code ec; asio::write(socket_, asio::buffer(e), ec); return !ec;
    }
    protocol::DecodedPacket read(std::chrono::milliseconds) {
        protocol::LengthHeader h{}; boost::system::error_code ec;
        asio::read(socket_, asio::buffer(h.data(), h.size()), ec); if (ec) return {};
        auto len = protocol::decode_length(h);
        std::vector<char> p(len); asio::read(socket_, asio::buffer(p.data(), p.size()), ec); if (ec) return {};
        return protocol::decode_payload(p);
    }
    bool has_data() { boost::system::error_code ec; socket_.available(ec); return !ec && socket_.available() > 0; }
private:
    boost::asio::io_context io_context_; tcp::socket socket_{io_context_}; std::atomic<bool> connected_{false}; std::thread io_thread_;
};

class SdkClient::Impl {
    TcpConnection conn_; std::atomic<std::uint32_t> next_{1};

    bool is_push(std::uint16_t id) { return id == msg::kSessionKickedPush || id == msg::kSessionResumedPush || id == msg::kRoomStatePush || id == msg::kBattleStatePush || id == msg::kBattleInputPush; }

    protocol::DecodedPacket expect(std::uint16_t req, const std::string& body, std::chrono::milliseconds to, std::uint16_t exp) {
        auto rid = next_++; if (!conn_.send(req, rid, body)) return {};
        auto dl = std::chrono::steady_clock::now() + to;
        while (std::chrono::steady_clock::now() < dl) {
            auto p = conn_.read(std::chrono::milliseconds(100)); if (p.message_id == 0) continue;
            if (is_push(p.message_id)) continue;
            return p;
        }
        return {};
    }

public:
    bool connect(const std::string& h, std::uint16_t p, std::chrono::milliseconds t) { return conn_.connect(h, p, t); }
    void disconnect() { conn_.disconnect(); }
    bool is_connected() const { return conn_.is_connected(); }

    LoginResult login(const std::string& u, const std::string& tok, std::chrono::milliseconds to) {
        auto r = expect(msg::kLoginRequest, u+"|token:"+tok+"|"+u, to, msg::kLoginResponse);
        LoginResult lr; lr.ok = (r.message_id == msg::kLoginResponse); lr.user_id = u; lr.display_name = u; lr.error_code = r.error_code; lr.error_message = r.body; return lr;
    }
    RoomResult create_room(const std::string& rid, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomCreateRequest, rid, to, msg::kRoomCreateResponse);
        RoomResult rr; rr.ok = (r.message_id == msg::kRoomCreateResponse); rr.room_id = rid; rr.error_code = 0; rr.error_message = r.body; return rr;
    }
    RoomResult join_room(const std::string& rid, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomJoinRequest, rid, to, msg::kRoomJoinResponse);
        RoomResult rr; rr.ok = (r.message_id == msg::kRoomJoinResponse); rr.room_id = rid; rr.error_code = 0; rr.error_message = r.body; return rr;
    }
    RoomResult leave_room(const std::string& rid, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomLeaveRequest, rid, to, msg::kRoomLeaveResponse);
        RoomResult rr; rr.ok = (r.message_id == msg::kRoomLeaveResponse); rr.room_id = rid; rr.error_code = 0; rr.error_message = r.body; return rr;
    }
    RoomResult set_ready(bool v, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomReadyRequest, v?"true":"false", to, msg::kRoomReadyResponse);
        RoomResult rr; rr.ok = (r.message_id == msg::kRoomReadyResponse); rr.room_id = ""; rr.error_code = 0; rr.error_message = r.body; return rr;
    }
    BattleStartResult start_battle(const std::string& rid, std::chrono::milliseconds to) {
        auto r = expect(msg::kBattleStartRequest, rid, to, msg::kBattleStartResponse);
        BattleStartResult br; br.ok = (r.message_id == msg::kBattleStartResponse); br.error_code = r.error_code; br.error_message = r.body; br.battle_id = ""; return br;
    }
    BattleInputResult send_battle_input(const std::string& d, std::chrono::milliseconds to) {
        auto r = expect(msg::kBattleInputRequest, d, to, msg::kBattleInputResponse);
        BattleInputResult bi; bi.ok = (r.message_id == msg::kBattleInputResponse); bi.error_code = r.error_code; bi.error_message = r.body; bi.input_seq = 0; return bi;
    }
    EchoResult echo(const std::string& b, std::chrono::milliseconds to) {
        auto r = expect(msg::kEchoRequest, b, to, msg::kEchoResponse);
        EchoResult er; er.ok = (r.message_id == msg::kEchoResponse); er.echo_body = r.body; return er;
    }
};

SdkClient::SdkClient() : impl_(std::make_unique<Impl>()) {}
SdkClient::~SdkClient() = default;
bool SdkClient::connect(const std::string& h, std::uint16_t p, std::chrono::milliseconds t) { return impl_->connect(h,p,t); }
void SdkClient::disconnect() { impl_->disconnect(); }
bool SdkClient::is_connected() const { return impl_->is_connected(); }
LoginResult SdkClient::login(const std::string& u, const std::string& t, std::chrono::milliseconds to) { return impl_->login(u,t,to); }
RoomResult SdkClient::create_room(const std::string& r, std::chrono::milliseconds t) { return impl_->create_room(r,t); }
RoomResult SdkClient::join_room(const std::string& r, std::chrono::milliseconds t) { return impl_->join_room(r,t); }
RoomResult SdkClient::leave_room(const std::string& r, std::chrono::milliseconds t) { return impl_->leave_room(r,t); }
RoomResult SdkClient::set_ready(bool r, std::chrono::milliseconds t) { return impl_->set_ready(r,t); }
BattleStartResult SdkClient::start_battle(const std::string& r, std::chrono::milliseconds t) { return impl_->start_battle(r,t); }
BattleInputResult SdkClient::send_battle_input(const std::string& d, std::chrono::milliseconds t) { return impl_->send_battle_input(d,t); }
EchoResult SdkClient::echo(const std::string& b, std::chrono::milliseconds t) { return impl_->echo(b,t); }
void SdkClient::start_heartbeat(std::chrono::seconds) {}
void SdkClient::stop_heartbeat() {}

}} // namespaces
