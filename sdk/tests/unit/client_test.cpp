// SDK v4.2.0: Client tests
#include <gtest/gtest.h>
#include "boost_gateway/sdk/client.h"
#include "boost_gateway/sdk/protocol/codec.h"
#include "boost_gateway/sdk/protocol/message.h"
#include <boost/asio.hpp>
#include <atomic>
#include <future>
#include <memory>
#include <thread>
using namespace boost_gateway::sdk;
using namespace std::chrono_literals;

TEST(ClientV4Test, DefaultState) {
    SdkClient c;
    EXPECT_FALSE(c.is_connected());
}

TEST(ClientV4Test, ConnectFailGracefully) {
    SdkClient c;
    EXPECT_FALSE(c.connect("127.0.0.1", 1, 100ms));
    EXPECT_FALSE(c.is_connected());
}

TEST(ClientV4Test, LoginWithoutConnection) {
    SdkClient c;
    auto r = c.login("u", "t", 100ms);
    EXPECT_FALSE(r.ok);
    EXPECT_EQ(r.error_code, static_cast<int>(SdkError::kNotConnected));
    EXPECT_EQ(r.error_message, "not_connected");
}

TEST(ClientV4Test, RoomOpsWithoutConnection) {
    SdkClient c;
    const auto create = c.create_room("r", 100ms);
    const auto join = c.join_room("r", 100ms);
    const auto leave = c.leave_room("r", 100ms);
    const auto ready = c.set_ready(true, 100ms);
    EXPECT_FALSE(create.ok);
    EXPECT_FALSE(join.ok);
    EXPECT_FALSE(leave.ok);
    EXPECT_FALSE(ready.ok);
    EXPECT_EQ(create.error_code, static_cast<int>(SdkError::kNotConnected));
    EXPECT_EQ(join.error_code, static_cast<int>(SdkError::kNotConnected));
    EXPECT_EQ(leave.error_code, static_cast<int>(SdkError::kNotConnected));
    EXPECT_EQ(ready.error_code, static_cast<int>(SdkError::kNotConnected));
}

TEST(ClientV4Test, DisconnectSafeWhenNotConnected) {
    SdkClient c;
    c.disconnect();  // no crash
    SUCCEED();
}

TEST(ClientV4Test, EchoWithoutConnection) {
    SdkClient c;
    auto r = c.echo("hi", 100ms);
    EXPECT_FALSE(r.ok);
}

TEST(ClientV4Test, MatchmakingWithoutConnection) {
    SdkClient c;

    auto join = c.match_join("u", 1000, "1v1", 100ms);
    EXPECT_FALSE(join.ok);
    EXPECT_EQ(join.error_code, static_cast<int>(SdkError::kNotConnected));

    auto leave = c.match_leave("u", "1v1", 100ms);
    EXPECT_FALSE(leave.ok);
    EXPECT_EQ(leave.error_code, static_cast<int>(SdkError::kNotConnected));

    auto status = c.match_status("u", "1v1", 100ms);
    EXPECT_FALSE(status.ok);
    EXPECT_EQ(status.error_code, static_cast<int>(SdkError::kNotConnected));
}

TEST(ClientV4Test, LeaderboardWithoutConnection) {
    SdkClient c;

    auto submit = c.leaderboard_submit("u", "User", 42, 100ms);
    EXPECT_FALSE(submit.ok);
    EXPECT_EQ(submit.error_code, static_cast<int>(SdkError::kNotConnected));

    auto top = c.leaderboard_top(10, 100ms);
    EXPECT_FALSE(top.ok);
    EXPECT_EQ(top.error_code, static_cast<int>(SdkError::kNotConnected));

    auto rank = c.leaderboard_rank("u", 100ms);
    EXPECT_FALSE(rank.ok);
    EXPECT_EQ(rank.error_code, static_cast<int>(SdkError::kNotConnected));
}

TEST(ClientV4Test, HeartbeatLifecycleSafeWhenDisconnected) {
    SdkClient c;
    c.start_heartbeat(1s);
    c.stop_heartbeat();
    c.disconnect();
    SUCCEED();
}

TEST(ClientV4Test, AsyncCallbackMayDestroyClient) {
    auto client = std::make_unique<SdkClient>();
    std::atomic<bool> may_destroy{false};
    std::promise<void> destroyed;
    auto destroyed_future = destroyed.get_future();

    client->async_login("u", "t", [&](LoginResult result) {
        EXPECT_FALSE(result.ok);
        while (!may_destroy.load()) std::this_thread::yield();
        client.reset();
        destroyed.set_value();
    });
    may_destroy = true;

    EXPECT_EQ(destroyed_future.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(client, nullptr);
}

TEST(ClientV4Test, DestructionCancelsInflightAsyncConnect) {
    auto client = std::make_unique<SdkClient>();
    client->async_connect("127.0.0.1", 1, [](bool) {}, 5s);
    std::this_thread::sleep_for(50ms);
    const auto started = std::chrono::steady_clock::now();
    client.reset();
    EXPECT_LT(std::chrono::steady_clock::now() - started, 1s);
}

TEST(ClientV4Test, LateResponseDoesNotSatisfyNextRequest) {
    namespace asio = boost::asio;
    using tcp = asio::ip::tcp;
    using namespace boost_gateway::sdk::protocol;

    asio::io_context io;
    tcp::acceptor acceptor(io, tcp::endpoint(tcp::v4(), 0));
    const auto port = acceptor.local_endpoint().port();
    std::thread server([&] {
        tcp::socket socket(io);
        acceptor.accept(socket);
        auto read_packet = [&] {
            LengthHeader header{};
            asio::read(socket, asio::buffer(header));
            std::vector<char> payload(decode_length(header));
            asio::read(socket, asio::buffer(payload));
            return decode_payload(payload);
        };

        const auto first = read_packet();
        std::this_thread::sleep_for(120ms);
        const auto stale = encode(kEchoResponse, first.request_id, 0, "stale");
        asio::write(socket, asio::buffer(stale));
        const auto second = read_packet();
        const auto push = encode(kRoomStatePush, 0, 0, "room-push");
        const auto fresh = encode(kEchoResponse, second.request_id, 0, "fresh");
        asio::write(socket, asio::buffer(push));
        asio::write(socket, asio::buffer(fresh));
    });

    SdkClient client;
    ASSERT_TRUE(client.connect("127.0.0.1", port, 1s));
    std::promise<std::string> pushed;
    auto pushed_future = pushed.get_future();
    client.on_async_push([&](const std::string& body) { pushed.set_value(body); });
    EXPECT_FALSE(client.echo("first", 50ms).ok);
    const auto second = client.echo("second", 1s);
    EXPECT_TRUE(second.ok);
    EXPECT_EQ(second.echo_body, "fresh");
    ASSERT_EQ(pushed_future.wait_for(1s), std::future_status::ready);
    EXPECT_EQ(pushed_future.get(), "room-push");
    client.disconnect();
    server.join();
}

namespace {

boost_gateway::sdk::protocol::DecodedPacket read_sdk_packet(
    boost::asio::ip::tcp::socket& socket) {
    using namespace boost_gateway::sdk::protocol;
    LengthHeader header{};
    boost::asio::read(socket, boost::asio::buffer(header));
    std::vector<char> payload(decode_length(header));
    boost::asio::read(socket, boost::asio::buffer(payload));
    return decode_payload(payload);
}

void verify_push_callback_can_reenter_sync_api(bool async_push) {
    namespace asio = boost::asio;
    using tcp = asio::ip::tcp;
    using namespace boost_gateway::sdk::protocol;

    asio::io_context io;
    tcp::acceptor acceptor(io, tcp::endpoint(tcp::v4(), 0));
    std::thread server([&] {
        tcp::socket socket(io);
        acceptor.accept(socket);
        const auto outer = read_sdk_packet(socket);
        const auto push = encode(kRoomStatePush, 0, 0, "reenter");
        const auto outer_response = encode(
            kEchoResponse, outer.request_id, 0, "outer-response");
        asio::write(socket, asio::buffer(push));
        std::this_thread::sleep_for(20ms);
        asio::write(socket, asio::buffer(outer_response));

        const auto nested = read_sdk_packet(socket);
        const auto nested_response = encode(
            kEchoResponse, nested.request_id, 0, "nested-response");
        asio::write(socket, asio::buffer(nested_response));
    });

    SdkClient client;
    ASSERT_TRUE(client.connect("127.0.0.1", acceptor.local_endpoint().port(), 1s));
    std::promise<EchoResult> nested_result;
    auto nested_future = nested_result.get_future();
    if (async_push) {
        client.on_async_push([&](const std::string&) {
            nested_result.set_value(client.echo("nested", 1s));
        });
    } else {
        client.on_push([&](const PushMessage&) {
            nested_result.set_value(client.echo("nested", 1s));
        });
    }

    const auto outer = client.echo("outer", 1s);
    EXPECT_TRUE(outer.ok);
    EXPECT_EQ(outer.echo_body, "outer-response");
    ASSERT_EQ(nested_future.wait_for(2s), std::future_status::ready);
    const auto nested = nested_future.get();
    EXPECT_TRUE(nested.ok);
    EXPECT_EQ(nested.echo_body, "nested-response");
    client.disconnect();
    server.join();
}

void verify_push_callback_can_destroy_client(bool async_push) {
    namespace asio = boost::asio;
    using tcp = asio::ip::tcp;
    using namespace boost_gateway::sdk::protocol;

    asio::io_context io;
    tcp::acceptor acceptor(io, tcp::endpoint(tcp::v4(), 0));
    std::thread server([&] {
        tcp::socket socket(io);
        acceptor.accept(socket);
        const auto request = read_sdk_packet(socket);
        const auto push = encode(kRoomStatePush, 0, 0, "destroy");
        boost::system::error_code ec;
        asio::write(socket, asio::buffer(push), ec);
        std::this_thread::sleep_for(200ms);
        const auto response = encode(kEchoResponse, request.request_id, 0, "late");
        asio::write(socket, asio::buffer(response), ec);
    });

    auto client = std::make_unique<SdkClient>();
    ASSERT_TRUE(client->connect("127.0.0.1", acceptor.local_endpoint().port(), 1s));
    std::promise<void> destroyed;
    auto destroyed_future = destroyed.get_future();
    auto destroy = [&] {
        client.reset();
        destroyed.set_value();
    };
    if (async_push) {
        client->on_async_push([&](const std::string&) { destroy(); });
    } else {
        client->on_push([&](const PushMessage&) { destroy(); });
    }

    auto* raw_client = client.get();
    auto request = std::async(std::launch::async, [raw_client] {
        return raw_client->echo("outer", 2s);
    });
    ASSERT_EQ(destroyed_future.wait_for(2s), std::future_status::ready);
    EXPECT_EQ(client, nullptr);
    ASSERT_EQ(request.wait_for(2s), std::future_status::ready);
    EXPECT_FALSE(request.get().ok);
    server.join();
}

}  // namespace

TEST(ClientV4Test, PushCallbackMayReenterSynchronousApi) {
    verify_push_callback_can_reenter_sync_api(false);
}

TEST(ClientV4Test, AsyncPushCallbackMayReenterSynchronousApi) {
    verify_push_callback_can_reenter_sync_api(true);
}

TEST(ClientV4Test, PushCallbackMayDestroyClientDuringSynchronousRequest) {
    verify_push_callback_can_destroy_client(false);
}

TEST(ClientV4Test, AsyncPushCallbackMayDestroyClientDuringSynchronousRequest) {
    verify_push_callback_can_destroy_client(true);
}

TEST(ClientV4Test, RoomResultPropagatesServerErrorCode) {
    namespace asio = boost::asio;
    using tcp = asio::ip::tcp;
    using namespace boost_gateway::sdk::protocol;

    asio::io_context io;
    tcp::acceptor acceptor(io, tcp::endpoint(tcp::v4(), 0));
    std::thread server([&] {
        tcp::socket socket(io);
        acceptor.accept(socket);
        LengthHeader header{};
        asio::read(socket, asio::buffer(header));
        std::vector<char> payload(decode_length(header));
        asio::read(socket, asio::buffer(payload));
        const auto request = decode_payload(payload);
        const auto response = encode(kErrorResponse, request.request_id, 4242, "room denied");
        asio::write(socket, asio::buffer(response));
    });

    SdkClient client;
    ASSERT_TRUE(client.connect("127.0.0.1", acceptor.local_endpoint().port(), 1s));
    const auto result = client.create_room("forbidden", 1s);
    EXPECT_FALSE(result.ok);
    EXPECT_EQ(result.error_code, 4242);
    EXPECT_EQ(result.error_message, "room denied");
    client.disconnect();
    server.join();
}
