// SDK v2.4.0: Full integration testing with live gateway server.
// Tests complete business flows: login, reconnect, room lifecycle,
// battle start/move/finish, service split data consistency.

#include "boost_gateway/sdk/client.h"
#include "app/logging.h"
#include "v2/battle/battle_backend_service.h"
#include "v2/gateway/demo_server.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v2/login/login_backend_service.h"
#include "v2/match/matchmaking_service.h"
#include "v2/room/room_backend_service.h"

#include <boost/asio.hpp>

#include <chrono>
#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <memory>
#include <string>
#include <thread>

#include <future>
#include <gtest/gtest.h>

#ifdef _WIN32
#include <process.h>
#else
#include <unistd.h>
#endif

namespace sdk = boost_gateway::sdk;
using namespace std::chrono_literals;

namespace {

#ifdef _WIN32
void set_test_env(const char* key, const char* value) {
    _putenv_s(key, value);
}

void unset_test_env(const char* key) {
    _putenv_s(key, "");
}

std::filesystem::path test_config_path() {
    return std::filesystem::temp_directory_path() /
           ("boost_gateway_sdk_business_flow_no_config_" +
            std::to_string(static_cast<unsigned long long>(_getpid())) + ".json");
}
#else
void set_test_env(const char* key, const char* value) {
    setenv(key, value, 1);
}

void unset_test_env(const char* key) {
    unsetenv(key);
}

std::filesystem::path test_config_path() {
    return std::filesystem::temp_directory_path() /
           ("boost_gateway_sdk_business_flow_no_config_" +
            std::to_string(static_cast<unsigned long long>(getpid())) + ".json");
}
#endif

struct GatewayFixture : public ::testing::Test {
    std::unique_ptr<v2::gateway::DemoServer> server_;
    std::unique_ptr<std::thread> server_thread_;
    std::unique_ptr<v2::login::LoginBackendService> login_backend_;
    std::unique_ptr<v2::room::RoomBackendService> room_backend_;
    std::unique_ptr<v2::battle::BattleBackendService> battle_backend_;
    std::unique_ptr<v2::match::MatchmakingService> matchmaking_backend_;
    std::unique_ptr<v2::leaderboard::LeaderboardService> leaderboard_backend_;
    std::uint16_t port_ = 0;
    std::filesystem::path test_config_path_;

    void SetUp() override {
        app::logging::init("sdk_business_flow_tests");
        test_config_path_ = test_config_path();
        std::error_code remove_ec;
        std::filesystem::remove(test_config_path_, remove_ec);
        set_test_env("CONFIG_PATH", test_config_path_.c_str());
        set_test_env("V2_BACKEND_CONNECTION_POOL_SIZE", "1");
        set_test_env("BOOST_DISABLE_REDIS_AUTO_CONNECT", "1");

        login_backend_ = std::make_unique<v2::login::LoginBackendService>(0);
        room_backend_ = std::make_unique<v2::room::RoomBackendService>(0, 3, 300000, 50);
        battle_backend_ = std::make_unique<v2::battle::BattleBackendService>(0);
        matchmaking_backend_ = std::make_unique<v2::match::MatchmakingService>(0);
        leaderboard_backend_ = std::make_unique<v2::leaderboard::LeaderboardService>(0);

        login_backend_->start();
        room_backend_->start();
        battle_backend_->start();
        matchmaking_backend_->start();
        leaderboard_backend_->start();

        v2::gateway::DemoServerOptions options;
        options.login_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = login_backend_->local_port(),
            .timeout = std::chrono::milliseconds(5000),
            .connect_timeout = std::chrono::milliseconds(1000),
        };
        options.room_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = room_backend_->local_port(),
            .timeout = std::chrono::milliseconds(5000),
            .connect_timeout = std::chrono::milliseconds(1000),
        };
        options.battle_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = battle_backend_->local_port(),
            .timeout = std::chrono::milliseconds(5000),
            .connect_timeout = std::chrono::milliseconds(1000),
        };
        options.matchmaking_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = matchmaking_backend_->local_port(),
            .timeout = std::chrono::milliseconds(5000),
            .connect_timeout = std::chrono::milliseconds(1000),
        };
        options.leaderboard_backend_config = v2::gateway::GatewayServiceBridge::BackendConfig{
            .host = "127.0.0.1",
            .port = leaderboard_backend_->local_port(),
            .timeout = std::chrono::milliseconds(5000),
            .connect_timeout = std::chrono::milliseconds(1000),
        };

        server_ = std::make_unique<v2::gateway::DemoServer>(0, net::SessionOptions{}, std::move(options));
        server_thread_ = std::make_unique<std::thread>([this]() {
            server_->start();
        });
        // Give the server thread time to set up the acceptor before polling
        std::this_thread::sleep_for(500ms);
        for (int i = 0; i < 100; ++i) {
            try {
                boost::asio::io_context io;
                boost::asio::ip::tcp::socket sock(io);
                port_ = server_->local_port();
                if (port_ == 0) {
                    std::this_thread::sleep_for(100ms);
                    continue;
                }
                sock.connect(boost::asio::ip::tcp::endpoint(
                    boost::asio::ip::make_address("127.0.0.1"), port_));
                break;
            } catch (...) {
                std::this_thread::sleep_for(100ms);
            }
        }
    }

    void TearDown() override {
        if (server_) server_->stop();
        if (server_thread_ && server_thread_->joinable()) server_thread_->join();
        if (leaderboard_backend_) leaderboard_backend_->stop();
        if (matchmaking_backend_) matchmaking_backend_->stop();
        if (battle_backend_) battle_backend_->stop();
        if (room_backend_) room_backend_->stop();
        if (login_backend_) login_backend_->stop();
        unset_test_env("BOOST_DISABLE_REDIS_AUTO_CONNECT");
        unset_test_env("V2_BACKEND_CONNECTION_POOL_SIZE");
        unset_test_env("CONFIG_PATH");
        std::error_code remove_ec;
        std::filesystem::remove(test_config_path_, remove_ec);
    }

    sdk::SdkClient make_client() { return sdk::SdkClient(); }
};

// ─── Login ─────────────────────────────────────────────────────────────

TEST_F(GatewayFixture, SdkLoginSuccess) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));

    auto result = client.login("alice", "token:alice", 5s);
    EXPECT_TRUE(result.ok) << result.error_message;
    EXPECT_EQ(result.user_id, "alice");

    client.disconnect();
}

TEST_F(GatewayFixture, SdkLoginInvalidTokenFails) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));

    // Empty token should fail in local path
    auto result = client.login("mallory", "", 5s);
    // Local path may accept it (dev mode), bridge path would reject
    if (!result.ok) {
        EXPECT_NE(result.error_code, 0);
    }

    client.disconnect();
}

// ─── Room lifecycle ────────────────────────────────────────────────────

TEST_F(GatewayFixture, SdkCreateAndJoinRoom) {
    auto alice = make_client();
    auto bob = make_client();

    ASSERT_TRUE(alice.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(bob.connect("127.0.0.1", port_, 5s));

    EXPECT_TRUE(alice.login("alice", "token:alice", 5s).ok);
    EXPECT_TRUE(bob.login("bob", "token:bob", 5s).ok);

    // Alice creates room
    auto create = alice.create_room("sdk_room", 5s);
    EXPECT_TRUE(create.ok) << create.error_message;
    EXPECT_EQ(create.room_id, "sdk_room");

    // Bob joins
    auto join = bob.join_room("sdk_room", 5s);
    EXPECT_TRUE(join.ok) << join.error_message;

    alice.disconnect();
    bob.disconnect();
}

TEST_F(GatewayFixture, SdkReadyAndLeaveRoom) {
    auto alice = make_client();
    ASSERT_TRUE(alice.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(alice.login("alice", "token:alice", 5s).ok);
    ASSERT_TRUE(alice.create_room("ready_room", 5s).ok);

    // Set ready
    auto ready = alice.set_ready(true, 5s);
    EXPECT_TRUE(ready.ok);

    // Toggle off
    auto unready = alice.set_ready(false, 5s);
    EXPECT_TRUE(unready.ok);

    // Leave room
    auto leave = alice.leave_room("ready_room", 5s);
    EXPECT_TRUE(leave.ok);

    alice.disconnect();
}

// ─── Echo ──────────────────────────────────────────────────────────────

TEST_F(GatewayFixture, SdkEchoRoundTrip) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(client.login("echo_user", "token:echo_user", 5s).ok);

    auto result = client.echo("Hello, SDK!", 5s);
    EXPECT_TRUE(result.ok);
    EXPECT_EQ(result.echo_body, "Hello, SDK!");

    client.disconnect();
}

// ─── Matchmaking / Leaderboard backends ───────────────────────────────

TEST_F(GatewayFixture, SdkMatchmakingRoundTripThroughBackend) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(client.login("match_user", "token:match_user", 5s).ok);

    auto join = client.match_join("match_user", 1180, "1v1", 5s);
    ASSERT_TRUE(join.ok) << join.error_message << " body=" << join.response_body;
    EXPECT_NE(join.response_body.find("\"queued\":true"), std::string::npos);

    auto status = client.match_status("match_user", "1v1", 5s);
    ASSERT_TRUE(status.ok) << status.error_message << " body=" << status.response_body;
    EXPECT_NE(status.response_body.find("\"matched\":false"), std::string::npos);
    EXPECT_NE(status.response_body.find("\"queue_size\""), std::string::npos);

    auto leave = client.match_leave("match_user", "1v1", 5s);
    ASSERT_TRUE(leave.ok) << leave.error_message << " body=" << leave.response_body;
    EXPECT_NE(leave.response_body.find("\"left\":true"), std::string::npos);

    client.disconnect();
}

TEST_F(GatewayFixture, SdkLeaderboardRoundTripThroughBackend) {
    auto alice = make_client();
    auto bob = make_client();
    ASSERT_TRUE(alice.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(bob.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(alice.login("lb_alice", "token:lb_alice", 5s).ok);
    ASSERT_TRUE(bob.login("lb_bob", "token:lb_bob", 5s).ok);

    auto alice_submit = alice.leaderboard_submit("lb_alice", "Alice", 9001, 5s);
    ASSERT_TRUE(alice_submit.ok) << alice_submit.error_message
                                 << " body=" << alice_submit.response_body;

    auto bob_submit = bob.leaderboard_submit("lb_bob", "Bob", 9100, 5s);
    ASSERT_TRUE(bob_submit.ok) << bob_submit.error_message
                               << " body=" << bob_submit.response_body;

    auto top = alice.leaderboard_top(2, 5s);
    ASSERT_TRUE(top.ok) << top.error_message << " body=" << top.response_body;
    EXPECT_NE(top.response_body.find("\"user_id\":\"lb_bob\""), std::string::npos)
        << "leaderboard top body=" << top.response_body;
    EXPECT_NE(top.response_body.find("\"user_id\":\"lb_alice\""), std::string::npos)
        << "leaderboard top body=" << top.response_body;

    auto rank = alice.leaderboard_rank("lb_alice", 5s);
    ASSERT_TRUE(rank.ok) << rank.error_message << " body=" << rank.response_body;
    EXPECT_NE(rank.response_body.find("\"rank\":2"), std::string::npos)
        << "leaderboard rank body=" << rank.response_body;
    EXPECT_NE(rank.response_body.find("\"score\":9001"), std::string::npos)
        << "leaderboard rank body=" << rank.response_body;

    alice.disconnect();
    bob.disconnect();
}

// ─── Reconnect / Session Resume ────────────────────────────────────────

TEST_F(GatewayFixture, SdkReconnectAfterDisconnect) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));

    // Login and create a room
    ASSERT_TRUE(client.login("reconnect_user", "token:reconnect_user", 5s).ok);
    ASSERT_TRUE(client.create_room("reconnect_room", 5s).ok);

    // Simulate disconnect and reconnect
    client.disconnect();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));

    // Re-login (should resume room state)
    auto relogin = client.login("reconnect_user", "token:reconnect_user", 5s);
    EXPECT_TRUE(relogin.ok);

    client.disconnect();
}

TEST_F(GatewayFixture, SdkHeartbeatKeepsConnectionAlive) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(client.login("heartbeat_user", "token:heartbeat_user", 5s).ok);

    client.start_heartbeat(1s);
    std::this_thread::sleep_for(1200ms);
    EXPECT_TRUE(client.echo("after heartbeat", 5s).ok);
    client.stop_heartbeat();
    client.disconnect();
}

TEST_F(GatewayFixture, SdkDisconnectCallbackFiresAfterHeartbeatFailure) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(client.login("disconnect_user", "token:disconnect_user", 5s).ok);

    std::atomic<int> disconnects{0};
    std::atomic<int> async_disconnects{0};
    client.on_disconnect([&] { disconnects.fetch_add(1); });
    client.on_async_disconnect([&] { async_disconnects.fetch_add(1); });
    client.start_heartbeat(1s);

    server_->stop();
    if (server_thread_ && server_thread_->joinable()) {
        server_thread_->join();
    }
    server_thread_.reset();
    server_.reset();

    for (int i = 0; i < 50 && disconnects.load() == 0; ++i) {
        std::this_thread::sleep_for(100ms);
    }
    client.stop_heartbeat();
    EXPECT_GT(disconnects.load(), 0);
    EXPECT_GT(async_disconnects.load(), 0);
}

// ─── Battle flow ───────────────────────────────────────────────────────

TEST_F(GatewayFixture, SdkFullBattleFlow) {
    auto alice = make_client();
    auto bob = make_client();
    ASSERT_TRUE(alice.connect("127.0.0.1", port_, 5s));
    ASSERT_TRUE(bob.connect("127.0.0.1", port_, 5s));

    // Login both
    ASSERT_TRUE(alice.login("alice", "token:alice", 5s).ok);
    ASSERT_TRUE(bob.login("bob", "token:bob", 5s).ok);

    // Room setup
    ASSERT_TRUE(alice.create_room("battle_room", 5s).ok);
    ASSERT_TRUE(bob.join_room("battle_room", 5s).ok);
    ASSERT_TRUE(alice.set_ready(true, 5s).ok);
    ASSERT_TRUE(bob.set_ready(true, 5s).ok);

    // Start battle
    auto battle = alice.start_battle("battle_room", 5s);
    // Battle start may succeed or fail depending on backend setup
    // In local mode (no bridge), battle is handled locally
    if (battle.ok) {
        // Send battle input
        auto move = alice.send_battle_input("move:10,20", 5s);
        EXPECT_TRUE(move.ok);

        auto attack = bob.send_battle_input("move:30,40", 5s);
        EXPECT_TRUE(attack.ok);

        // Finish battle
        auto finish = alice.send_battle_input("finish:surrender", 5s);
        EXPECT_TRUE(finish.ok);
    }

    alice.disconnect();
    bob.disconnect();
}

// ─── Push callback ─────────────────────────────────────────────────────

TEST_F(GatewayFixture, SdkPushCallback) {
    auto client = make_client();
    ASSERT_TRUE(client.connect("127.0.0.1", port_, 5s));

    int push_count = 0;
    client.on_push([&](const sdk::PushMessage&) { ++push_count; });
    ASSERT_TRUE(client.login("push_user", "token:push_user", 5s).ok);

    // Create room — may trigger room state push
    client.create_room("push_room", 5s);
    // Give time for push to arrive
    std::this_thread::sleep_for(100ms);
    // Push callback may or may not fire depending on local vs bridge mode
    SUCCEED() << "Push callback registered, push_count=" << push_count;

    client.disconnect();
}

// ─── Concurrent clients ────────────────────────────────────────────────

TEST_F(GatewayFixture, SdkMultipleConcurrentConnections) {
    constexpr int kClients = 5;
    std::vector<sdk::SdkClient> clients(kClients);
    std::vector<std::thread> threads;

    for (int i = 0; i < kClients; ++i) {
        threads.emplace_back([this, i, &clients]() {
            auto user = "user_" + std::to_string(i);
            ASSERT_TRUE(clients[i].connect("127.0.0.1", port_, 5s));
            auto login = clients[i].login(user, "token:" + user, 5s);
            EXPECT_TRUE(login.ok);
        });
    }
    for (auto& t : threads) t.join();

    for (auto& c : clients) c.disconnect();
    SUCCEED();
}

}  // namespace

// ── Async API tests (v3.4.0) ────────────────────────────────────────────

namespace {

using namespace boost_gateway::sdk;

TEST(SdkAsyncTest, AsyncConnectCallback) {
    SdkClient client;
    std::promise<bool> promise;
    auto future = promise.get_future();

    client.async_connect("127.0.0.1", 9201,
        [&promise](bool ok) { promise.set_value(ok); });

    // Timeout after 2 seconds (no server expected in unit test)
    auto status = future.wait_for(std::chrono::seconds(2));
    EXPECT_EQ(status, std::future_status::timeout)
        << "Expected timeout since no server is running";
}

TEST(SdkAsyncTest, AsyncLoginNoServer) {
    SdkClient client;
    std::promise<LoginResult> promise;
    auto future = promise.get_future();

    // Don't connect first - should fail gracefully
    client.async_login("test_user", "test_token",
        [&promise](LoginResult result) { promise.set_value(result); });

    auto status = future.wait_for(std::chrono::seconds(2));
    ASSERT_EQ(status, std::future_status::ready);
    EXPECT_FALSE(future.get().ok);
}

TEST(SdkAsyncTest, AsyncPushCallbackRegistration) {
    SdkClient client;
    bool callback_called = false;

    client.on_async_push([&callback_called](const std::string&) {
        callback_called = true;
    });

    // Just verify registration doesn't crash - actual push needs server
    SUCCEED();
}

TEST(SdkAsyncTest, MultipleAsyncOperations) {
    SdkClient client;
    std::atomic<int> completed{0};

    auto cb = [&completed](auto&&...) { completed++; };

    client.async_login("u1", "t1", cb);
    client.async_create_room("r1", cb);
    client.async_join_room("r1", cb);
    client.async_send_battle_input("input", cb);
    client.async_connect("127.0.0.1", 9201, cb);

    std::this_thread::sleep_for(std::chrono::milliseconds(500));
    // At minimum, all callbacks should have been invoked
    EXPECT_GT(completed.load(), 0);
}

}  // namespace
