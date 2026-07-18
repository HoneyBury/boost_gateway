#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/gateway/demo_server.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v2/service/backend_connection.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v2/match/matchmaking_service.h"
#include "v3/cluster/cluster_router.h"
#include "v3/cluster/raft.h"
#include "v3/tracing/otel_exporter.h"

#include <boost/asio.hpp>
#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/http/message.hpp>
#include <boost/beast/http/read.hpp>
#include <boost/beast/http/string_body.hpp>
#include <boost/beast/http/write.hpp>
#include <nlohmann/json.hpp>

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <unordered_set>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
using tcp = asio::ip::tcp;

constexpr const char* kGatewayHost = "127.0.0.1";

class DisableRedisAutoConnectForTest {
public:
    DisableRedisAutoConnectForTest() {
        if (const char* value = std::getenv("BOOST_DISABLE_REDIS_AUTO_CONNECT")) {
            previous_value_ = value;
        }
        set_environment_variable("BOOST_DISABLE_REDIS_AUTO_CONNECT", "1");
    }

    ~DisableRedisAutoConnectForTest() {
        if (previous_value_.has_value()) {
            set_environment_variable("BOOST_DISABLE_REDIS_AUTO_CONNECT",
                                     previous_value_->c_str());
        } else {
            unset_environment_variable("BOOST_DISABLE_REDIS_AUTO_CONNECT");
        }
    }

    DisableRedisAutoConnectForTest(const DisableRedisAutoConnectForTest&) = delete;
    DisableRedisAutoConnectForTest& operator=(
        const DisableRedisAutoConnectForTest&) = delete;

private:
    static void set_environment_variable(const char* name, const char* value) {
#if defined(_WIN32)
        _putenv_s(name, value);
#else
        setenv(name, value, 1);
#endif
    }

    static void unset_environment_variable(const char* name) {
#if defined(_WIN32)
        _putenv_s(name, "");
#else
        unsetenv(name);
#endif
    }

    std::optional<std::string> previous_value_;
};

struct FakeOtlpCollector {
    asio::io_context io_context;
    tcp::acceptor acceptor{io_context};
    std::thread thread;
    std::mutex mutex;
    std::string last_target;
    std::string last_body;
    std::size_t request_count = 0;
    std::atomic<bool> running{false};

    bool start() {
        try {
            acceptor.open(tcp::v4());
            acceptor.set_option(asio::socket_base::reuse_address(true));
            acceptor.bind({tcp::v4(), 0});
            acceptor.listen();
            running = true;
            thread = std::thread([this]() { run(); });
            return true;
        } catch (...) {
            return false;
        }
    }

    void stop() {
        running = false;
        boost::system::error_code ignored_ec;
        if (acceptor.is_open()) {
            tcp::socket wake_socket(io_context);
            wake_socket.connect(
                tcp::endpoint(asio::ip::make_address("127.0.0.1"), port()),
                ignored_ec);
            wake_socket.close(ignored_ec);
        }
        if (thread.joinable()) {
            thread.join();
        }
        acceptor.close(ignored_ec);
        io_context.stop();
    }

    std::uint16_t port() const {
        return acceptor.local_endpoint().port();
    }

private:
    void run() {
        while (running) {
            boost::system::error_code ec;
            tcp::socket socket(io_context);
            acceptor.accept(socket, ec);
            if (ec) {
                if (!running) {
                    return;
                }
                continue;
            }

            beast::flat_buffer buffer;
            http::request<http::string_body> request;
            http::read(socket, buffer, request, ec);
            if (!ec) {
                std::lock_guard lock(mutex);
                last_target = std::string(request.target());
                last_body = request.body();
                ++request_count;
            }

            http::response<http::string_body> response{http::status::ok, 11};
            response.set(http::field::content_type, "application/json");
            response.body() = R"({"ok":true})";
            response.prepare_payload();
            http::write(socket, response, ec);
            socket.shutdown(tcp::socket::shutdown_both, ec);
        }
    }
};

// ─── Backend helpers ──────────────────────────────────────────────

v2::service::BackendServer::HandlerMap make_login_handlers() {
    v2::service::BackendServer::HandlerMap handlers;

    handlers["login_request"] = [](const v2::service::BackendEnvelope& request) {
        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kResponse;

        auto& payload = request.payload;
        if (payload.empty()) {
            response.kind = v2::service::MessageKind::kError;
            response.error_code = -1004;
            return response;
        }

        auto first_pipe = payload.find('|');
        if (first_pipe == std::string::npos) {
            response.kind = v2::service::MessageKind::kError;
            response.error_code = -1004;
            return response;
        }

        std::string user_id = payload.substr(0, first_pipe);
        auto token_start = payload.find("token:");
        if (token_start == std::string::npos || token_start + 6 >= payload.size()) {
            response.kind = v2::service::MessageKind::kError;
            response.error_code = -1004;
            response.payload = "empty_token";
            return response;
        }

        response.payload = "login_ok:" + user_id;
        return response;
    };

    // Reject empty tokens for error testing
    handlers["reject_empty"] = [](const v2::service::BackendEnvelope& /*request*/) {
        v2::service::BackendEnvelope response;
        response.kind = v2::service::MessageKind::kError;
        response.error_code = -1004;
        response.payload = "rejected";
        return response;
    };

    return handlers;
}

// ─── Backend server wrapper ───────────────────────────────────────

struct BackendProcess {
    std::unique_ptr<v2::service::BackendServer> server;
    std::uint16_t port = 0;

    bool start() {
        server = std::make_unique<v2::service::BackendServer>(0, make_login_handlers());
        server->start();
        port = server->local_port();
        return port > 0;
    }

    void stop() {
        if (server) server->stop();
    }
};

// ─── Minimal gateway that bridges client packets to backend ───────

struct BridgeGateway {
    asio::io_context io_context;
    std::unique_ptr<tcp::acceptor> acceptor;
    std::unique_ptr<v2::service::BackendConnection> backend_conn;
    std::thread thread;
    std::atomic<bool> running{false};

    bool start(std::uint16_t own_port, std::uint16_t backend_port) {
        try {
            v2::service::BackendConnectionOptions opts{
                .host = kGatewayHost,
                .port = backend_port,
            };

            backend_conn = std::make_unique<v2::service::BackendConnection>(opts);
            if (!backend_conn->connect()) return false;

            acceptor = std::make_unique<tcp::acceptor>(
                io_context, tcp::endpoint(tcp::v4(), own_port));

            running = true;
            thread = std::thread([this] {
                do_accept();
                io_context.run();
            });
            return true;
        } catch (const std::exception&) {
            return false;
        }
    }

    void stop() {
        running = false;
        if (acceptor) {
            boost::system::error_code ec;
            acceptor->close(ec);
        }
        io_context.stop();
        if (thread.joinable()) thread.join();
        if (backend_conn) backend_conn->close();
    }

    [[nodiscard]] std::uint16_t local_port() const {
        if (!acceptor || !acceptor->is_open()) return 0;
        return acceptor->local_endpoint().port();
    }

private:
    void do_accept() {
        if (!running) return;
        auto socket = std::make_shared<tcp::socket>(io_context);
        acceptor->async_accept(*socket, [this, socket](boost::system::error_code ec) {
            if (!ec && running) handle_client(std::move(socket));
            do_accept();
        });
    }

    void handle_client(std::shared_ptr<tcp::socket> socket) {
        while (running && socket->is_open()) {
            // Read client length-prefixed packet
            net::packet::LengthHeader header{};
            boost::system::error_code ec;
            asio::read(*socket, asio::buffer(header),
                       asio::transfer_exactly(sizeof(header)), ec);
            if (ec) break;

            const auto payload_length = net::packet::decode_length(header);
            if (payload_length == 0 || payload_length > 1024 * 1024) break;

            std::vector<char> payload(payload_length);
            asio::read(*socket, asio::buffer(payload),
                       asio::transfer_exactly(payload_length), ec);
            if (ec) break;

            auto packet = net::packet::decode_payload(payload);

            // Map message_id to message_type string
            std::string message_type;
            switch (packet.message_id) {
                case net::protocol::kLoginRequest:
                    message_type = "login_request";
                    break;
                case 0:  // test: use reject_empty handler
                    message_type = "reject_empty";
                    break;
                default:
                    message_type = "unknown";
                    break;
            }

            // Forward to backend via BackendEnvelope
            v2::service::BackendEnvelope request{
                .target_service = v2::service::ServiceId::kLogin,
                .kind = v2::service::MessageKind::kRequest,
                .payload = packet.body,
                .message_type = message_type,
            };

            auto response = backend_conn->send_request(request);

            // Build client response packet
            if (response) {
                std::uint16_t response_msg_id;
                std::int32_t response_error = 0;
                std::string response_body;

                if (response->kind == v2::service::MessageKind::kError) {
                    response_msg_id = net::protocol::kErrorResponse;
                    response_error = response->error_code;
                    response_body = response->payload;
                } else {
                    response_msg_id = net::protocol::kLoginResponse;
                    response_body = response->payload;
                }

                auto outbound = net::packet::encode(
                    response_msg_id, packet.request_id, response_error, response_body);
                asio::write(*socket, asio::buffer(outbound), ec);
                if (ec) break;
            } else {
                // Backend timeout/unavailable
                auto outbound = net::packet::encode(
                    net::protocol::kErrorResponse, packet.request_id,
                    -2002, "backend_unavailable");
                asio::write(*socket, asio::buffer(outbound), ec);
                if (ec) break;
            }
        }
    }
};

// ─── TestClient (reuse pattern from demo_server_smoke_test) ───────

class TestClient {
public:
    TestClient() : socket_(io_context_) {}

    void connect(std::uint16_t port) {
        socket_.connect(tcp::endpoint(asio::ip::make_address(kGatewayHost), port));
    }

    void close() {
        boost::system::error_code ignored_ec;
        socket_.shutdown(tcp::socket::shutdown_both, ignored_ec);
        socket_.close(ignored_ec);
    }

    net::packet::DecodedPacket exchange(std::uint16_t message_id,
                                        std::uint32_t request_id,
                                        const std::string& body) {
        send(message_id, request_id, body);
        return read();
    }

    void send(std::uint16_t message_id, std::uint32_t request_id,
              const std::string& body) {
        auto outbound = net::packet::encode(message_id, request_id, 0, body);
        asio::write(socket_, asio::buffer(outbound));
    }

    net::packet::DecodedPacket read() {
        net::packet::LengthHeader header{};
        asio::read(socket_, asio::buffer(header));
        auto payload_length = net::packet::decode_length(header);

        std::vector<char> payload(payload_length);
        asio::read(socket_, asio::buffer(payload));
        return net::packet::decode_payload(payload);
    }

    net::packet::DecodedPacket expect_message(std::uint16_t message_id) {
        for (;;) {
            const auto packet = read();
            if (packet.message_id == message_id) {
                return packet;
            }
        }
    }

private:
    asio::io_context io_context_;
    tcp::socket socket_;
};

// ─── JSON login backend (matches v2_login_backend contract) ────────

struct LoginBackendProcess {
    std::unique_ptr<v2::service::BackendServer> server;
    std::uint16_t port = 0;
    std::string backend_id = "login-backend";

    std::unordered_map<std::string, std::string> active_sessions_;
    std::mutex mutex_;

    bool start() {
        v2::service::BackendServer::HandlerMap handlers;

        handlers["login_request"] = [this](const v2::service::BackendEnvelope& request) {
            v2::service::BackendEnvelope response;
            response.kind = v2::service::MessageKind::kError;
            response.error_code = -1004;

            if (request.payload.empty()) {
                response.payload = R"({"status":"error","reason":"empty_payload"})";
                return response;
            }

            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("token")) {
                response.payload = R"({"status":"error","reason":"invalid_json"})";
                return response;
            }

            std::string user_id = doc["user_id"].get<std::string>();
            std::string token = doc["token"].get<std::string>();
            std::string display_name = doc.value("display_name", user_id);

            if (user_id.empty()) {
                response.payload = R"({"status":"error","reason":"empty_user_id"})";
                return response;
            }

            if (token.empty()) {
                response.payload = R"({"status":"error","reason":"empty_token"})";
                return response;
            }

            std::lock_guard<std::mutex> lock(mutex_);

            bool is_duplicate = active_sessions_.contains(user_id);
            active_sessions_[user_id] = token;

            response.kind = v2::service::MessageKind::kResponse;
            nlohmann::json body{
                {"status", "ok"},
                {"user_id", user_id},
                {"display_name", display_name},
                {"is_duplicate", is_duplicate},
                {"backend_id", backend_id},
            };
            response.payload = body.dump();
            return response;
        };

        server = std::make_unique<v2::service::BackendServer>(0, std::move(handlers));
        server->start();
        port = server->local_port();
        return port > 0;
    }

    void stop() {
        if (server) server->stop();
    }
};

}  // namespace

// ─── Tests ────────────────────────────────────────────────────────

TEST(V2BackendRoutingTest, BasicRoundTrip) {
    app::logging::init("project_tests");

    BackendProcess backend;
    ASSERT_TRUE(backend.start());

    BridgeGateway gateway;
    ASSERT_TRUE(gateway.start(0, backend.port));

    TestClient client;
    client.connect(gateway.local_port());

    auto response = client.exchange(net::protocol::kLoginRequest, 1,
                                    "alice|token:alice_secret|Alice");
    EXPECT_EQ(response.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(response.body, "login_ok:alice");

    client.close();
    gateway.stop();
    backend.stop();
}

TEST(V2BackendRoutingTest, BackendUnreachable) {
    app::logging::init("project_tests");

    // Start backend first so gateway can connect, then stop it
    BackendProcess backend;
    ASSERT_TRUE(backend.start());

    BridgeGateway gateway;
    ASSERT_TRUE(gateway.start(0, backend.port));

    // Stop the backend to simulate crash/disconnect
    backend.stop();

    TestClient client;
    client.connect(gateway.local_port());

    auto response = client.exchange(net::protocol::kLoginRequest, 2,
                                    "bob|token:bob_secret|Bob");
    // Should get an error since backend is no longer running
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);

    client.close();
    gateway.stop();
}

TEST(V2BackendRoutingTest, BackendReturnsError) {
    app::logging::init("project_tests");

    BackendProcess backend;
    ASSERT_TRUE(backend.start());

    BridgeGateway gateway;
    ASSERT_TRUE(gateway.start(0, backend.port));

    TestClient client;
    client.connect(gateway.local_port());

    // Send message with message_id=0 which maps to "reject_empty" handler
    auto response = client.exchange(0, 3, "");
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(response.body, "rejected");

    client.close();
    gateway.stop();
    backend.stop();
}

TEST(V2BackendRoutingTest, GatewayServiceBridgePreservesErrorPayload) {
    app::logging::init("project_tests");

    BackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = backend.port,
        },
        std::nullopt, std::nullopt,
        std::nullopt, std::nullopt, metrics);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "reject_empty",
                               "");
    EXPECT_FALSE(result.success);
    EXPECT_EQ(result.error, v2::service::ServiceErrorCode::kInvalidRequest);
    EXPECT_EQ(result.response_payload, "rejected");

    bridge.shutdown();
    backend.stop();
}

// ─── S2: DemoServer + GatewayServiceBridge tests ──────────────────

#define SKIP_IF_V2_RUNTIME_UNAVAILABLE(server_ptr, startup_error)          \
    do {                                                                   \
        if (!(server_ptr)) {                                               \
            GTEST_SKIP() << "socket bind unavailable in this environment: " \
                         << (startup_error);                               \
        }                                                                  \
    } while (false)

TEST(V2BackendRoutingTest, LoginViaBridgeSuccess) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient client;
    client.connect(server->local_port());

    auto response = client.exchange(net::protocol::kLoginRequest, 1,
                                    "alice|token:alice_secret|Alice");
    EXPECT_EQ(response.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(response.body, "login_ok:alice");

    client.close();
    server->stop();
    backend.stop();
}

TEST(V2BackendRoutingTest, LoginViaBridgeInvalidToken) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient client;
    client.connect(server->local_port());

    // Empty token (body: "bob||Bob" → token="")
    auto response = client.exchange(net::protocol::kLoginRequest, 2, "bob||Bob");
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);

    client.close();
    server->stop();
    backend.stop();
}

TEST(V2BackendRoutingTest, LoginViaBridgeBackendUnreachable) {
    app::logging::init("project_tests");

    // Point to a port where nothing listens
    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = 19999,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient client;
    client.connect(server->local_port());

    auto response = client.exchange(net::protocol::kLoginRequest, 3,
                                    "alice|token:alice_secret|Alice");
    EXPECT_EQ(response.message_id, net::protocol::kErrorResponse);

    client.close();
    server->stop();
}

TEST(V2BackendRoutingTest, LoginViaBridgeDuplicateLogin) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient session1;
    TestClient session2;
    session1.connect(server->local_port());
    session2.connect(server->local_port());

    // First login succeeds
    auto login1 = session1.exchange(net::protocol::kLoginRequest, 10,
                                    "alice|token:alice_secret|Alice");
    EXPECT_EQ(login1.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(login1.body, "login_ok:alice");

    // Second login with same user_id triggers duplicate detection
    auto login2 = session2.exchange(net::protocol::kLoginRequest, 11,
                                    "alice|token:alice_secret|Alice");
    EXPECT_EQ(login2.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(login2.body, "login_ok:alice");

    // Session 1 should receive a kick push
    auto kick = session1.expect_message(net::protocol::kSessionKickedPush);
    EXPECT_EQ(kick.message_id, net::protocol::kSessionKickedPush);

    session1.close();
    session2.close();
    server->stop();
    backend.stop();
}

// ─── S3: Room/Battle backend processes ────────────────────────────

struct RoomBackendProcess {
    std::unique_ptr<v2::service::BackendServer> server;
    std::uint16_t port = 0;

    struct RoomState {
        std::string room_id;
        std::string owner_user_id;
        std::vector<std::string> members;
        std::unordered_map<std::string, bool> ready_state;
        std::string active_battle_id;
    };
    std::unordered_map<std::string, RoomState> rooms_;
    std::mutex mutex_;

    RoomState* find(const std::string& room_id) {
        auto it = rooms_.find(room_id);
        return it != rooms_.end() ? &it->second : nullptr;
    }

    bool all_ready(const RoomState& room) {
        return room.members.size() >= 2 &&
               std::all_of(room.members.begin(), room.members.end(),
                           [&](const std::string& uid) {
                               auto it = room.ready_state.find(uid);
                               return it != room.ready_state.end() && it->second;
                           });
    }

    bool start() {
        v2::service::BackendServer::HandlerMap handlers;

        handlers["room_create"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -1004;
                resp.payload = R"({"status":"error","reason":"invalid_json"})";
                return resp;
            }
            std::string user_id = doc["user_id"].get<std::string>();
            std::string room_id = doc["room_id"].get<std::string>();

            std::lock_guard<std::mutex> lock(mutex_);
            if (find(room_id)) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2002;
                resp.payload = R"({"status":"error","reason":"room_already_exists"})";
                return resp;
            }
            RoomState room;
            room.room_id = room_id;
            room.owner_user_id = user_id;
            room.members.push_back(user_id);
            room.ready_state[user_id] = false;
            rooms_[room_id] = std::move(room);

            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = R"({"status":"ok","room_id":")" + room_id + R"(","member_count":1})";
            return resp;
        };

        handlers["room_join"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            if (doc.is_discarded() || !doc.contains("user_id") || !doc.contains("room_id")) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -1004;
                resp.payload = R"({"status":"error","reason":"invalid_json"})";
                return resp;
            }
            std::string user_id = doc["user_id"].get<std::string>();
            std::string room_id = doc["room_id"].get<std::string>();

            std::lock_guard<std::mutex> lock(mutex_);
            auto* room = find(room_id);
            if (!room) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2003;
                resp.payload = R"({"status":"error","reason":"room_not_found"})";
                return resp;
            }
            auto member_it = std::find(room->members.begin(), room->members.end(), user_id);
            if (member_it == room->members.end()) {
                room->members.push_back(user_id);
                room->ready_state[user_id] = false;
            }
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = R"({"status":"ok","room_id":")" + room_id + R"(","member_count":)" +
                std::to_string(room->members.size()) + "}";
            return resp;
        };

        handlers["room_ready"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            if (doc.is_discarded() || !doc.contains("user_id")) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -1004;
                resp.payload = R"({"status":"error","reason":"invalid_json"})";
                return resp;
            }
            std::string user_id = doc["user_id"].get<std::string>();
            std::string room_id = doc.value("room_id", "");
            bool ready = doc.value("ready", true);

            std::lock_guard<std::mutex> lock(mutex_);
            auto* room = find(room_id);
            if (!room) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2003;
                resp.payload = R"({"status":"error","reason":"room_not_found"})";
                return resp;
            }
            room->ready_state[user_id] = ready;
            bool all_r = all_ready(*room);
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = R"({"status":"ok","room_id":")" + room_id +
                R"(","all_ready":)" + (all_r ? "true" : "false") + "}";
            return resp;
        };

        handlers["room_start_battle"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            std::string user_id = doc.value("user_id", "");
            std::string room_id = doc.value("room_id", "");

            std::lock_guard<std::mutex> lock(mutex_);
            auto* room = find(room_id);
            if (!room) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2003;
                resp.payload = R"({"status":"error","reason":"room_not_found"})";
                return resp;
            }
            if (room->members.size() < 2) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -3001;
                resp.payload = R"({"status":"error","reason":"not_enough_players"})";
                return resp;
            }

            nlohmann::json player_ids = nlohmann::json::array();
            for (const auto& m : room->members) player_ids.push_back(m);

            nlohmann::json forward_payload{
                {"battle_id", "battle_" + room_id},
                {"room_id", room_id},
                {"player_ids", player_ids},
                {"max_frames", 5},
            };

            nlohmann::json body{
                {"status", "ok"},
                {"room_id", room_id},
                {"player_ids", player_ids},
                {"forward", nlohmann::json{
                    {"target", "battle"},
                    {"message_type", "battle_create"},
                    {"payload", std::move(forward_payload)},
                }},
            };
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = body.dump();
            return resp;
        };

        server = std::make_unique<v2::service::BackendServer>(0, std::move(handlers));
        server->start();
        port = server->local_port();
        return port > 0;
    }

    void stop() { if (server) server->stop(); }
};

struct BattleBackendProcess {
    std::unique_ptr<v2::service::BackendServer> server;
    std::uint16_t port = 0;

    struct BattleEntry {
        std::unique_ptr<v2::ecs::World> world;
        std::string battle_id;
        std::string room_id;
    };
    std::unordered_map<std::string, BattleEntry> battles_;
    std::mutex mutex_;

    bool start() {
        v2::service::BackendServer::HandlerMap handlers;

        handlers["battle_create"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            if (doc.is_discarded()) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -1004;
                resp.payload = R"({"status":"error","reason":"invalid_json"})";
                return resp;
            }
            std::string battle_id = doc.value("battle_id", "battle_001");
            std::string room_id = doc.value("room_id", "");
            std::uint32_t max_frames = doc.value("max_frames", 0);
            std::vector<std::string> player_ids;
            if (doc.contains("player_ids") && doc["player_ids"].is_array()) {
                for (const auto& p : doc["player_ids"]) player_ids.push_back(p.get<std::string>());
            }

            std::lock_guard<std::mutex> lock(mutex_);
            if (battles_.contains(battle_id)) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2004;
                resp.payload = R"({"status":"error","reason":"battle_already_exists"})";
                return resp;
            }

            auto world = v2::battle::create_battle_world(battle_id, room_id, player_ids, max_frames);
            battles_[battle_id] = {std::move(world), battle_id, room_id};

            nlohmann::json push{
                {"kind", "battle_started"},
                {"battle_id", battle_id},
                {"room_id", room_id},
                {"player_ids", doc["player_ids"]},
            };

            nlohmann::json body{
                {"status", "ok"},
                {"battle_id", battle_id},
                {"room_id", room_id},
                {"player_ids", doc["player_ids"]},
                {"push_to_sessions", nlohmann::json::array({push})},
            };
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = body.dump();
            return resp;
        };

        handlers["battle_input"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            if (doc.is_discarded()) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -1004;
                resp.payload = R"({"status":"error","reason":"invalid_json"})";
                return resp;
            }
            std::string user_id = doc.value("user_id", "");
            std::string input_data = doc.value("input_data", "");
            std::int64_t score = doc.value("score", 0);
            std::uint32_t submitted_frame = doc.value("submitted_frame", 0);
            // Resolve battle_id — use first battle if not specified
            std::string battle_id = doc.value("battle_id", "");
            if (battle_id.empty()) {
                std::lock_guard<std::mutex> lock(mutex_);
                if (!battles_.empty()) battle_id = battles_.begin()->first;
            }

            std::lock_guard<std::mutex> lock(mutex_);
            auto it = battles_.find(battle_id);
            if (it == battles_.end()) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2003;
                resp.payload = R"({"status":"error","reason":"battle_not_found"})";
                return resp;
            }

            auto* world = it->second.world.get();
            auto input_result = v2::battle::battle_world_process_input(
                *world, user_id, input_data, score, submitted_frame);
            if (!input_result.accepted) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -3002;
                resp.payload = R"({"status":"error","reason":")" + input_result.reject_reason + "\"}";
                return resp;
            }

            auto current_frame = v2::battle::battle_world_frame_number(*world);
            auto next_frame = current_frame + 1;
            auto frame_result = v2::battle::battle_world_advance_frame(
                *world, next_frame, "input:" + user_id + ":" + std::to_string(input_result.input_seq));

            nlohmann::json pushes = nlohmann::json::array();
            nlohmann::json frame_push{
                {"kind", "frame_advanced"},
                {"battle_id", battle_id},
                {"frame_number", frame_result.frame_number},
                {"trigger", frame_result.trigger},
            };

            auto snapshot = v2::battle::battle_world_snapshot(*world);
            nlohmann::json participants_json = nlohmann::json::array();
            for (const auto& p : snapshot.participants) {
                participants_json.push_back({
                    {"user_id", p.user_id},
                    {"online", p.online},
                    {"score", p.score},
                    {"pos_x", p.pos_x},
                    {"pos_y", p.pos_y},
                    {"hp", p.hp},
                });
            }
            frame_push["participants"] = std::move(participants_json);
            pushes.push_back(std::move(frame_push));

            if (frame_result.should_finish) {
                pushes.push_back({
                    {"kind", "battle_finished"},
                    {"battle_id", battle_id},
                    {"reason", v2::battle::to_string(frame_result.finish_reason)},
                    {"total_frames", snapshot.clock.frame_number},
                });
            }

            nlohmann::json body{
                {"status", "ok"},
                {"battle_id", battle_id},
                {"input_seq", input_result.input_seq},
                {"frame_number", frame_result.frame_number},
                {"should_finish", frame_result.should_finish},
                {"push_to_sessions", std::move(pushes)},
            };
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = body.dump();
            return resp;
        };

        handlers["battle_finish"] = [this](const v2::service::BackendEnvelope& request) {
            auto doc = nlohmann::json::parse(request.payload, nullptr, false);
            std::string user_id = doc.value("user_id", "");
            std::string battle_id = doc.value("battle_id", "");
            if (battle_id.empty()) {
                std::lock_guard<std::mutex> lock(mutex_);
                if (!battles_.empty()) battle_id = battles_.begin()->first;
            }

            std::lock_guard<std::mutex> lock(mutex_);
            auto it = battles_.find(battle_id);
            if (it == battles_.end()) {
                v2::service::BackendEnvelope resp;
                resp.kind = v2::service::MessageKind::kError;
                resp.error_code = -2003;
                resp.payload = R"({"status":"error","reason":"battle_not_found"})";
                return resp;
            }

            auto* world = it->second.world.get();
            v2::battle::battle_world_set_lifecycle(
                *world, v2::battle::BattleLifecycleState::kFinished);

            nlohmann::json push{
                {"kind", "battle_finished"},
                {"battle_id", battle_id},
                {"reason", "user_requested"},
                {"total_frames", v2::battle::battle_world_frame_number(*world)},
            };

            nlohmann::json body{
                {"status", "ok"},
                {"battle_id", battle_id},
                {"reason", "user_requested"},
                {"push_to_sessions", nlohmann::json::array({push})},
            };
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = body.dump();
            return resp;
        };

        server = std::make_unique<v2::service::BackendServer>(0, std::move(handlers));
        server->start();
        port = server->local_port();
        return port > 0;
    }

    void stop() { if (server) server->stop(); }
};

// ─── S3 Integration Tests ────────────────────────────────────────

TEST(V2BackendRoutingTest, RoomCreateViaBridgeSuccess) {
    app::logging::init("project_tests");

    LoginBackendProcess login_backend;
    RoomBackendProcess room_backend;
    ASSERT_TRUE(login_backend.start());
    ASSERT_TRUE(room_backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = login_backend.port,
            },
            .room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = room_backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient client;
    client.connect(server->local_port());

    // Login first
    auto login = client.exchange(net::protocol::kLoginRequest, 100,
                                 "alice|token:alice_secret|Alice");
    EXPECT_EQ(login.message_id, net::protocol::kLoginResponse);

    // Create room
    auto create = client.exchange(net::protocol::kRoomCreateRequest, 101, "test_room");
    EXPECT_EQ(create.message_id, net::protocol::kRoomCreateResponse);
    EXPECT_EQ(create.body, "test_room");

    client.close();
    server->stop();
    login_backend.stop();
    room_backend.stop();
}

TEST(V2BackendRoutingTest, RoomJoinViaBridgeSuccess) {
    app::logging::init("project_tests");

    LoginBackendProcess login_backend;
    RoomBackendProcess room_backend;
    ASSERT_TRUE(login_backend.start());
    ASSERT_TRUE(room_backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = login_backend.port,
            },
            .room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = room_backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient alice;
    TestClient bob;
    alice.connect(server->local_port());
    bob.connect(server->local_port());

    // Login both
    alice.exchange(net::protocol::kLoginRequest, 100, "alice|token:a_secret|Alice");
    bob.exchange(net::protocol::kLoginRequest, 200, "bob|token:b_secret|Bob");

    // Alice creates room
    auto create = alice.exchange(net::protocol::kRoomCreateRequest, 101, "room_join_test");
    EXPECT_EQ(create.message_id, net::protocol::kRoomCreateResponse);

    // Bob joins room
    auto join = bob.exchange(net::protocol::kRoomJoinRequest, 201, "room_join_test");
    EXPECT_EQ(join.message_id, net::protocol::kRoomJoinResponse);
    EXPECT_EQ(join.body, "room_join_test");

    alice.close();
    bob.close();
    server->stop();
    login_backend.stop();
    room_backend.stop();
}

TEST(V2BackendRoutingTest, RoomReadyViaBridgeSuccess) {
    app::logging::init("project_tests");

    LoginBackendProcess login_backend;
    RoomBackendProcess room_backend;
    ASSERT_TRUE(login_backend.start());
    ASSERT_TRUE(room_backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = login_backend.port,
            },
            .room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = room_backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient alice;
    alice.connect(server->local_port());
    alice.exchange(net::protocol::kLoginRequest, 100, "alice|token:a_secret|Alice");

    // Create and ready up
    alice.exchange(net::protocol::kRoomCreateRequest, 101, "room_ready_test");
    auto ready = alice.exchange(net::protocol::kRoomReadyRequest, 102, "true");
    EXPECT_EQ(ready.message_id, net::protocol::kRoomReadyResponse);
    EXPECT_EQ(ready.body, "true");

    alice.close();
    server->stop();
    login_backend.stop();
    room_backend.stop();
}

TEST(V2BackendRoutingTest, BattleStartCascadeViaBridge) {
    app::logging::init("project_tests");

    LoginBackendProcess login_backend;
    RoomBackendProcess room_backend;
    BattleBackendProcess battle_backend;
    ASSERT_TRUE(login_backend.start());
    ASSERT_TRUE(room_backend.start());
    ASSERT_TRUE(battle_backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = login_backend.port,
            },
            .room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = room_backend.port,
            },
            .battle_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = battle_backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient alice;
    TestClient bob;
    alice.connect(server->local_port());
    bob.connect(server->local_port());

    // Login both
    alice.exchange(net::protocol::kLoginRequest, 100, "alice|token:a_secret|Alice");
    bob.exchange(net::protocol::kLoginRequest, 200, "bob|token:b_secret|Bob");

    // Alice creates room
    alice.exchange(net::protocol::kRoomCreateRequest, 101, "cascade_room");

    // Bob joins room
    bob.exchange(net::protocol::kRoomJoinRequest, 201, "cascade_room");

    // Both ready up
    alice.exchange(net::protocol::kRoomReadyRequest, 102, "true");
    bob.exchange(net::protocol::kRoomReadyRequest, 202, "true");

    // Alice starts battle → room_start_battle → forward → battle_create cascade
    auto start = alice.exchange(net::protocol::kBattleStartRequest, 103, "cascade_room");
    EXPECT_EQ(start.message_id, net::protocol::kBattleStartResponse);

    // Both clients should receive kBattleStatePush (broadcast from push_to_sessions)
    auto push_a = alice.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(push_a.message_id, net::protocol::kBattleStatePush);

    auto push_b = bob.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(push_b.message_id, net::protocol::kBattleStatePush);

    alice.close();
    bob.close();
    server->stop();
    login_backend.stop();
    room_backend.stop();
    battle_backend.stop();
}

TEST(V2BackendRoutingTest, BattleInputFrameAdvanceViaBridge) {
    app::logging::init("project_tests");

    LoginBackendProcess login_backend;
    RoomBackendProcess room_backend;
    BattleBackendProcess battle_backend;
    ASSERT_TRUE(login_backend.start());
    ASSERT_TRUE(room_backend.start());
    ASSERT_TRUE(battle_backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = login_backend.port,
            },
            .room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = room_backend.port,
            },
            .battle_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = battle_backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient alice;
    TestClient bob;
    alice.connect(server->local_port());
    bob.connect(server->local_port());

    // Login, create room, join, ready, start battle
    alice.exchange(net::protocol::kLoginRequest, 100, "alice|token:a_secret|Alice");
    bob.exchange(net::protocol::kLoginRequest, 200, "bob|token:b_secret|Bob");
    alice.exchange(net::protocol::kRoomCreateRequest, 101, "input_room");
    bob.exchange(net::protocol::kRoomJoinRequest, 201, "input_room");
    alice.exchange(net::protocol::kRoomReadyRequest, 102, "true");
    bob.exchange(net::protocol::kRoomReadyRequest, 202, "true");

    auto start = alice.exchange(net::protocol::kBattleStartRequest, 103, "input_room");
    EXPECT_EQ(start.message_id, net::protocol::kBattleStartResponse);

    // Drain initial battle state pushes
    alice.expect_message(net::protocol::kBattleStatePush);
    bob.expect_message(net::protocol::kBattleStatePush);

    // Alice sends battle input → backend processes → frame_advanced push broadcast
    auto input_resp = alice.exchange(net::protocol::kBattleInputRequest, 104, "move:10,20");
    EXPECT_EQ(input_resp.message_id, net::protocol::kBattleInputResponse);

    // Both should receive frame_advanced push
    auto frame_a = alice.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(frame_a.message_id, net::protocol::kBattleStatePush);

    auto frame_b = bob.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(frame_b.message_id, net::protocol::kBattleStatePush);

    alice.close();
    bob.close();
    server->stop();
    login_backend.stop();
    room_backend.stop();
    battle_backend.stop();
}

TEST(V2BackendRoutingTest, BattleFinishViaBridge) {
    app::logging::init("project_tests");

    LoginBackendProcess login_backend;
    RoomBackendProcess room_backend;
    BattleBackendProcess battle_backend;
    ASSERT_TRUE(login_backend.start());
    ASSERT_TRUE(room_backend.start());
    ASSERT_TRUE(battle_backend.start());

    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;
    try {
        v2::gateway::DemoServerOptions options{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = login_backend.port,
            },
            .room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = room_backend.port,
            },
            .battle_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = battle_backend.port,
            },
        };
        server = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server->start();
    } catch (const std::exception& ex) {
        startup_error = ex.what();
    }
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(server, startup_error);

    TestClient alice;
    TestClient bob;
    alice.connect(server->local_port());
    bob.connect(server->local_port());

    // Login, create room, join, ready, start battle
    alice.exchange(net::protocol::kLoginRequest, 100, "alice|token:a_secret|Alice");
    bob.exchange(net::protocol::kLoginRequest, 200, "bob|token:b_secret|Bob");
    alice.exchange(net::protocol::kRoomCreateRequest, 101, "finish_room");
    bob.exchange(net::protocol::kRoomJoinRequest, 201, "finish_room");
    alice.exchange(net::protocol::kRoomReadyRequest, 102, "true");
    bob.exchange(net::protocol::kRoomReadyRequest, 202, "true");

    auto start = alice.exchange(net::protocol::kBattleStartRequest, 103, "finish_room");
    EXPECT_EQ(start.message_id, net::protocol::kBattleStartResponse);

    // Drain battle state pushes
    alice.expect_message(net::protocol::kBattleStatePush);
    bob.expect_message(net::protocol::kBattleStatePush);

    // Alice sends finish request → backend processes → battle_finished push broadcast
    auto finish_resp = alice.exchange(net::protocol::kBattleInputRequest, 104, "surrender");
    EXPECT_EQ(finish_resp.message_id, net::protocol::kBattleInputResponse);

    // Both should receive battle_finished push
    auto done_a = alice.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(done_a.message_id, net::protocol::kBattleStatePush);

    auto done_b = bob.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(done_b.message_id, net::protocol::kBattleStatePush);

    alice.close();
    bob.close();
    server->stop();
    login_backend.stop();
    room_backend.stop();
    battle_backend.stop();
}

// ─── v3.0.0: ClusterRouter + GatewayServiceBridge integration ───────

TEST(V2BackendRoutingTest, ClusterRouterDiscoveryRoutesToBackend) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend.port, .node_name = "login-test-1"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        /*login_config=*/std::nullopt,
        /*room_config=*/std::nullopt,
        /*battle_config=*/std::nullopt,
        /*matchmaking_config=*/std::nullopt,
        /*leaderboard_config=*/std::nullopt,
        metrics);
    bridge.set_cluster_router(router);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"alice","token":"alice_secret","display_name":"Alice"})");
    EXPECT_TRUE(result.success) << "error=" << static_cast<int>(result.error);
    EXPECT_FALSE(result.response_payload.empty());

    auto snap = metrics->snapshot(v2::service::ServiceId::kLogin);
    EXPECT_GT(snap.total_requests, 0U);
    EXPECT_GT(snap.total_successes, 0U);

    bridge.shutdown();
    backend.stop();
}

TEST(V2BackendRoutingTest, ClusterRouterUnreachableMarkedUnhealthy) {
    app::logging::init("project_tests");

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = 19998, .node_name = "dead-login"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt, metrics);
    bridge.set_cluster_router(router);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"bob","token":"t","display_name":"B"})");
    EXPECT_FALSE(result.success);
    EXPECT_EQ(router->healthy_count("login"), 0U);
    EXPECT_EQ(router->unhealthy_count("login"), 1U);

    bridge.shutdown();
}

TEST(V2BackendRoutingTest, ClusterRouterFallbackToStaticConfig) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = backend.port,
        },
        std::nullopt, std::nullopt,
        std::nullopt, std::nullopt, metrics);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"alice","token":"alice_secret","display_name":"Alice"})");
    EXPECT_TRUE(result.success) << "error=" << static_cast<int>(result.error);

    bridge.shutdown();
    backend.stop();
}

// ─── v3.0.0: ShardRouter + consistent hashing integration ────────

TEST(V2BackendRoutingTest, ShardRouterRoutesWithShardKey) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend.port, .node_name = "login-shard-1"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto shard_router = std::make_shared<v3::cluster::ShardRouter>();
    shard_router->add_backend("login-shard-1");

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt, metrics);
    bridge.set_cluster_router(router);
    bridge.set_shard_router(shard_router);

    // Route with a shard_key (simulating room_id-based affinity)
    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"carol","token":"t3","display_name":"Carol"})",
                               "room_alpha");
    EXPECT_TRUE(result.success) << "error=" << static_cast<int>(result.error);
    EXPECT_FALSE(result.response_payload.empty());

    // Same shard_key should route to same backend (connection reused)
    auto result2 = bridge.route(v2::service::ServiceId::kLogin,
                                "login_request",
                                R"({"user_id":"dave","token":"t4","display_name":"Dave"})",
                                "room_alpha");
    EXPECT_TRUE(result2.success) << "error=" << static_cast<int>(result2.error);

    bridge.shutdown();
    backend.stop();
}

TEST(V2BackendRoutingTest, ShardRouterConsistentAffinityAcrossBackends) {
    app::logging::init("project_tests");

    LoginBackendProcess backend_a;
    LoginBackendProcess backend_b;
    backend_a.backend_id = "login-A";
    backend_b.backend_id = "login-B";
    ASSERT_TRUE(backend_a.start());
    ASSERT_TRUE(backend_b.start());

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend_a.port, .node_name = "login-A"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend_b.port, .node_name = "login-B"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto shard_router = std::make_shared<v3::cluster::ShardRouter>();
    shard_router->add_backend("login-A");
    shard_router->add_backend("login-B");

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt, metrics);
    bridge.set_cluster_router(router);
    bridge.set_shard_router(shard_router);

    // Route multiple times with same shard_key — all should succeed
    for (int i = 0; i < 5; ++i) {
        auto result = bridge.route(v2::service::ServiceId::kLogin,
                                   "login_request",
                                   R"({"user_id":"eve","token":"t5","display_name":"Eve"})",
                                   "battle_42");
        EXPECT_TRUE(result.success)
            << "iteration=" << i << " error=" << static_cast<int>(result.error);
    }

    // Different shard keys should also work
    auto r1 = bridge.route(v2::service::ServiceId::kLogin,
                           "login_request",
                           R"({"user_id":"frank","token":"t6","display_name":"Frank"})",
                           "room_x");
    EXPECT_TRUE(r1.success) << "error=" << static_cast<int>(r1.error);

    auto r2 = bridge.route(v2::service::ServiceId::kLogin,
                           "login_request",
                           R"({"user_id":"grace","token":"t7","display_name":"Grace"})",
                           "room_y");
    EXPECT_TRUE(r2.success) << "error=" << static_cast<int>(r2.error);

    // Both backends still healthy
    EXPECT_EQ(router->healthy_count("login"), 2U);

    bridge.shutdown();
    backend_a.stop();
    backend_b.stop();
}

TEST(V2BackendRoutingTest, ShardRouterWithoutShardKeyFallsBackToRoundRobin) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend.port, .node_name = "login-rr"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto shard_router = std::make_shared<v3::cluster::ShardRouter>();
    shard_router->add_backend("login-rr");

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt, metrics);
    bridge.set_cluster_router(router);
    bridge.set_shard_router(shard_router);

    // Route WITHOUT shard_key — falls back to round-robin discovery
    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"hank","token":"t8","display_name":"Hank"})");
    EXPECT_TRUE(result.success) << "error=" << static_cast<int>(result.error);

    bridge.shutdown();
    backend.stop();
}

// ─── v3.0.0: TLS config integration tests ───────────────────────────

TEST(V2BackendRoutingTest, ClusterRouterKeepsShardAffinityPerBackendNode) {
    app::logging::init("project_tests");

    LoginBackendProcess backend_a;
    LoginBackendProcess backend_b;
    backend_a.backend_id = "login-A";
    backend_b.backend_id = "login-B";
    ASSERT_TRUE(backend_a.start());
    ASSERT_TRUE(backend_b.start());

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend_a.port, .node_name = "login-A"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend_b.port, .node_name = "login-B"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto shard_router = std::make_shared<v3::cluster::ShardRouter>();
    shard_router->add_backend("login-A");
    shard_router->add_backend("login-B");

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt, metrics);
    bridge.set_cluster_router(router);
    bridge.set_shard_router(shard_router);

    std::optional<std::string> sticky_backend_id;
    for (int i = 0; i < 5; ++i) {
        auto result = bridge.route(v2::service::ServiceId::kLogin,
                                   "login_request",
                                   R"({"user_id":"eve","token":"t5","display_name":"Eve"})",
                                   "battle_42");
        ASSERT_TRUE(result.success) << "iteration=" << i;
        auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
        ASSERT_FALSE(doc.is_discarded());
        ASSERT_TRUE(doc.contains("backend_id"));
        const auto current_backend_id = doc["backend_id"].get<std::string>();
        if (!sticky_backend_id.has_value()) {
            sticky_backend_id = current_backend_id;
        } else {
            EXPECT_EQ(current_backend_id, *sticky_backend_id);
        }
    }

    std::unordered_set<std::string> seen_backends;
    for (int i = 0; i < 256 && seen_backends.size() < 2U; ++i) {
        auto result = bridge.route(v2::service::ServiceId::kLogin,
                                   "login_request",
                                   R"({"user_id":"frank","token":"t6","display_name":"Frank"})",
                                   "room_" + std::to_string(i) + "_" + std::to_string(i * 17 + 3));
        ASSERT_TRUE(result.success) << "shard=" << i;
        auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
        ASSERT_FALSE(doc.is_discarded());
        seen_backends.insert(doc.value("backend_id", ""));
    }
    EXPECT_EQ(seen_backends.size(), 2U);

    bridge.shutdown();
    backend_a.stop();
    backend_b.stop();
}

TEST(V2BackendRoutingTest, ClusterRouterRoundRobinUsesMultipleBackendNodes) {
    app::logging::init("project_tests");

    LoginBackendProcess backend_a;
    LoginBackendProcess backend_b;
    backend_a.backend_id = "login-rr-a";
    backend_b.backend_id = "login-rr-b";
    ASSERT_TRUE(backend_a.start());
    ASSERT_TRUE(backend_b.start());

    auto router = std::make_shared<v3::cluster::ClusterRouter>();
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend_a.port, .node_name = "login-rr-a"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });
    router->register_service(v3::cluster::ServiceInstance{
        .node = {.host = "127.0.0.1", .port = backend_b.port, .node_name = "login-rr-b"},
        .service_name = "login",
        .state = v3::cluster::ServiceState::kHealthy,
    });

    auto metrics = std::make_shared<v2::gateway::BackendMetrics>();
    v2::gateway::GatewayServiceBridge bridge(
        std::nullopt, std::nullopt, std::nullopt, std::nullopt, std::nullopt, metrics);
    bridge.set_cluster_router(router);

    std::unordered_set<std::string> seen_backends;
    for (int i = 0; i < 6; ++i) {
        auto result = bridge.route(v2::service::ServiceId::kLogin,
                                   "login_request",
                                   R"({"user_id":"hank","token":"t8","display_name":"Hank"})");
        ASSERT_TRUE(result.success) << "iteration=" << i;
        auto doc = nlohmann::json::parse(result.response_payload, nullptr, false);
        ASSERT_FALSE(doc.is_discarded());
        seen_backends.insert(doc.value("backend_id", ""));
    }
    EXPECT_EQ(seen_backends.size(), 2U);

    bridge.shutdown();
    backend_a.stop();
    backend_b.stop();
}

TEST(V2BackendRoutingTest, TlsConfigStoredAndAccessible) {
    app::logging::init("project_tests");

    v3::cluster::TlsSessionConfig tls_cfg;
    tls_cfg.verify_mode = v3::cluster::TlsVerifyMode::kNone;
    tls_cfg.min_version = v3::cluster::TlsSessionConfig::TlsVersion::k13;

    v2::service::BackendConnectionOptions opts{
        .host = "127.0.0.1",
        .port = 19999,
        .tls_config = tls_cfg,
    };

    v2::service::BackendConnection conn(opts);
    EXPECT_TRUE(conn.is_tls_enabled());

    // Connection without valid certs should fail gracefully (no crash)
    bool connected = conn.connect();
    EXPECT_FALSE(connected);

    // Verify close after failed TLS connect doesn't crash
    conn.close();
    SUCCEED();
}

TEST(V2BackendRoutingTest, TlsConfigDisabledRoutesNormally) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    // Explicitly no TLS config — the default BackendConnectionOptions path.
    v2::service::BackendConnectionOptions opts{
        .host = "127.0.0.1",
        .port = backend.port,
        .tls_config = std::nullopt,
    };

    v2::service::BackendConnection conn(opts);
    EXPECT_FALSE(conn.is_tls_enabled());
    EXPECT_TRUE(conn.connect());

    v2::service::BackendEnvelope request;
    request.target_service = v2::service::ServiceId::kLogin;
    request.kind = v2::service::MessageKind::kRequest;
    request.message_type = "login_request";
    request.payload = R"({"user_id":"iris","token":"t9","display_name":"Iris"})";

    auto response = conn.send_request(request);
    EXPECT_TRUE(response.has_value());
    if (response) {
        EXPECT_TRUE(response->kind == v2::service::MessageKind::kResponse ||
                    response->kind == v2::service::MessageKind::kError);
    }

    conn.close();
    backend.stop();
}

TEST(V2BackendRoutingTest, BackendTlsListenerCompletesLoginRequest) {
    app::logging::init("project_tests");

    // Check that TLS certificate files exist before proceeding.
    // On Windows CI environments, certificates may not be provisioned.
    {
        const auto& cert_path = "certs/server.crt";
        const auto& key_path = "certs/server.key";
        const auto& ca_path = "certs/ca.crt";
        auto file_exists = [](const char* path) {
            std::ifstream f(path);
            return f.good();
        };
        if (!file_exists(cert_path) || !file_exists(key_path) || !file_exists(ca_path)) {
            GTEST_SKIP() << "TLS certificate files not found in certs/ — skipping TLS test";
        }
    }

    v3::cluster::TlsSessionConfig tls_cfg;
    tls_cfg.cert.cert_chain_path = "certs/server.crt";
    tls_cfg.cert.private_key_path = "certs/server.key";
    tls_cfg.cert.ca_cert_path = "certs/ca.crt";
    tls_cfg.verify_mode = v3::cluster::TlsVerifyMode::kNone;
    tls_cfg.min_version = v3::cluster::TlsSessionConfig::TlsVersion::k12;

    // Windows CI environment may not have TLS certificates available.
    // Catch initialization failures and skip gracefully.
    std::unique_ptr<v2::service::BackendServer> server;
    std::string tls_error;
    try {
        server = std::make_unique<v2::service::BackendServer>(
            v2::service::BackendServerOptions{
                .port = 0,
                .tls_config = tls_cfg,
            },
            make_login_handlers());
        server->start();
    } catch (const std::exception& ex) {
        tls_error = ex.what();
    }
    if (!server || server->local_port() == 0) {
        GTEST_SKIP() << "TLS backend not available: " << tls_error;
    }
    ASSERT_GT(server->local_port(), 0);

    v2::service::BackendConnectionOptions opts{
        .host = "127.0.0.1",
        .port = server->local_port(),
        .tls_config = tls_cfg,
    };
    v2::service::BackendConnection conn(opts);
    ASSERT_TRUE(conn.connect());
    EXPECT_TRUE(conn.is_tls_enabled());

    v2::service::BackendEnvelope request;
    request.target_service = v2::service::ServiceId::kLogin;
    request.kind = v2::service::MessageKind::kRequest;
    request.message_type = "login_request";
    request.payload = "tls_user|token:tls_user";

    auto response = conn.send_request(request);
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->kind, v2::service::MessageKind::kResponse);
    EXPECT_EQ(response->payload, "login_ok:tls_user");

    conn.close();
    server->stop();
}

TEST(V2BackendRoutingTest, SecurityPolicyPerServiceDefaults) {
    // Verify SecurityPolicy per-service TLS policies are correctly configured.
    v3::cluster::SecurityPolicy policy;
    EXPECT_TRUE(policy.require_tls);

    // Login: mTLS not required (handles high-volume public auth)
    const auto* login_pol = policy.policy_for("login");
    ASSERT_NE(login_pol, nullptr);
    EXPECT_TRUE(login_pol->tls_required);
    EXPECT_FALSE(login_pol->mtls_required);

    // Leaderboard: mTLS required (PII data)
    const auto* lb_pol = policy.policy_for("leaderboard");
    ASSERT_NE(lb_pol, nullptr);
    EXPECT_TRUE(lb_pol->tls_required);
    EXPECT_TRUE(lb_pol->mtls_required);

    // Unknown service
    EXPECT_EQ(policy.policy_for("nonexistent"), nullptr);
}

TEST(V2BackendRoutingTest, SecurityPolicyAllowsPlaintextWhenGlobalTlsDisabled) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto flags = std::make_shared<v2::config::FeatureFlags>();
    flags->register_flag("v3_tls_enabled", 0, false);

    v3::cluster::SecurityPolicy policy;
    policy.require_tls = false;

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{"127.0.0.1", backend.port},
        std::nullopt, std::nullopt);
    bridge.set_feature_flags(flags);
    bridge.set_security_policy(policy);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"tls_fallback","token":"token:tls_fallback","display_name":"TLS Fallback"})");
    EXPECT_TRUE(result.success);

    bridge.shutdown();
    backend.stop();
}

// ─── v3.0.0 B4: Raft consensus integration tests ─────────────────────

TEST(V2BackendRoutingTest, RaftSingleNodeBecomesLeader) {
    app::logging::init("project_tests");

    v2::match::MatchmakingService matchmaking(0);

    v3::cluster::RaftConfig raft_cfg;
    raft_cfg.node_id = "matchmaker-1";
    raft_cfg.election_timeout_min = std::chrono::milliseconds(100);
    raft_cfg.election_timeout_max = std::chrono::milliseconds(200);
    raft_cfg.heartbeat_interval = std::chrono::milliseconds(50);
    raft_cfg.peers = {{"matchmaker-1", "127.0.0.1", 0}};

    matchmaking.set_raft_config(std::move(raft_cfg));
    matchmaking.start();

    // Single-node cluster: quorum = 1, becomes leader after election timeout.
    std::this_thread::sleep_for(std::chrono::milliseconds(500));

    EXPECT_TRUE(matchmaking.is_raft_leader());

    matchmaking.stop();
}

TEST(V2BackendRoutingTest, RaftRequestVoteHandlerRespondsToRpc) {
    app::logging::init("project_tests");

    v2::match::MatchmakingService matchmaking(0);

    v3::cluster::RaftConfig raft_cfg;
    raft_cfg.node_id = "matchmaker-2";
    raft_cfg.election_timeout_min = std::chrono::milliseconds(100);
    raft_cfg.election_timeout_max = std::chrono::milliseconds(200);
    raft_cfg.peers = {{"matchmaker-2", "127.0.0.1", 0}};

    matchmaking.set_raft_config(std::move(raft_cfg));
    matchmaking.start();

    auto port = matchmaking.local_port();
    ASSERT_GT(port, 0);

    v2::service::BackendConnectionOptions opts{
        .host = "127.0.0.1",
        .port = port,
    };
    v2::service::BackendConnection conn(opts);
    ASSERT_TRUE(conn.connect());

    // Send RequestVote RPC — dispatch is by message_type, target_service is arbitrary.
    v2::service::BackendEnvelope request;
    request.target_service = v2::service::ServiceId::kGateway;
    request.kind = v2::service::MessageKind::kRequest;
    request.message_type = "raft_request_vote";
    request.payload = R"({"term":1,"candidate_id":"peer-1","last_log_term":0,"last_log_index":0})";

    auto response = conn.send_request(request);
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->kind, v2::service::MessageKind::kResponse);

    auto doc = nlohmann::json::parse(response->payload, nullptr, false);
    EXPECT_FALSE(doc.is_discarded());
    EXPECT_TRUE(doc.contains("term"));
    EXPECT_TRUE(doc.contains("vote_granted"));

    conn.close();
    matchmaking.stop();
}

TEST(V2BackendRoutingTest, RaftAppendEntriesHandlerRespondsToRpc) {
    app::logging::init("project_tests");

    v2::match::MatchmakingService matchmaking(0);

    v3::cluster::RaftConfig raft_cfg;
    raft_cfg.node_id = "matchmaker-3";
    raft_cfg.election_timeout_min = std::chrono::milliseconds(100);
    raft_cfg.election_timeout_max = std::chrono::milliseconds(200);
    raft_cfg.peers = {{"matchmaker-3", "127.0.0.1", 0}};

    matchmaking.set_raft_config(std::move(raft_cfg));
    matchmaking.start();

    auto port = matchmaking.local_port();
    ASSERT_GT(port, 0);

    v2::service::BackendConnectionOptions opts{
        .host = "127.0.0.1",
        .port = port,
    };
    v2::service::BackendConnection conn(opts);
    ASSERT_TRUE(conn.connect());

    // Send AppendEntries (heartbeat) RPC.
    v2::service::BackendEnvelope request;
    request.target_service = v2::service::ServiceId::kGateway;
    request.kind = v2::service::MessageKind::kRequest;
    request.message_type = "raft_append_entries";
    request.payload = R"({"term":1,"leader_id":"peer-1"})";

    auto response = conn.send_request(request);
    ASSERT_TRUE(response.has_value());
    EXPECT_EQ(response->kind, v2::service::MessageKind::kResponse);

    auto doc = nlohmann::json::parse(response->payload, nullptr, false);
    EXPECT_FALSE(doc.is_discarded());
    EXPECT_TRUE(doc.contains("term"));
    EXPECT_TRUE(doc.contains("success"));

    conn.close();
    matchmaking.stop();
}

TEST(V2BackendRoutingTest, MatchmakingServicesElectSingleLeaderOverBackendRpc) {
    app::logging::init("project_tests");

    constexpr std::uint16_t kPort1 = 19431;
    constexpr std::uint16_t kPort2 = 19432;
    constexpr std::uint16_t kPort3 = 19433;

    const std::vector<v3::cluster::RaftNodeId> peers = {
        {"match-1", "127.0.0.1", kPort1},
        {"match-2", "127.0.0.1", kPort2},
        {"match-3", "127.0.0.1", kPort3},
    };

    v2::match::MatchmakingService node1(kPort1);
    v2::match::MatchmakingService node2(kPort2);
    v2::match::MatchmakingService node3(kPort3);

    auto make_cfg = [&](const std::string& node_id) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = node_id;
        cfg.peers = peers;
        cfg.election_timeout_min = std::chrono::milliseconds(150);
        cfg.election_timeout_max = std::chrono::milliseconds(300);
        cfg.heartbeat_interval = std::chrono::milliseconds(50);
        return cfg;
    };

    node1.set_raft_config(make_cfg("match-1"));
    node2.set_raft_config(make_cfg("match-2"));
    node3.set_raft_config(make_cfg("match-3"));

    node1.start();
    node2.start();
    node3.start();

    int leaders = 0;
    for (int i = 0; i < 80; ++i) {
        leaders = static_cast<int>(node1.is_raft_leader()) +
                  static_cast<int>(node2.is_raft_leader()) +
                  static_cast<int>(node3.is_raft_leader());
        if (leaders == 1) {
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    EXPECT_EQ(leaders, 1);

    node1.stop();
    node2.stop();
    node3.stop();
}

TEST(V2BackendRoutingTest, LeaderboardReplicatesCommittedScoresAcrossRaftFollowers) {
    app::logging::init("project_tests");
    DisableRedisAutoConnectForTest redis_guard;

    constexpr std::uint16_t kPort1 = 19441;
    constexpr std::uint16_t kPort2 = 19442;
    constexpr std::uint16_t kPort3 = 19443;

    const std::vector<v3::cluster::RaftNodeId> peers = {
        {"lb-1", "127.0.0.1", kPort1},
        {"lb-2", "127.0.0.1", kPort2},
        {"lb-3", "127.0.0.1", kPort3},
    };

    v2::leaderboard::LeaderboardService node1(kPort1);
    v2::leaderboard::LeaderboardService node2(kPort2);
    v2::leaderboard::LeaderboardService node3(kPort3);

    auto make_cfg = [&](const std::string& node_id) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = node_id;
        cfg.peers = peers;
        cfg.election_timeout_min = std::chrono::milliseconds(150);
        cfg.election_timeout_max = std::chrono::milliseconds(300);
        cfg.heartbeat_interval = std::chrono::milliseconds(50);
        return cfg;
    };

    node1.set_raft_config(make_cfg("lb-1"));
    node2.set_raft_config(make_cfg("lb-2"));
    node3.set_raft_config(make_cfg("lb-3"));

    node1.start();
    node2.start();
    node3.start();

    int leaders = 0;
    v2::leaderboard::LeaderboardService* leader = nullptr;
    std::uint16_t leader_port = 0;
    std::uint16_t follower_port = 0;
    for (int i = 0; i < 80; ++i) {
        leaders = static_cast<int>(node1.is_raft_leader()) +
                  static_cast<int>(node2.is_raft_leader()) +
                  static_cast<int>(node3.is_raft_leader());
        if (leaders == 1) {
            if (node1.is_raft_leader()) {
                leader = &node1;
                leader_port = kPort1;
                follower_port = kPort2;
            } else if (node2.is_raft_leader()) {
                leader = &node2;
                leader_port = kPort2;
                follower_port = kPort1;
            } else {
                leader = &node3;
                leader_port = kPort3;
                follower_port = kPort1;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    ASSERT_EQ(leaders, 1);
    ASSERT_NE(leader, nullptr);

    v2::service::BackendConnection leader_conn({
        .host = "127.0.0.1",
        .port = leader_port,
    });
    ASSERT_TRUE(leader_conn.connect());

    v2::service::BackendEnvelope submit_request;
    submit_request.target_service = v2::service::ServiceId::kGateway;
    submit_request.kind = v2::service::MessageKind::kRequest;
    submit_request.message_type = "leaderboard_submit";
    submit_request.payload =
        R"({"user_id":"alice","display_name":"Alice","score":1500})";

    auto submit_response = leader_conn.send_request(submit_request);
    ASSERT_TRUE(submit_response.has_value());
    EXPECT_EQ(submit_response->kind, v2::service::MessageKind::kResponse);
    leader_conn.close();

    v2::service::BackendConnection follower_conn({
        .host = "127.0.0.1",
        .port = follower_port,
    });
    ASSERT_TRUE(follower_conn.connect());

    v2::service::BackendEnvelope rank_request;
    rank_request.target_service = v2::service::ServiceId::kGateway;
    rank_request.kind = v2::service::MessageKind::kRequest;
    rank_request.message_type = "leaderboard_rank";
    rank_request.payload = R"({"user_id":"alice"})";

    std::optional<v2::service::BackendEnvelope> rank_response;
    for (int i = 0; i < 30; ++i) {
        rank_response = follower_conn.send_request(rank_request);
        if (rank_response.has_value() &&
            rank_response->kind == v2::service::MessageKind::kResponse) {
            auto doc = nlohmann::json::parse(rank_response->payload, nullptr, false);
            if (!doc.is_discarded() && doc.value("rank", 0) == 1) {
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    ASSERT_TRUE(rank_response.has_value());
    EXPECT_EQ(rank_response->kind, v2::service::MessageKind::kResponse);
    auto rank_doc = nlohmann::json::parse(rank_response->payload, nullptr, false);
    ASSERT_FALSE(rank_doc.is_discarded());
    EXPECT_EQ(rank_doc.value("user_id", std::string{}), "alice");
    EXPECT_EQ(rank_doc.value("rank", 0), 1);
    EXPECT_EQ(rank_doc.value("score", 0), 1500);

    follower_conn.close();
    node1.stop();
    node2.stop();
    node3.stop();
}

TEST(V2BackendRoutingTest, MatchmakingReplicatesQueuedPlayersAndMatchesAcrossFollowers) {
    app::logging::init("project_tests");

    constexpr std::uint16_t kPort1 = 19451;
    constexpr std::uint16_t kPort2 = 19452;
    constexpr std::uint16_t kPort3 = 19453;

    const std::vector<v3::cluster::RaftNodeId> peers = {
        {"match-1", "127.0.0.1", kPort1},
        {"match-2", "127.0.0.1", kPort2},
        {"match-3", "127.0.0.1", kPort3},
    };

    v2::match::MatchmakingService node1(kPort1);
    v2::match::MatchmakingService node2(kPort2);
    v2::match::MatchmakingService node3(kPort3);

    auto make_cfg = [&](const std::string& node_id) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = node_id;
        cfg.peers = peers;
        cfg.election_timeout_min = std::chrono::milliseconds(150);
        cfg.election_timeout_max = std::chrono::milliseconds(300);
        cfg.heartbeat_interval = std::chrono::milliseconds(50);
        return cfg;
    };

    node1.set_raft_config(make_cfg("match-1"));
    node2.set_raft_config(make_cfg("match-2"));
    node3.set_raft_config(make_cfg("match-3"));

    node1.start();
    node2.start();
    node3.start();

    int leaders = 0;
    std::uint16_t leader_port = 0;
    std::uint16_t follower_port = 0;
    for (int i = 0; i < 80; ++i) {
        leaders = static_cast<int>(node1.is_raft_leader()) +
                  static_cast<int>(node2.is_raft_leader()) +
                  static_cast<int>(node3.is_raft_leader());
        if (leaders == 1) {
            if (node1.is_raft_leader()) {
                leader_port = kPort1;
                follower_port = kPort2;
            } else if (node2.is_raft_leader()) {
                leader_port = kPort2;
                follower_port = kPort1;
            } else {
                leader_port = kPort3;
                follower_port = kPort1;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    ASSERT_EQ(leaders, 1);

    v2::service::BackendConnection leader_conn({
        .host = "127.0.0.1",
        .port = leader_port,
    });
    ASSERT_TRUE(leader_conn.connect());

    auto send_join = [&](const std::string& user_id, int mmr) {
        v2::service::BackendEnvelope request;
        request.target_service = v2::service::ServiceId::kGateway;
        request.kind = v2::service::MessageKind::kRequest;
        request.message_type = "match_join";
        request.payload = nlohmann::json{
            {"user_id", user_id},
            {"mmr", mmr},
            {"mode", "1v1"},
        }.dump();
        return leader_conn.send_request(request);
    };

    auto join_one = send_join("alice", 1000);
    ASSERT_TRUE(join_one.has_value());
    EXPECT_EQ(join_one->kind, v2::service::MessageKind::kResponse);

    auto join_two = send_join("bob", 1010);
    ASSERT_TRUE(join_two.has_value());
    EXPECT_EQ(join_two->kind, v2::service::MessageKind::kResponse);
    leader_conn.close();

    v2::service::BackendConnection follower_conn({
        .host = "127.0.0.1",
        .port = follower_port,
    });
    ASSERT_TRUE(follower_conn.connect());

    v2::service::BackendEnvelope status_request;
    status_request.target_service = v2::service::ServiceId::kGateway;
    status_request.kind = v2::service::MessageKind::kRequest;
    status_request.message_type = "match_status";
    status_request.payload = R"({"user_id":"alice","mode":"1v1"})";

    std::optional<v2::service::BackendEnvelope> status_response;
    for (int i = 0; i < 40; ++i) {
        status_response = follower_conn.send_request(status_request);
        if (status_response.has_value() &&
            status_response->kind == v2::service::MessageKind::kResponse) {
            auto doc = nlohmann::json::parse(status_response->payload, nullptr, false);
            if (!doc.is_discarded() && doc.value("matched", false)) {
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }

    ASSERT_TRUE(status_response.has_value());
    EXPECT_EQ(status_response->kind, v2::service::MessageKind::kResponse);
    auto status_doc = nlohmann::json::parse(status_response->payload, nullptr, false);
    ASSERT_FALSE(status_doc.is_discarded());
    EXPECT_TRUE(status_doc.value("matched", false));
    EXPECT_EQ(status_doc.value("mode", std::string{}), "1v1");
    EXPECT_GT(status_doc.value("match_id", std::string{}).size(), 0U);

    follower_conn.close();
    node1.stop();
    node2.stop();
    node3.stop();
}

TEST(V2BackendRoutingTest, MatchmakingReplicatesExpiredQueuePurgeAcrossFollowers) {
    app::logging::init("project_tests");

    constexpr std::uint16_t kPort1 = 19461;
    constexpr std::uint16_t kPort2 = 19462;
    constexpr std::uint16_t kPort3 = 19463;

    const std::vector<v3::cluster::RaftNodeId> peers = {
        {"match-expire-1", "127.0.0.1", kPort1},
        {"match-expire-2", "127.0.0.1", kPort2},
        {"match-expire-3", "127.0.0.1", kPort3},
    };

    v2::match::MatchmakingConfig match_cfg;
    match_cfg.max_wait_ms = 50;
    match_cfg.match_check_interval_ms = 50;

    v2::match::MatchmakingService node1(kPort1);
    v2::match::MatchmakingService node2(kPort2);
    v2::match::MatchmakingService node3(kPort3);
    node1.set_matchmaking_config(match_cfg);
    node2.set_matchmaking_config(match_cfg);
    node3.set_matchmaking_config(match_cfg);

    auto make_cfg = [&](const std::string& node_id) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = node_id;
        cfg.peers = peers;
        cfg.election_timeout_min = std::chrono::milliseconds(150);
        cfg.election_timeout_max = std::chrono::milliseconds(300);
        cfg.heartbeat_interval = std::chrono::milliseconds(50);
        return cfg;
    };

    node1.set_raft_config(make_cfg("match-expire-1"));
    node2.set_raft_config(make_cfg("match-expire-2"));
    node3.set_raft_config(make_cfg("match-expire-3"));

    node1.start();
    node2.start();
    node3.start();

    int leaders = 0;
    std::uint16_t leader_port = 0;
    std::uint16_t follower_port = 0;
    for (int i = 0; i < 80; ++i) {
        leaders = static_cast<int>(node1.is_raft_leader()) +
                  static_cast<int>(node2.is_raft_leader()) +
                  static_cast<int>(node3.is_raft_leader());
        if (leaders == 1) {
            if (node1.is_raft_leader()) {
                leader_port = kPort1;
                follower_port = kPort2;
            } else if (node2.is_raft_leader()) {
                leader_port = kPort2;
                follower_port = kPort1;
            } else {
                leader_port = kPort3;
                follower_port = kPort1;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    ASSERT_EQ(leaders, 1);

    v2::service::BackendConnection leader_conn({
        .host = "127.0.0.1",
        .port = leader_port,
    });
    ASSERT_TRUE(leader_conn.connect());

    const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();

    v2::service::BackendEnvelope join_request;
    join_request.target_service = v2::service::ServiceId::kGateway;
    join_request.kind = v2::service::MessageKind::kRequest;
    join_request.message_type = "match_join";
    join_request.payload = nlohmann::json{
        {"user_id", "stale-player"},
        {"mmr", 1000},
        {"mode", "1v1"},
        {"queued_at_ms", static_cast<std::uint64_t>(now_ms - 200)},
    }.dump();

    auto join_response = leader_conn.send_request(join_request);
    ASSERT_TRUE(join_response.has_value());
    EXPECT_EQ(join_response->kind, v2::service::MessageKind::kResponse);
    leader_conn.close();

    v2::service::BackendConnection follower_conn({
        .host = "127.0.0.1",
        .port = follower_port,
    });
    ASSERT_TRUE(follower_conn.connect());

    v2::service::BackendEnvelope status_request;
    status_request.target_service = v2::service::ServiceId::kGateway;
    status_request.kind = v2::service::MessageKind::kRequest;
    status_request.message_type = "match_status";
    status_request.payload = R"({"user_id":"stale-player","mode":"1v1"})";

    std::optional<v2::service::BackendEnvelope> status_response;
    for (int i = 0; i < 40; ++i) {
        status_response = follower_conn.send_request(status_request);
        if (status_response.has_value() &&
            status_response->kind == v2::service::MessageKind::kResponse) {
            auto doc = nlohmann::json::parse(status_response->payload, nullptr, false);
            if (!doc.is_discarded() &&
                !doc.value("matched", false) &&
                doc.value("queue_size", 1) == 0) {
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    ASSERT_TRUE(status_response.has_value());
    EXPECT_EQ(status_response->kind, v2::service::MessageKind::kResponse);
    auto status_doc = nlohmann::json::parse(status_response->payload, nullptr, false);
    ASSERT_FALSE(status_doc.is_discarded());
    EXPECT_FALSE(status_doc.value("matched", true));
    EXPECT_EQ(status_doc.value("queue_size", 1), 0);

    follower_conn.close();
    node1.stop();
    node2.stop();
    node3.stop();
}

TEST(V2BackendRoutingTest, LeaderboardRestoresCommittedScoresAfterRestart) {
    app::logging::init("project_tests");
    DisableRedisAutoConnectForTest redis_guard;

    const auto storage_root =
        std::filesystem::temp_directory_path() / "boost_lb_restart_test";
    std::error_code ec;
    std::filesystem::remove_all(storage_root, ec);
    std::filesystem::create_directories(storage_root, ec);

    auto make_cfg = [&](std::uint16_t port) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = "lb-restart";
        cfg.peers = {{"lb-restart", "127.0.0.1", port}};
        cfg.storage_dir = storage_root.string();
        cfg.election_timeout_min = std::chrono::milliseconds(50);
        cfg.election_timeout_max = std::chrono::milliseconds(100);
        cfg.heartbeat_interval = std::chrono::milliseconds(20);
        return cfg;
    };

    {
        v2::leaderboard::LeaderboardService service(19471);
        service.set_raft_config(make_cfg(19471));
        service.start();

        for (int i = 0; i < 40 && !service.is_raft_leader(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
        ASSERT_TRUE(service.is_raft_leader());

        v2::service::BackendConnection conn({
            .host = "127.0.0.1",
            .port = 19471,
        });
        ASSERT_TRUE(conn.connect());

        v2::service::BackendEnvelope submit_request;
        submit_request.target_service = v2::service::ServiceId::kGateway;
        submit_request.kind = v2::service::MessageKind::kRequest;
        submit_request.message_type = "leaderboard_submit";
        submit_request.payload =
            R"({"user_id":"alice","display_name":"Alice","score":1700})";

        auto submit_response = conn.send_request(submit_request);
        ASSERT_TRUE(submit_response.has_value());
        EXPECT_EQ(submit_response->kind, v2::service::MessageKind::kResponse);
        conn.close();
        service.stop();
    }

    {
        v2::leaderboard::LeaderboardService recovered(19471);
        recovered.set_raft_config(make_cfg(19471));
        recovered.start();

        v2::service::BackendConnection conn({
            .host = "127.0.0.1",
            .port = 19471,
        });
        ASSERT_TRUE(conn.connect());

        v2::service::BackendEnvelope rank_request;
        rank_request.target_service = v2::service::ServiceId::kGateway;
        rank_request.kind = v2::service::MessageKind::kRequest;
        rank_request.message_type = "leaderboard_rank";
        rank_request.payload = R"({"user_id":"alice"})";

        auto rank_response = conn.send_request(rank_request);
        ASSERT_TRUE(rank_response.has_value());
        EXPECT_EQ(rank_response->kind, v2::service::MessageKind::kResponse);
        auto rank_doc = nlohmann::json::parse(rank_response->payload, nullptr, false);
        ASSERT_FALSE(rank_doc.is_discarded());
        EXPECT_EQ(rank_doc.value("user_id", std::string{}), "alice");
        EXPECT_EQ(rank_doc.value("rank", 0), 1);
        EXPECT_EQ(rank_doc.value("score", 0), 1700);

        conn.close();
        recovered.stop();
    }

    std::filesystem::remove_all(storage_root, ec);
}

TEST(V2BackendRoutingTest, MatchmakingRestoresCommittedMatchAfterRestart) {
    app::logging::init("project_tests");

    const auto storage_root =
        std::filesystem::temp_directory_path() / "boost_match_restart_test";
    std::error_code ec;
    std::filesystem::remove_all(storage_root, ec);
    std::filesystem::create_directories(storage_root, ec);

    v2::match::MatchmakingConfig match_cfg;
    match_cfg.match_check_interval_ms = 50;

    auto make_cfg = [&](std::uint16_t port) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = "match-restart";
        cfg.peers = {{"match-restart", "127.0.0.1", port}};
        cfg.storage_dir = storage_root.string();
        cfg.election_timeout_min = std::chrono::milliseconds(50);
        cfg.election_timeout_max = std::chrono::milliseconds(100);
        cfg.heartbeat_interval = std::chrono::milliseconds(20);
        return cfg;
    };

    {
        v2::match::MatchmakingService service(19481);
        service.set_matchmaking_config(match_cfg);
        service.set_raft_config(make_cfg(19481));
        service.start();

        for (int i = 0; i < 40 && !service.is_raft_leader(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
        ASSERT_TRUE(service.is_raft_leader());

        v2::service::BackendConnection conn({
            .host = "127.0.0.1",
            .port = 19481,
        });
        ASSERT_TRUE(conn.connect());

        auto send_join = [&](const std::string& user_id, int mmr) {
            v2::service::BackendEnvelope request;
            request.target_service = v2::service::ServiceId::kGateway;
            request.kind = v2::service::MessageKind::kRequest;
            request.message_type = "match_join";
            request.payload = nlohmann::json{
                {"user_id", user_id},
                {"mmr", mmr},
                {"mode", "1v1"},
            }.dump();
            return conn.send_request(request);
        };

        auto join_one = send_join("alice", 1000);
        ASSERT_TRUE(join_one.has_value());
        auto join_two = send_join("bob", 1010);
        ASSERT_TRUE(join_two.has_value());

        v2::service::BackendEnvelope status_request;
        status_request.target_service = v2::service::ServiceId::kGateway;
        status_request.kind = v2::service::MessageKind::kRequest;
        status_request.message_type = "match_status";
        status_request.payload = R"({"user_id":"alice","mode":"1v1"})";

        for (int i = 0; i < 40; ++i) {
            auto status = conn.send_request(status_request);
            if (status.has_value() &&
                status->kind == v2::service::MessageKind::kResponse) {
                auto doc = nlohmann::json::parse(status->payload, nullptr, false);
                if (!doc.is_discarded() && doc.value("matched", false)) {
                    break;
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }

        conn.close();
        service.stop();
    }

    {
        v2::match::MatchmakingService recovered(19481);
        recovered.set_matchmaking_config(match_cfg);
        recovered.set_raft_config(make_cfg(19481));
        recovered.start();

        v2::service::BackendConnection conn({
            .host = "127.0.0.1",
            .port = 19481,
        });
        ASSERT_TRUE(conn.connect());

        v2::service::BackendEnvelope status_request;
        status_request.target_service = v2::service::ServiceId::kGateway;
        status_request.kind = v2::service::MessageKind::kRequest;
        status_request.message_type = "match_status";
        status_request.payload = R"({"user_id":"alice","mode":"1v1"})";

        auto status = conn.send_request(status_request);
        ASSERT_TRUE(status.has_value());
        EXPECT_EQ(status->kind, v2::service::MessageKind::kResponse);
        auto doc = nlohmann::json::parse(status->payload, nullptr, false);
        ASSERT_FALSE(doc.is_discarded());
        EXPECT_TRUE(doc.value("matched", false));
        EXPECT_EQ(doc.value("mode", std::string{}), "1v1");
        EXPECT_GT(doc.value("match_id", std::string{}).size(), 0U);

        conn.close();
        recovered.stop();
    }

    std::filesystem::remove_all(storage_root, ec);
}

TEST(V2BackendRoutingTest, LeaderboardReelectsAndLogicalRestartCatchesUpOverBackendRpc) {
    app::logging::init("project_tests");
    DisableRedisAutoConnectForTest redis_guard;

    constexpr std::uint16_t kPort1 = 19491;
    constexpr std::uint16_t kPort2 = 19492;
    constexpr std::uint16_t kPort3 = 19493;

    const std::vector<v3::cluster::RaftNodeId> peers = {
        {"lb-roll-1", "127.0.0.1", kPort1},
        {"lb-roll-2", "127.0.0.1", kPort2},
        {"lb-roll-3", "127.0.0.1", kPort3},
    };

    auto make_cfg = [&](const std::string& node_id) {
        v3::cluster::RaftConfig cfg;
        cfg.node_id = node_id;
        cfg.peers = peers;
        cfg.election_timeout_min = std::chrono::milliseconds(150);
        cfg.election_timeout_max = std::chrono::milliseconds(300);
        cfg.heartbeat_interval = std::chrono::milliseconds(50);
        return cfg;
    };

    v2::leaderboard::LeaderboardService node1(kPort1);
    v2::leaderboard::LeaderboardService node2(kPort2);
    v2::leaderboard::LeaderboardService node3(kPort3);
    node1.set_raft_config(make_cfg("lb-roll-1"));
    node2.set_raft_config(make_cfg("lb-roll-2"));
    node3.set_raft_config(make_cfg("lb-roll-3"));
    node1.start();
    node2.start();
    node3.start();

    std::uint16_t leader_port = 0;
    v2::leaderboard::LeaderboardService* stopped_leader = nullptr;
    for (int i = 0; i < 80; ++i) {
        const bool node1_leads = node1.is_raft_leader();
        const bool node2_leads = node2.is_raft_leader();
        const bool node3_leads = node3.is_raft_leader();
        const int leader_count = static_cast<int>(node1_leads) + static_cast<int>(node2_leads) +
                                 static_cast<int>(node3_leads);
        if (leader_count == 1) {
            if (node1_leads) {
                leader_port = kPort1;
                stopped_leader = &node1;
            } else if (node2_leads) {
                leader_port = kPort2;
                stopped_leader = &node2;
            } else {
                leader_port = kPort3;
                stopped_leader = &node3;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    ASSERT_NE(leader_port, 0);
    ASSERT_NE(stopped_leader, nullptr);

    v2::service::BackendConnection leader_conn({
        .host = "127.0.0.1",
        .port = leader_port,
    });
    ASSERT_TRUE(leader_conn.connect());

    v2::service::BackendEnvelope submit_request;
    submit_request.target_service = v2::service::ServiceId::kGateway;
    submit_request.kind = v2::service::MessageKind::kRequest;
    submit_request.message_type = "leaderboard_submit";
    submit_request.payload = R"({"user_id":"eve","display_name":"Eve","score":1337})";
    auto submit_response = leader_conn.send_request(submit_request);
    ASSERT_TRUE(submit_response.has_value());
    ASSERT_EQ(submit_response->kind, v2::service::MessageKind::kResponse);
    leader_conn.close();

    stopped_leader->stop();

    v2::service::BackendConnection stopped_conn({
        .host = "127.0.0.1",
        .port = leader_port,
    });
    EXPECT_FALSE(stopped_conn.connect());

    v2::leaderboard::LeaderboardService* new_leader = nullptr;
    std::uint16_t new_leader_port = 0;
    for (int i = 0; i < 80; ++i) {
        const bool node1_leads = leader_port != kPort1 && node1.is_raft_leader();
        const bool node2_leads = leader_port != kPort2 && node2.is_raft_leader();
        const bool node3_leads = leader_port != kPort3 && node3.is_raft_leader();
        const int survivor_leaders = static_cast<int>(node1_leads) + static_cast<int>(node2_leads) +
                                     static_cast<int>(node3_leads);
        if (survivor_leaders == 1) {
            if (node1_leads) {
                new_leader = &node1;
                new_leader_port = kPort1;
            } else if (node2_leads) {
                new_leader = &node2;
                new_leader_port = kPort2;
            } else {
                new_leader = &node3;
                new_leader_port = kPort3;
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    ASSERT_NE(new_leader, nullptr);
    ASSERT_NE(new_leader_port, leader_port);

    v2::service::BackendConnection new_leader_conn({
        .host = "127.0.0.1",
        .port = new_leader_port,
    });
    ASSERT_TRUE(new_leader_conn.connect());

    submit_request.payload = R"({"user_id":"frank","display_name":"Frank","score":2048})";
    auto failover_submit_response = new_leader_conn.send_request(submit_request);
    ASSERT_TRUE(failover_submit_response.has_value());
    EXPECT_EQ(failover_submit_response->kind, v2::service::MessageKind::kResponse);
    new_leader_conn.close();

    stopped_leader->start();

    v2::service::BackendConnection recovered_conn({
        .host = "127.0.0.1",
        .port = leader_port,
    });
    ASSERT_TRUE(recovered_conn.connect());

    v2::service::BackendEnvelope rank_request;
    rank_request.target_service = v2::service::ServiceId::kGateway;
    rank_request.kind = v2::service::MessageKind::kRequest;
    rank_request.message_type = "leaderboard_rank";
    rank_request.payload = R"({"user_id":"frank"})";

    std::optional<v2::service::BackendEnvelope> rank_response;
    for (int i = 0; i < 80; ++i) {
        rank_response = recovered_conn.send_request(rank_request);
        if (rank_response.has_value() &&
            rank_response->kind == v2::service::MessageKind::kResponse) {
            auto doc = nlohmann::json::parse(rank_response->payload, nullptr, false);
            if (!doc.is_discarded() && doc.value("score", 0) == 2048) {
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }

    ASSERT_TRUE(rank_response.has_value());
    EXPECT_EQ(rank_response->kind, v2::service::MessageKind::kResponse);
    auto rank_doc = nlohmann::json::parse(rank_response->payload, nullptr, false);
    ASSERT_FALSE(rank_doc.is_discarded());
    EXPECT_EQ(rank_doc.value("user_id", std::string{}), "frank");
    EXPECT_EQ(rank_doc.value("score", 0), 2048);

    const int final_leader_count = static_cast<int>(node1.is_raft_leader()) +
                                   static_cast<int>(node2.is_raft_leader()) +
                                   static_cast<int>(node3.is_raft_leader());
    EXPECT_EQ(final_leader_count, 1);

    recovered_conn.close();
    node1.stop();
    node2.stop();
    node3.stop();
}

// ─── v3.0.0 B5: OpenTelemetry export integration tests ───────────────

TEST(V2BackendRoutingTest, OtelExporterReceivesSpanOnSuccessfulRoute) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto exporter = std::make_shared<v3::tracing::OtlpExporter>(
        v3::tracing::OtlpExporter::Config{.service_name = "gateway-test"});

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{"127.0.0.1", backend.port},
        std::nullopt, std::nullopt);
    bridge.set_otel_exporter(exporter);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"zoe","token":"t10","display_name":"Zoe"})");
    EXPECT_TRUE(result.success);

    auto records = exporter->drain();
    ASSERT_GE(records.size(), 1U);
    EXPECT_EQ(records[0].service_name, "login");
    EXPECT_EQ(records[0].status, "ok");

    bridge.shutdown();
    backend.stop();
}

TEST(V2BackendRoutingTest, OtelExporterReceivesSpanOnFailedRoute) {
    app::logging::init("project_tests");

    auto exporter = std::make_shared<v3::tracing::OtlpExporter>(
        v3::tracing::OtlpExporter::Config{.service_name = "gateway-test"});

    // Point at a port that nothing listens on — route will fail.
    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{"127.0.0.1", 19998},
        std::nullopt, std::nullopt);
    bridge.set_otel_exporter(exporter);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"zoe","token":"t10","display_name":"Zoe"})");
    EXPECT_FALSE(result.success);

    auto records = exporter->drain();
    ASSERT_GE(records.size(), 1U);
    EXPECT_EQ(records[0].service_name, "login");

    bridge.shutdown();
}

TEST(V2BackendRoutingTest, OtelExporterSpanHasCorrectOperationName) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    auto exporter = std::make_shared<v3::tracing::OtlpExporter>(
        v3::tracing::OtlpExporter::Config{.service_name = "gateway-test"});

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{"127.0.0.1", backend.port},
        std::nullopt, std::nullopt);
    bridge.set_otel_exporter(exporter);

    auto route_result = bridge.route(v2::service::ServiceId::kLogin,
                                     "room_create",
                                     R"({"user_id":"alice","room_id":"room_001"})");
    EXPECT_FALSE(route_result.success);

    auto records = exporter->drain();
    ASSERT_GE(records.size(), 1U);
    EXPECT_EQ(records[0].operation_name, "route.room_create");

    bridge.shutdown();
    backend.stop();
}

TEST(V2BackendRoutingTest, OtelExporterPostsSpanToCollectorEndpoint) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    FakeOtlpCollector collector;
    ASSERT_TRUE(collector.start());

    auto exporter = std::make_shared<v3::tracing::OtlpExporter>(
        v3::tracing::OtlpExporter::Config{
            .service_name = "gateway-test",
            .export_endpoint =
                "http://127.0.0.1:" + std::to_string(collector.port()) + "/v1/traces",
            .max_batch_size = 1,
        });

    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{"127.0.0.1", backend.port},
        std::nullopt, std::nullopt);
    bridge.set_otel_exporter(exporter);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"zoe","token":"t10","display_name":"Zoe"})");
    EXPECT_TRUE(result.success);

    for (int i = 0; i < 50; ++i) {
        {
            std::lock_guard lock(collector.mutex);
            if (collector.request_count > 0) {
                break;
            }
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    {
        std::lock_guard lock(collector.mutex);
        EXPECT_EQ(collector.request_count, 1U);
        EXPECT_EQ(collector.last_target, "/v1/traces");
        EXPECT_NE(collector.last_body.find("\"serviceName\":\"login\""), std::string::npos);
        EXPECT_NE(collector.last_body.find("\"operationName\":\"route.login_request\""),
                  std::string::npos);
    }

    bridge.shutdown();
    collector.stop();
    backend.stop();
}

TEST(V2BackendRoutingTest, OtelExporterNoCrashWhenNotConfigured) {
    app::logging::init("project_tests");

    LoginBackendProcess backend;
    ASSERT_TRUE(backend.start());

    // No exporter configured — route must still succeed without crash.
    v2::gateway::GatewayServiceBridge bridge(
        v2::gateway::GatewayServiceBridge::BackendConfig{"127.0.0.1", backend.port},
        std::nullopt, std::nullopt);

    auto result = bridge.route(v2::service::ServiceId::kLogin,
                               "login_request",
                               R"({"user_id":"zoe","token":"t10","display_name":"Zoe"})");
    EXPECT_TRUE(result.success);

    bridge.shutdown();
    backend.stop();
}
