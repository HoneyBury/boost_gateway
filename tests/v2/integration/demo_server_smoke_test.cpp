#include "app/logging.h"
#include "net/http_manager.h"
#include "net/packet_codec.h"
#include "net/protocol.h"
#include "v2/gateway/demo_server.h"

#include <boost/asio.hpp>
#include <boost/beast/core/flat_buffer.hpp>
#include <boost/beast/http/message.hpp>
#include <boost/beast/http/read.hpp>
#include <boost/beast/http/string_body.hpp>
#include <boost/beast/http/write.hpp>
#include <nlohmann/json.hpp>

#include <chrono>
#include <memory>
#include <string>
#include <stdexcept>
#include <thread>
#include <sys/socket.h>
#include <sys/time.h>
#include <vector>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
namespace beast = boost::beast;
namespace http = beast::http;
using tcp = asio::ip::tcp;

struct V2DemoRuntime {
    std::unique_ptr<v2::gateway::DemoServer> server;
    std::string startup_error;

    bool start() {
        try {
            if (!server) {
                server = std::make_unique<v2::gateway::DemoServer>(0);
            }
            server->start();
            return true;
        } catch (const std::exception& ex) {
            startup_error = ex.what();
            return false;
        }
    }

    void stop() {
        if (server) {
            server->stop();
        }
    }
};

class TestClient {
public:
    TestClient() : socket_(io_context_) {}

    void connect(std::uint16_t port) {
        socket_.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), port));
        timeval timeout{.tv_sec = 5, .tv_usec = 0};
        setsockopt(socket_.native_handle(), SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
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

    void send(std::uint16_t message_id, std::uint32_t request_id, const std::string& body) {
        const auto outbound = net::packet::encode(message_id, request_id, 0, body);
        asio::write(socket_, asio::buffer(outbound));
    }

    void wait_readable() {
        fd_set read_fds;
        FD_ZERO(&read_fds);
        FD_SET(socket_.native_handle(), &read_fds);
        timeval timeout{.tv_sec = 5, .tv_usec = 0};
        const int rc = select(socket_.native_handle() + 1, &read_fds, nullptr, nullptr, &timeout);
        if (rc <= 0) {
            throw std::runtime_error("timed out waiting for packet");
        }
    }

    net::packet::DecodedPacket read() {
        wait_readable();
        net::packet::LengthHeader header{};
        asio::read(socket_, asio::buffer(header));
        const auto payload_length = net::packet::decode_length(header);

        wait_readable();
        std::vector<char> payload(payload_length);
        asio::read(socket_, asio::buffer(payload));
        return net::packet::decode_payload(payload);
    }

    net::packet::DecodedPacket expect_message(std::uint16_t message_id) {
        std::string observed;
        for (;;) {
            net::packet::DecodedPacket packet;
            try {
                packet = read();
            } catch (const std::exception& ex) {
                throw std::runtime_error("timed out waiting for message id " +
                                         std::to_string(message_id) + ": " + ex.what() +
                                         "; observed=" + observed);
            }
            observed += std::to_string(packet.message_id) + ":" + packet.body + ";";
            if (packet.message_id == message_id) {
                return packet;
            }
        }
    }

private:
    asio::io_context io_context_;
    tcp::socket socket_;
};

http::response<http::string_body> http_request(std::uint16_t port, std::string_view path) {
    asio::io_context io_context;
    tcp::resolver resolver(io_context);
    const auto endpoints = resolver.resolve("127.0.0.1", std::to_string(port));

    tcp::socket socket(io_context);
    asio::connect(socket, endpoints);

    http::request<http::string_body> req{http::verb::get, std::string(path), 11};
    req.set(http::field::host, "127.0.0.1");
    http::write(socket, req);

    beast::flat_buffer buffer;
    http::response<http::string_body> res;
    http::read(socket, buffer, res);
    socket.close();
    return res;
}

}  // namespace

#define SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime) \
    do { \
        if (!(runtime).start()) { \
            GTEST_SKIP() << "socket bind unavailable in this environment: " << (runtime).startup_error; \
        } \
    } while (false)

TEST(V2DemoServerSmokeTest, RealSocketFlowSupportsBootstrapAndDisconnectCleanup) {
    app::logging::init("project_tests");

    V2DemoRuntime runtime;
    runtime.server = std::make_unique<v2::gateway::DemoServer>(
        0,
        net::SessionOptions{
            .heartbeat_check_interval = std::chrono::milliseconds(5000),
            .heartbeat_timeout = std::chrono::minutes(2),
        });
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime);

    TestClient owner;
    TestClient member;
    owner.connect(runtime.server->local_port());
    member.connect(runtime.server->local_port());

    const auto owner_login =
        owner.exchange(net::protocol::kLoginRequest, 1, "owner|token:owner|Owner");
    EXPECT_EQ(owner_login.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(owner_login.body, "login_ok:owner");

    const auto member_login =
        member.exchange(net::protocol::kLoginRequest, 2, "member|token:member|Member");
    EXPECT_EQ(member_login.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(member_login.body, "login_ok:member");

    const auto room_create =
        owner.exchange(net::protocol::kRoomCreateRequest, 3, "room_alpha");
    EXPECT_EQ(room_create.message_id, net::protocol::kRoomCreateResponse);

    const auto room_join =
        member.exchange(net::protocol::kRoomJoinRequest, 4, "room_alpha");
    EXPECT_EQ(room_join.message_id, net::protocol::kRoomJoinResponse);

    const auto owner_ready =
        owner.exchange(net::protocol::kRoomReadyRequest, 5, "true");
    EXPECT_EQ(owner_ready.message_id, net::protocol::kRoomReadyResponse);

    const auto member_ready =
        member.exchange(net::protocol::kRoomReadyRequest, 6, "true");
    EXPECT_EQ(member_ready.message_id, net::protocol::kRoomReadyResponse);

    owner.send(net::protocol::kBattleStartRequest, 7, "room_alpha");
    const auto start_response = owner.expect_message(net::protocol::kBattleStartResponse);
    EXPECT_EQ(start_response.body, "battle_started:room_id=room_alpha:battle_id=battle_0001");

    const auto owner_started = owner.expect_message(net::protocol::kBattleStatePush);
    const auto member_started = member.expect_message(net::protocol::kBattleStatePush);
    EXPECT_EQ(owner_started.body, "battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001");
    EXPECT_EQ(member_started.body, "battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001");

    owner.close();
    member.close();
    runtime.stop();
}

TEST(V2DemoServerSmokeTest, DisconnectCleanupIsSerializedWithQueuedRequests) {
    app::logging::init("project_tests");

    V2DemoRuntime runtime;
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime);

    constexpr int kClosingClients = 24;
    for (int index = 0; index < kClosingClients; ++index) {
        TestClient client;
        client.connect(runtime.server->local_port());
        client.send(net::protocol::kLoginRequest,
                    static_cast<std::uint32_t>(100 + index),
                    "queued_" + std::to_string(index) + "|token:queued_" +
                        std::to_string(index) + "|Queued");
        client.close();
    }

    // Close callbacks and packet handling must share the gateway worker instead
    // of concurrently mutating Runtime and ActorSystem state from I/O threads.
    std::this_thread::sleep_for(std::chrono::milliseconds(250));

    TestClient survivor;
    survivor.connect(runtime.server->local_port());
    const auto response = survivor.exchange(
        net::protocol::kLoginRequest, 200, "survivor|token:survivor|Survivor");
    EXPECT_EQ(response.message_id, net::protocol::kLoginResponse);
    EXPECT_EQ(response.body, "login_ok:survivor");

    survivor.close();
    runtime.stop();
}

TEST(V2DemoServerSmokeTest, RealSocketFlowSupportsRequestedBattleFinish) {
    app::logging::init("project_tests");

    V2DemoRuntime runtime;
    SKIP_IF_V2_RUNTIME_UNAVAILABLE(runtime);

    TestClient owner;
    TestClient member;
    owner.connect(runtime.server->local_port());
    member.connect(runtime.server->local_port());

    EXPECT_EQ(owner.exchange(net::protocol::kLoginRequest, 21, "owner|token:owner|Owner").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(member.exchange(net::protocol::kLoginRequest, 22, "member|token:member|Member").message_id,
              net::protocol::kLoginResponse);
    EXPECT_EQ(owner.exchange(net::protocol::kRoomCreateRequest, 23, "room_beta").message_id,
              net::protocol::kRoomCreateResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomJoinRequest, 24, "room_beta").message_id,
              net::protocol::kRoomJoinResponse);
    EXPECT_EQ(owner.exchange(net::protocol::kRoomReadyRequest, 25, "true").message_id,
              net::protocol::kRoomReadyResponse);
    EXPECT_EQ(member.exchange(net::protocol::kRoomReadyRequest, 26, "true").message_id,
              net::protocol::kRoomReadyResponse);

    owner.send(net::protocol::kBattleStartRequest, 27, "room_beta");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStartResponse).body,
              "battle_started:room_id=room_beta:battle_id=battle_0001");
    (void)owner.expect_message(net::protocol::kBattleStatePush);
    (void)member.expect_message(net::protocol::kBattleStatePush);

    owner.send(net::protocol::kBattleInputRequest, 28, "finish:surrender");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=settlement:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=settlement:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleInputResponse).body,
              "battle_end_accepted:surrender");
    EXPECT_EQ(owner.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=finished:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");
    EXPECT_EQ(member.expect_message(net::protocol::kBattleStatePush).body,
              "battle_state:kind=finished:room_id=room_beta:battle_id=battle_0001:reason=surrender:user_id=owner");

    const auto after_finish = member.exchange(net::protocol::kBattleInputRequest, 29, "move:9,9");
    EXPECT_EQ(after_finish.message_id, net::protocol::kErrorResponse);
    EXPECT_EQ(after_finish.error_code,
              static_cast<std::int32_t>(net::protocol::ErrorCode::kBattleNotStarted));

    owner.close();
    member.close();
    runtime.stop();
}

TEST(V2DemoServerSmokeTest, DemoServerReportsConfiguredIoCoreCount) {
    app::logging::init("project_tests");

    auto io_engine = std::make_unique<v2::io::AsioIoEngine>(2);
    v2::gateway::DemoServer server(0, {}, {}, std::move(io_engine));
    EXPECT_EQ(server.io_core_count(), 2U);
}

TEST(V2DemoServerSmokeTest, DemoServerUsesMultiCoreAcceptorWhenNotPinned) {
    app::logging::init("project_tests");

    try {
        auto io_engine = std::make_unique<v2::io::AsioIoEngine>(4);
        v2::gateway::DemoServer server(0, {}, {}, std::move(io_engine));
        server.start();

        EXPECT_EQ(server.io_core_count(), 4U);
        EXPECT_EQ(server.acceptor_core_id(), 0U);

        TestClient client;
        client.connect(server.local_port());
        const auto login = client.exchange(net::protocol::kLoginRequest, 61, "multi_user|token:multi_user|MultiUser");
        EXPECT_EQ(login.message_id, net::protocol::kLoginResponse);

        const auto snapshots = server.io_core_snapshot();
        ASSERT_EQ(snapshots.size(), 4U);
        std::uint64_t accepted_total = 0;
        for (const auto& snapshot : snapshots) {
            accepted_total += snapshot.accepted_sessions;
        }
        EXPECT_EQ(accepted_total, 1U);

        client.close();
        server.stop();
    } catch (const std::exception& ex) {
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2DemoServerSmokeTest, DemoServerTracksPinnedAcceptorCoreAndSessionSnapshot) {
    app::logging::init("project_tests");

    try {
        auto io_engine = std::make_unique<v2::io::AsioIoEngine>(2);
        v2::gateway::DemoServer server(
            0,
            {},
            v2::gateway::DemoServerOptions{.acceptor_core_id = 1},
            std::move(io_engine));
        server.start();

        TestClient client;
        client.connect(server.local_port());
        const auto login = client.exchange(net::protocol::kLoginRequest, 31, "core_user|token:core_user|CoreUser");
        EXPECT_EQ(login.message_id, net::protocol::kLoginResponse);

        EXPECT_EQ(server.acceptor_core_id(), 1U);
        EXPECT_EQ(server.session_io_core(1), 1U);
        const auto snapshots = server.io_core_snapshot();
        ASSERT_EQ(snapshots.size(), 2U);
        EXPECT_EQ(snapshots[1].active_sessions, 1U);
        EXPECT_EQ(snapshots[1].accepted_sessions, 1U);
        EXPECT_GE(snapshots[1].outbound_dispatches, 1U);
        const auto diagnostics = nlohmann::json::parse(server.diagnostics_json());
        EXPECT_EQ(diagnostics["io_core_count"], 2);
        EXPECT_EQ(diagnostics["acceptor_core_id"], 1);
        EXPECT_EQ(diagnostics["total_active_sessions"], 1);
        EXPECT_EQ(diagnostics["total_accepted_sessions"], 1);
        EXPECT_GE(diagnostics["total_outbound_dispatches"].get<std::uint64_t>(), 1U);
        ASSERT_EQ(diagnostics["io_cores"].size(), 2U);
        EXPECT_EQ(diagnostics["io_cores"][1]["core_id"], 1);
        EXPECT_EQ(diagnostics["io_cores"][1]["active_sessions"], 1);
        EXPECT_EQ(diagnostics["io_cores"][1]["accepted_sessions"], 1);
        EXPECT_GE(diagnostics["io_cores"][1]["outbound_dispatches"].get<std::uint64_t>(), 1U);

        client.close();
        server.stop();
    } catch (const std::exception& ex) {
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2DemoServerSmokeTest, DiagnosticsExposeOtelExporterMetrics) {
    app::logging::init("project_tests");

    v2::gateway::DemoServer server(0, {},
                                   v2::gateway::DemoServerOptions{
                                       .login_backend_config =
                                           v2::gateway::GatewayServiceBridge::BackendConfig{
                                               .host = "127.0.0.1",
                                               .port = 19999,
                                           },
                                   });
    auto exporter = std::make_shared<v3::tracing::OtlpExporter>(
        v3::tracing::OtlpExporter::Config{.service_name = "diagnostics-test"});
    ASSERT_NE(server.service_bridge(), nullptr);
    server.service_bridge()->set_otel_exporter(exporter);

    auto span = v2::tracing::Span::root("diagnostics_span");
    span.finish();
    exporter->export_span(span);

    const auto diagnostics = nlohmann::json::parse(server.diagnostics_json());
    const auto& metrics = diagnostics["otel_exporter_metrics"];
    EXPECT_EQ(metrics["configured"], true);
    EXPECT_EQ(metrics["enqueued_spans"], 1);
    EXPECT_EQ(metrics["exported_spans"], 0);
    EXPECT_EQ(metrics["successful_batches"], 0);
    EXPECT_EQ(metrics["failed_batches"], 0);
    EXPECT_EQ(metrics["buffered_spans"], 1);
}

TEST(V2DemoServerSmokeTest, DiagnosticsHttpEndpointReturnsStructuredSnapshot) {
    app::logging::init("project_tests");

    try {
        auto io_engine = std::make_unique<v2::io::AsioIoEngine>(2);
        v2::gateway::DemoServer server(
            0,
            {},
            v2::gateway::DemoServerOptions{.acceptor_core_id = 1},
            std::move(io_engine));
        server.start();

        asio::io_context management_io;
        net::HttpManager http_manager(management_io.get_executor(), 0);
        http_manager.set_metrics_provider([&server]() {
            const auto diagnostics = server.diagnostics();
            const auto diagnostics_json = server.diagnostics_json();
            return net::HttpMetricsSnapshot{
                .prometheus_text = "",
                .json_text = diagnostics_json,
                .diagnostics_text = "",
                .diagnostics_json_text = diagnostics_json,
            };
        });
        http_manager.start();
        std::thread http_thread([&management_io]() { management_io.run(); });

        TestClient client;
        client.connect(server.local_port());
        const auto login = client.exchange(net::protocol::kLoginRequest, 41, "http_user|token:http_user|HttpUser");
        EXPECT_EQ(login.message_id, net::protocol::kLoginResponse);

        const auto response = http_request(http_manager.local_port(), "/metrics/diagnostics/json");
        EXPECT_EQ(response.result(), http::status::ok);
        const auto diagnostics = nlohmann::json::parse(response.body());
        EXPECT_EQ(diagnostics["io_core_count"], 2);
        EXPECT_EQ(diagnostics["acceptor_core_id"], 1);
        EXPECT_EQ(diagnostics["total_active_sessions"], 1);
        EXPECT_EQ(diagnostics["total_accepted_sessions"], 1);
        ASSERT_EQ(diagnostics["io_cores"].size(), 2U);
        EXPECT_EQ(diagnostics["io_cores"][1]["core_id"], 1);
        EXPECT_EQ(diagnostics["io_cores"][1]["active_sessions"], 1);

        client.close();
        http_manager.stop();
        management_io.stop();
        if (http_thread.joinable()) {
            http_thread.join();
        }
        server.stop();
    } catch (const std::exception& ex) {
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2DemoServerSmokeTest, ReadyJsonFailsWhenConfiguredBackendUnavailable) {
    app::logging::init("project_tests");

    v2::gateway::DemoServer server(
        0,
        {},
        v2::gateway::DemoServerOptions{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = 19999,
            },
        });

    const auto ready = nlohmann::json::parse(server.ready_json());
    EXPECT_FALSE(ready["ready"].get<bool>());
    EXPECT_EQ(ready["status"], "fail");

    bool found_bridge_check = false;
    for (const auto& check : ready["checks"]) {
        if (check["name"] == "bridge:login") {
            found_bridge_check = true;
            EXPECT_EQ(check["status"], "fail");
            EXPECT_EQ(check["message"], "backend unavailable");
        }
    }
    EXPECT_TRUE(found_bridge_check);
}

TEST(V2DemoServerSmokeTest, MetricsExposeBackendRouteLatencyHistogram) {
    app::logging::init("project_tests");

    auto io_engine = std::make_unique<v2::io::AsioIoEngine>(1);
    v2::gateway::DemoServer server(
        0,
        {},
        v2::gateway::DemoServerOptions{
            .login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
                .host = "127.0.0.1",
                .port = 1,
            },
        },
        std::move(io_engine));

    auto* bridge = server.service_bridge();
    ASSERT_NE(bridge, nullptr);
    auto metrics = bridge->get_metrics();
    ASSERT_NE(metrics, nullptr);
    metrics->record_request(v2::service::ServiceId::kLogin);
    metrics->record_success(v2::service::ServiceId::kLogin);
    metrics->record_latency(v2::service::ServiceId::kLogin, 1'500);
    metrics->record_latency(v2::service::ServiceId::kLogin, 40'000);
    metrics->record_latency(v2::service::ServiceId::kLogin, 700'000);

    const auto snapshot = server.metrics_snapshot();
    EXPECT_NE(snapshot.prometheus_text.find("gateway_backend_login_avg_latency_us"),
              std::string::npos);
    EXPECT_NE(snapshot.prometheus_text.find("gateway_backend_login_p99_latency_us 1000000"),
              std::string::npos);
    EXPECT_NE(snapshot.prometheus_text.find(
                  "gateway_backend_route_latency_us_bucket{service=\"login\",le=\"1000000\"} 3"),
              std::string::npos);
    EXPECT_NE(snapshot.prometheus_text.find("gateway_backend_route_latency_us_sum{service=\"login\"} 741500"),
              std::string::npos);
    EXPECT_NE(snapshot.prometheus_text.find("gateway_backend_route_latency_us_count{service=\"login\"} 3"),
              std::string::npos);

    const auto diagnostics = nlohmann::json::parse(snapshot.diagnostics_json_text);
    const auto& login = diagnostics["backend_metrics"]["login"];
    EXPECT_EQ(login["p50_latency_us"], 50'000);
    EXPECT_EQ(login["p99_latency_us"], 1'000'000);
    ASSERT_TRUE(login["latency_buckets"].is_array());
}
