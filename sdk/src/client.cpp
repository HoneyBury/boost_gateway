// SDK v4.1.0: Standalone client — zero server dependencies.
#include "boost_gateway/sdk/client.h"
#include "boost_gateway/sdk/protocol/codec.h"
#include "boost_gateway/sdk/protocol/message.h"
#include <boost/asio.hpp>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <algorithm>
#include <optional>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace boost_gateway { namespace sdk {
namespace asio = boost::asio; using tcp = asio::ip::tcp; namespace msg = protocol;
using namespace std::chrono_literals;

namespace {

std::string login_body(const std::string& user_id, const std::string& token) {
    return user_id + "|" + token + "|" + user_id;
}

std::string parse_key_value_field(const std::string& body, const std::string& key) {
    const auto token = key + "=";
    const auto pos = body.find(token);
    if (pos == std::string::npos) {
        return {};
    }
    const auto value_start = pos + token.size();
    const auto value_end = body.find(':', value_start);
    return body.substr(value_start, value_end == std::string::npos
                                    ? std::string::npos
                                    : value_end - value_start);
}

std::string json_escape(const std::string& value) {
    std::string out;
    out.reserve(value.size());
    for (const auto ch : value) {
        if (ch == '\\' || ch == '"') {
            out.push_back('\\');
        }
        if (ch == '\n') {
            out += "\\n";
        } else {
            out.push_back(ch);
        }
    }
    return out;
}

}  // namespace

class TcpConnection {
public:
    TcpConnection() : socket_(io_context_) {}
    ~TcpConnection() { disconnect(); }
    bool connect(const std::string& host, std::uint16_t port, std::chrono::milliseconds timeout) {
        disconnect();
        io_context_.restart();
        socket_ = tcp::socket(io_context_);
        boost::system::error_code ec;
        auto dl = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < dl) {
            socket_.connect(tcp::endpoint(asio::ip::make_address(host), port), ec);
            if (!ec) {
                connected_ = true;
                return true;
            }
            socket_.close(ec);
            std::this_thread::sleep_for(50ms);
        }
        return false;
    }
    void disconnect() { connected_ = false; boost::system::error_code ec; socket_.close(ec); io_context_.stop(); }
    bool is_connected() const { return connected_; }
    bool send(std::uint16_t mid, std::uint32_t rid, const std::string& body) {
        auto e = protocol::encode(mid, rid, 0, body); boost::system::error_code ec; asio::write(socket_, asio::buffer(e), ec); return !ec;
    }
    protocol::DecodedPacket read(std::chrono::milliseconds timeout) {
        auto deadline = std::chrono::steady_clock::now() + timeout;
        while (std::chrono::steady_clock::now() < deadline) {
            if (auto packet = try_decode_buffered()) return *packet;

            boost::system::error_code ec;
            const auto available = socket_.available(ec);
            if (ec) {
                connected_ = false;
                return {};
            }
            if (available == 0) {
                std::this_thread::sleep_for(5ms);
                continue;
            }

            std::vector<char> chunk(available);
            const auto n = socket_.read_some(asio::buffer(chunk), ec);
            if (ec) {
                connected_ = false;
                return {};
            }
            read_buffer_.insert(read_buffer_.end(), chunk.begin(), chunk.begin() + static_cast<std::ptrdiff_t>(n));
        }
        return {};
    }
    bool has_data() { boost::system::error_code ec; socket_.available(ec); return !ec && socket_.available() > 0; }
private:
    std::optional<protocol::DecodedPacket> try_decode_buffered() {
        if (read_buffer_.size() < protocol::kLengthHeaderSize) {
            return std::nullopt;
        }
        protocol::LengthHeader h{};
        std::copy_n(read_buffer_.begin(), h.size(), h.begin());
        const auto len = protocol::decode_length(h);
        if (len < protocol::kFixedMetadataSize || len > 1024U * 1024U) {
            connected_ = false;
            read_buffer_.clear();
            return std::nullopt;
        }
        const auto frame_size = protocol::kLengthHeaderSize + len;
        if (read_buffer_.size() < frame_size) {
            return std::nullopt;
        }
        std::vector<char> payload(read_buffer_.begin() + protocol::kLengthHeaderSize,
                                  read_buffer_.begin() + static_cast<std::ptrdiff_t>(frame_size));
        read_buffer_.erase(read_buffer_.begin(),
                           read_buffer_.begin() + static_cast<std::ptrdiff_t>(frame_size));
        return protocol::decode_payload(payload);
    }

    boost::asio::io_context io_context_; tcp::socket socket_{io_context_}; std::atomic<bool> connected_{false}; std::vector<char> read_buffer_;
};

class SdkClient::Impl {
    TcpConnection conn_; std::atomic<std::uint32_t> next_{1}; PushCallback push_callback_; DisconnectCallback disconnect_callback_;
    std::function<void(const std::string&)> async_push_callback_; std::function<void()> async_disconnect_callback_;
    std::mutex callback_mutex_; std::mutex io_mutex_; std::mutex heartbeat_mutex_; std::condition_variable heartbeat_cv_;
    std::thread heartbeat_thread_; std::atomic<bool> heartbeat_running_{false};

    bool is_push(std::uint16_t id) { return id == msg::kSessionKickedPush || id == msg::kSessionResumedPush || id == msg::kRoomStatePush || id == msg::kBattleStatePush || id == msg::kBattleInputPush; }
    static std::string match_body(const std::string& user_id, std::int64_t mmr, const std::string& mode) {
        return user_id + "|" + std::to_string(mmr) + "|" + mode;
    }
    static std::string leaderboard_submit_body(const std::string& user_id,
                                               const std::string& display_name,
                                               std::int64_t score) {
        return user_id + "|" + display_name + "|" + std::to_string(score);
    }
    static std::string room_list_body(std::size_t page, std::size_t page_size, const std::string& status) {
        std::string body = "{\"page\":" + std::to_string(page) +
                           ",\"page_size\":" + std::to_string(page_size);
        if (!status.empty()) {
            body += ",\"status\":\"" + status + "\"";
        }
        body += "}";
        return body;
    }
    void dispatch_push(const protocol::DecodedPacket& p) {
        PushCallback callback;
        {
            std::lock_guard<std::mutex> lock(callback_mutex_);
            callback = push_callback_;
        }
        if (callback) callback(PushMessage{.message_id = p.message_id, .body = p.body});
    }
    void notify_disconnect() {
        DisconnectCallback callback;
        {
            std::lock_guard<std::mutex> lock(callback_mutex_);
            callback = disconnect_callback_;
        }
        if (callback) callback();
    }

    protocol::DecodedPacket expect(std::uint16_t req, const std::string& body, std::chrono::milliseconds to, std::uint16_t exp) {
        std::lock_guard<std::mutex> io_lock(io_mutex_);
        if (!conn_.is_connected()) return {.error_code = static_cast<std::int32_t>(SdkError::kNotConnected), .body = to_string(SdkError::kNotConnected)};
        auto rid = next_++;
        if (!conn_.send(req, rid, body)) return {.error_code = static_cast<std::int32_t>(SdkError::kSendFailed), .body = to_string(SdkError::kSendFailed)};
        auto dl = std::chrono::steady_clock::now() + to;
        while (std::chrono::steady_clock::now() < dl) {
            auto p = conn_.read(std::chrono::milliseconds(100)); if (p.message_id == 0) continue;
            if (is_push(p.message_id)) { dispatch_push(p); continue; }
            if (p.message_id == msg::kErrorResponse) return {.message_id = p.message_id, .request_id = p.request_id, .error_code = p.error_code, .body = p.body};
            if (p.message_id != exp) return {.message_id = p.message_id, .request_id = p.request_id, .error_code = static_cast<std::int32_t>(SdkError::kInvalidResponse), .body = p.body};
            return p;
        }
        return {.error_code = static_cast<std::int32_t>(SdkError::kTimeout), .body = to_string(SdkError::kTimeout)};
    }

public:
    bool connect(const std::string& h, std::uint16_t p, std::chrono::milliseconds t) { return conn_.connect(h, p, t); }
    ~Impl() { stop_heartbeat(); conn_.disconnect(); }
    void disconnect() { stop_heartbeat(); conn_.disconnect(); }
    bool is_connected() const { return conn_.is_connected(); }

    LoginResult login(const std::string& u, const std::string& tok, std::chrono::milliseconds to) {
        auto r = expect(msg::kLoginRequest, login_body(u, tok), to, msg::kLoginResponse);
        LoginResult lr; lr.ok = (r.message_id == msg::kLoginResponse); lr.user_id = u; lr.display_name = u; lr.error_code = r.error_code; lr.error_message = r.body; return lr;
    }
    RegisterResult register_account(const std::string& user_id, const std::string& credential, const std::string& display_name, std::chrono::milliseconds to) {
        std::string body = "{\"user_id\":\"" + json_escape(user_id) +
                           "\",\"credential\":\"" + json_escape(credential) +
                           "\",\"display_name\":\"" + json_escape(display_name.empty() ? user_id : display_name) + "\"}";
        auto r = expect(msg::kRegisterRequest, body, to, msg::kRegisterResponse);
        RegisterResult rr; rr.ok = (r.message_id == msg::kRegisterResponse); rr.error_code = r.error_code; rr.error_message = r.body; rr.response_body = r.body; return rr;
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
    RoomQueryResult room_list(std::size_t page, std::size_t page_size, const std::string& status, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomListRequest, room_list_body(page, page_size, status), to, msg::kRoomListResponse);
        RoomQueryResult rr; rr.ok = (r.message_id == msg::kRoomListResponse); rr.error_code = r.error_code; rr.error_message = r.body; rr.response_body = r.body; return rr;
    }
    RoomQueryResult room_detail(const std::string& room_id, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomDetailRequest, room_id, to, msg::kRoomDetailResponse);
        RoomQueryResult rr; rr.ok = (r.message_id == msg::kRoomDetailResponse); rr.error_code = r.error_code; rr.error_message = r.body; rr.response_body = r.body; return rr;
    }
    RoomQueryResult room_kick(const std::string& target_user_id, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomKickRequest, target_user_id, to, msg::kRoomKickResponse);
        RoomQueryResult rr; rr.ok = (r.message_id == msg::kRoomKickResponse); rr.error_code = r.error_code; rr.error_message = r.body; rr.response_body = r.body; return rr;
    }
    RoomQueryResult room_transfer_owner(const std::string& new_owner_id, std::chrono::milliseconds to) {
        auto r = expect(msg::kRoomTransferOwnerRequest, new_owner_id, to, msg::kRoomTransferOwnerResponse);
        RoomQueryResult rr; rr.ok = (r.message_id == msg::kRoomTransferOwnerResponse); rr.error_code = r.error_code; rr.error_message = r.body; rr.response_body = r.body; return rr;
    }
    BattleStartResult start_battle(const std::string& rid, std::chrono::milliseconds to) {
        auto r = expect(msg::kBattleStartRequest, rid, to, msg::kBattleStartResponse);
        BattleStartResult br; br.ok = (r.message_id == msg::kBattleStartResponse); br.error_code = r.error_code; br.error_message = r.body; br.battle_id = parse_key_value_field(r.body, "battle_id"); return br;
    }
    BattleInputResult send_battle_input(const std::string& d, std::chrono::milliseconds to) {
        auto r = expect(msg::kBattleInputRequest, d, to, msg::kBattleInputResponse);
        BattleInputResult bi; bi.ok = (r.message_id == msg::kBattleInputResponse); bi.error_code = r.error_code; bi.error_message = r.body; bi.input_seq = 0; return bi;
    }
    BattleStateResult battle_state(const std::string& battle_id, std::chrono::milliseconds to) {
        auto r = expect(msg::kBattleStateRequest, battle_id, to, msg::kBattleStateResponse);
        BattleStateResult bs; bs.ok = (r.message_id == msg::kBattleStateResponse); bs.error_code = r.error_code; bs.error_message = r.body; bs.response_body = r.body; return bs;
    }
    ReplayLoadResult replay_load(const std::string& battle_id, std::chrono::milliseconds to) {
        auto r = expect(msg::kReplayLoadRequest, battle_id, to, msg::kReplayLoadResponse);
        ReplayLoadResult rl; rl.ok = (r.message_id == msg::kReplayLoadResponse); rl.error_code = r.error_code; rl.error_message = r.body; rl.response_body = r.body; return rl;
    }
    MatchResult match_join(const std::string& user_id, std::int64_t mmr, const std::string& mode, std::chrono::milliseconds to) {
        auto r = expect(msg::kMatchJoinRequest, match_body(user_id, mmr, mode), to, msg::kMatchJoinResponse);
        MatchResult mr; mr.ok = (r.message_id == msg::kMatchJoinResponse); mr.error_code = r.error_code; mr.error_message = r.body; mr.response_body = r.body; return mr;
    }
    MatchResult match_leave(const std::string& user_id, const std::string& mode, std::chrono::milliseconds to) {
        auto r = expect(msg::kMatchLeaveRequest, match_body(user_id, 0, mode), to, msg::kMatchLeaveResponse);
        MatchResult mr; mr.ok = (r.message_id == msg::kMatchLeaveResponse); mr.error_code = r.error_code; mr.error_message = r.body; mr.response_body = r.body; return mr;
    }
    MatchResult match_status(const std::string& user_id, const std::string& mode, std::chrono::milliseconds to) {
        auto r = expect(msg::kMatchStatusRequest, match_body(user_id, 0, mode), to, msg::kMatchStatusResponse);
        MatchResult mr; mr.ok = (r.message_id == msg::kMatchStatusResponse); mr.error_code = r.error_code; mr.error_message = r.body; mr.response_body = r.body; return mr;
    }
    LeaderboardSubmitResult leaderboard_submit(const std::string& user_id, const std::string& display_name, std::int64_t score, std::chrono::milliseconds to) {
        auto r = expect(msg::kLeaderboardSubmitRequest, leaderboard_submit_body(user_id, display_name, score), to, msg::kLeaderboardSubmitResponse);
        LeaderboardSubmitResult lr; lr.ok = (r.message_id == msg::kLeaderboardSubmitResponse); lr.error_code = r.error_code; lr.error_message = r.body; lr.response_body = r.body; return lr;
    }
    LeaderboardQueryResult leaderboard_top(std::size_t k, std::chrono::milliseconds to) {
        auto r = expect(msg::kLeaderboardTopRequest, std::to_string(k), to, msg::kLeaderboardTopResponse);
        LeaderboardQueryResult lr; lr.ok = (r.message_id == msg::kLeaderboardTopResponse); lr.error_code = r.error_code; lr.error_message = r.body; lr.response_body = r.body; return lr;
    }
    LeaderboardQueryResult leaderboard_rank(const std::string& user_id, std::chrono::milliseconds to) {
        auto r = expect(msg::kLeaderboardRankRequest, user_id, to, msg::kLeaderboardRankResponse);
        LeaderboardQueryResult lr; lr.ok = (r.message_id == msg::kLeaderboardRankResponse); lr.error_code = r.error_code; lr.error_message = r.body; lr.response_body = r.body; return lr;
    }
    EchoResult echo(const std::string& b, std::chrono::milliseconds to) {
        auto r = expect(msg::kEchoRequest, b, to, msg::kEchoResponse);
        EchoResult er; er.ok = (r.message_id == msg::kEchoResponse); er.echo_body = r.body; return er;
    }
    void on_push(PushCallback callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        push_callback_ = std::move(callback);
    }
    void on_disconnect(DisconnectCallback callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        disconnect_callback_ = std::move(callback);
    }
    void on_async_push(std::function<void(const std::string&)> callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        async_push_callback_ = std::move(callback);
    }
    void on_async_disconnect(std::function<void()> callback) {
        std::lock_guard<std::mutex> lock(callback_mutex_);
        async_disconnect_callback_ = std::move(callback);
    }
    bool heartbeat_once(std::chrono::milliseconds timeout) {
        auto response = expect(msg::kHeartbeatRequest, "", timeout, msg::kHeartbeatResponse);
        return response.message_id == msg::kHeartbeatResponse && response.error_code == 0;
    }
    void start_heartbeat(std::chrono::seconds interval) {
        stop_heartbeat();
        if (interval <= std::chrono::seconds(0)) return;
        heartbeat_running_ = true;
        heartbeat_thread_ = std::thread([this, interval] {
            while (heartbeat_running_) {
                std::unique_lock<std::mutex> lock(heartbeat_mutex_);
                heartbeat_cv_.wait_for(lock, interval, [this] { return !heartbeat_running_; });
                lock.unlock();
                if (!heartbeat_running_) break;
                if (!conn_.is_connected()) continue;
                if (!heartbeat_once(std::chrono::seconds(3))) {
                    conn_.disconnect();
                    heartbeat_running_ = false;
                    notify_disconnect();
                    break;
                }
            }
        });
    }
    void stop_heartbeat() {
        heartbeat_running_ = false;
        heartbeat_cv_.notify_all();
        if (heartbeat_thread_.joinable()) heartbeat_thread_.join();
    }
};

SdkClient::SdkClient() : impl_(std::make_unique<Impl>()) {}
SdkClient::~SdkClient() = default;
bool SdkClient::connect(const std::string& h, std::uint16_t p, std::chrono::milliseconds t) { return impl_->connect(h,p,t); }
void SdkClient::disconnect() { impl_->disconnect(); }
bool SdkClient::is_connected() const { return impl_->is_connected(); }
LoginResult SdkClient::login(const std::string& u, const std::string& t, std::chrono::milliseconds to) { return impl_->login(u,t,to); }
RegisterResult SdkClient::register_account(const std::string& u, const std::string& c, const std::string& d, std::chrono::milliseconds t) { return impl_->register_account(u,c,d,t); }
RoomResult SdkClient::create_room(const std::string& r, std::chrono::milliseconds t) { return impl_->create_room(r,t); }
RoomResult SdkClient::join_room(const std::string& r, std::chrono::milliseconds t) { return impl_->join_room(r,t); }
RoomResult SdkClient::leave_room(const std::string& r, std::chrono::milliseconds t) { return impl_->leave_room(r,t); }
RoomResult SdkClient::set_ready(bool r, std::chrono::milliseconds t) { return impl_->set_ready(r,t); }
RoomQueryResult SdkClient::room_list(std::size_t p, std::size_t ps, const std::string& s, std::chrono::milliseconds t) { return impl_->room_list(p,ps,s,t); }
RoomQueryResult SdkClient::room_detail(const std::string& r, std::chrono::milliseconds t) { return impl_->room_detail(r,t); }
RoomQueryResult SdkClient::room_kick(const std::string& u, std::chrono::milliseconds t) { return impl_->room_kick(u,t); }
RoomQueryResult SdkClient::room_transfer_owner(const std::string& u, std::chrono::milliseconds t) { return impl_->room_transfer_owner(u,t); }
BattleStartResult SdkClient::start_battle(const std::string& r, std::chrono::milliseconds t) { return impl_->start_battle(r,t); }
BattleInputResult SdkClient::send_battle_input(const std::string& d, std::chrono::milliseconds t) { return impl_->send_battle_input(d,t); }
BattleStateResult SdkClient::battle_state(const std::string& b, std::chrono::milliseconds t) { return impl_->battle_state(b,t); }
ReplayLoadResult SdkClient::replay_load(const std::string& b, std::chrono::milliseconds t) { return impl_->replay_load(b,t); }
MatchResult SdkClient::match_join(const std::string& u, std::int64_t mmr, const std::string& mode, std::chrono::milliseconds t) { return impl_->match_join(u,mmr,mode,t); }
MatchResult SdkClient::match_leave(const std::string& u, const std::string& mode, std::chrono::milliseconds t) { return impl_->match_leave(u,mode,t); }
MatchResult SdkClient::match_status(const std::string& u, const std::string& mode, std::chrono::milliseconds t) { return impl_->match_status(u,mode,t); }
LeaderboardSubmitResult SdkClient::leaderboard_submit(const std::string& u, const std::string& d, std::int64_t s, std::chrono::milliseconds t) { return impl_->leaderboard_submit(u,d,s,t); }
LeaderboardQueryResult SdkClient::leaderboard_top(std::size_t k, std::chrono::milliseconds t) { return impl_->leaderboard_top(k,t); }
LeaderboardQueryResult SdkClient::leaderboard_rank(const std::string& u, std::chrono::milliseconds t) { return impl_->leaderboard_rank(u,t); }
EchoResult SdkClient::echo(const std::string& b, std::chrono::milliseconds t) { return impl_->echo(b,t); }
void SdkClient::on_push(PushCallback callback) { impl_->on_push(std::move(callback)); }
void SdkClient::on_disconnect(DisconnectCallback callback) { impl_->on_disconnect(std::move(callback)); }
void SdkClient::start_heartbeat(std::chrono::seconds interval) { impl_->start_heartbeat(interval); }
void SdkClient::stop_heartbeat() { impl_->stop_heartbeat(); }

// ── Async API ──────────────────────────────────────────────────────────

void SdkClient::async_connect(const std::string& host, std::uint16_t port,
                              std::function<void(bool)> callback,
                              std::chrono::milliseconds timeout) {
    std::thread([this, host, port, callback, timeout]() {
        bool ok = impl_->connect(host, port, timeout);
        if (callback) callback(ok);
    }).detach();
}

void SdkClient::async_login(const std::string& user_id, const std::string& token,
                            std::function<void(LoginResult)> callback,
                            std::chrono::milliseconds timeout) {
    std::thread([this, user_id, token, callback, timeout]() {
        auto result = impl_->login(user_id, token, timeout);
        if (callback) callback(result);
    }).detach();
}

void SdkClient::async_create_room(const std::string& room_id,
                                  std::function<void(RoomResult)> callback,
                                  std::chrono::milliseconds timeout) {
    std::thread([this, room_id, callback, timeout]() {
        auto result = impl_->create_room(room_id, timeout);
        if (callback) callback(result);
    }).detach();
}

void SdkClient::async_join_room(const std::string& room_id,
                                std::function<void(RoomResult)> callback,
                                std::chrono::milliseconds timeout) {
    std::thread([this, room_id, callback, timeout]() {
        auto result = impl_->join_room(room_id, timeout);
        if (callback) callback(result);
    }).detach();
}

void SdkClient::async_send_battle_input(const std::string& input_data,
                                        std::function<void(BattleInputResult)> callback,
                                        std::chrono::milliseconds timeout) {
    std::thread([this, input_data, callback, timeout]() {
        auto result = impl_->send_battle_input(input_data, timeout);
        if (callback) callback(result);
    }).detach();
}

void SdkClient::on_async_push(std::function<void(const std::string&)> callback) {
    impl_->on_async_push(std::move(callback));
}

void SdkClient::on_async_disconnect(std::function<void()> callback) {
    impl_->on_async_disconnect(std::move(callback));
}

}} // namespaces
