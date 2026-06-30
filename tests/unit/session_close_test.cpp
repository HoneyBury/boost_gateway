#include "app/logging.h"
#include "net/packet_codec.h"
#include "net/session.h"

#include <boost/asio.hpp>

#include <array>
#include <atomic>
#include <future>
#include <thread>
#include <vector>

#include <gtest/gtest.h>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

// v1.1.2 / T03: 验证 Session 主动关闭与异常关闭走同一条收口路径，确保 close_handler_
// 一定会被触发，并且只触发一次。这是 v1.1.x 维护期主链生命周期闭环的基础。
//
// 设计上不再依赖真实的 TCP accept/connect：handle_close 不要求 socket 处于已连接状态，
// 它只需要在 strand 上跑一次即可走完关闭收口。这样既能纯单元化（无端口竞用），
// 也能稳定区分 "stop() 没触发 close_handler" 与 "网络异常导致额外触发"。
//
// 参考 docs/development-optimization.md §6.A.3 / §8.2 与
// docs/v1-maturity-matrix.md §"不应被宣称为完成态的能力"中的 close-path 一致性要求。

class SessionCloseTest : public ::testing::Test {
protected:
    void SetUp() override {
        // handle_close() 内部走 LOG_INFO，要求 logger 已经初始化；否则会抛
        // std::logic_error 让 io_context.run() 异常退出，看起来像 close_handler_ 没被触发。
        app::logging::init("project_tests");
    }
};

}  // namespace

TEST_F(SessionCloseTest, StopFiresCloseHandlerExactlyOnce) {
    asio::io_context io_context;
    auto session = std::make_shared<net::Session>(tcp::socket(io_context));

    std::atomic<int> close_call_count{0};
    session->set_close_handler(
        [&close_call_count](const std::shared_ptr<net::Session>&,
                            const boost::system::error_code&) {
            close_call_count.fetch_add(1, std::memory_order_relaxed);
        });

    session->stop();

    // stop() 通过 asio::post(strand_, ...) 把 handle_close 投递到 strand，需要
    // 让 io_context 实际执行一次。strand 没有 work_guard，因此 run() 跑完即返回。
    io_context.run();

    EXPECT_EQ(close_call_count.load(), 1)
        << "v1.1.2 T03: Session::stop() 必须经由 handle_close() 统一收口触发 close_handler_，"
           "且仅触发一次。";
}

TEST_F(SessionCloseTest, MultipleStopCallsAreIdempotent) {
    asio::io_context io_context;
    auto session = std::make_shared<net::Session>(tcp::socket(io_context));

    std::atomic<int> close_call_count{0};
    session->set_close_handler(
        [&close_call_count](const std::shared_ptr<net::Session>&,
                            const boost::system::error_code&) {
            close_call_count.fetch_add(1, std::memory_order_relaxed);
        });

    session->stop();
    session->stop();
    session->stop();

    io_context.run();

    EXPECT_EQ(close_call_count.load(), 1)
        << "v1.1.2 T03: 反复调用 Session::stop() 必须保持幂等，close_handler_ 只能触发一次，"
           "避免上层 SessionManager / RoomManager / GatewayMetrics 出现双减引用计数。";
}

TEST_F(SessionCloseTest, StopReceivesOperationAbortedReason) {
    asio::io_context io_context;
    auto session = std::make_shared<net::Session>(tcp::socket(io_context));

    boost::system::error_code captured;
    std::atomic<bool> done{false};
    session->set_close_handler(
        [&captured, &done](const std::shared_ptr<net::Session>&,
                           const boost::system::error_code& ec) {
            captured = ec;
            done.store(true, std::memory_order_release);
        });

    session->stop();
    io_context.run();

    ASSERT_TRUE(done.load());
    // 主动 stop() 通过 handle_close(operation_aborted) 收口，便于上层在 close_handler
    // 中区分 "运维 / 治理触发的主动关闭" 和 "网络读写异常导致的被动关闭"。
    EXPECT_EQ(captured, asio::error::operation_aborted)
        << "v1.1.2 T03: Session::stop() 必须把 operation_aborted 交给 close_handler_，"
           "供上层区分主动停服与网络异常关闭。";
}

// v1.1.2 / T03 回归: 即使 close_handler_ 没被设置，stop() 也必须安全退出，
// 不能出现空函数对象调用或重复关闭 socket 触发 UB。
TEST_F(SessionCloseTest, StopWithoutCloseHandlerIsSafe) {
    asio::io_context io_context;
    auto session = std::make_shared<net::Session>(tcp::socket(io_context));

    session->stop();
    EXPECT_NO_THROW(io_context.run());
}

TEST_F(SessionCloseTest, HighPriorityWritesKeepFifoBeforeQueuedPushes) {
    asio::io_context server_io;
    asio::io_context client_io;

    tcp::acceptor acceptor(server_io, tcp::endpoint(tcp::v4(), 0));
    const auto endpoint = acceptor.local_endpoint();

    std::promise<tcp::socket> accepted_socket;
    acceptor.async_accept(
        [&accepted_socket](boost::system::error_code ec, tcp::socket socket) mutable {
            ASSERT_FALSE(ec);
            accepted_socket.set_value(std::move(socket));
        });

    tcp::socket client_socket(client_io);
    client_socket.connect(endpoint);
    server_io.run();
    server_io.restart();

    net::SessionOptions options;
    options.max_pending_write_bytes = 1024 * 1024;
    auto session = std::make_shared<net::Session>(accepted_socket.get_future().get(), options);

    session->send(4006, 1, 0, "push-1");
    session->send(2002, 2, 0, "response-1", 0, true);
    session->send(2002, 3, 0, "response-2", 0, true);
    session->send(4006, 4, 0, "push-2");

    std::thread io_thread([&server_io]() { server_io.run(); });

    auto read_packet = [&client_socket]() {
        std::array<unsigned char, net::packet::kLengthHeaderSize> header{};
        boost::asio::read(client_socket, boost::asio::buffer(header));
        const auto length = net::packet::decode_length(header);
        std::vector<char> payload(length);
        boost::asio::read(client_socket, boost::asio::buffer(payload));
        return net::packet::decode_payload(payload);
    };

    const auto first = read_packet();
    const auto second = read_packet();
    const auto third = read_packet();
    const auto fourth = read_packet();

    session->stop();
    server_io.stop();
    if (io_thread.joinable()) {
        io_thread.join();
    }

    EXPECT_EQ(first.request_id, 1U);
    EXPECT_EQ(first.body, "push-1");
    EXPECT_EQ(second.request_id, 2U);
    EXPECT_EQ(second.body, "response-1");
    EXPECT_EQ(third.request_id, 3U);
    EXPECT_EQ(third.body, "response-2");
    EXPECT_EQ(fourth.request_id, 4U);
    EXPECT_EQ(fourth.body, "push-2");

}
