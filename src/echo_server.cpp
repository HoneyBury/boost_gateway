#include "app/logging.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"
#include "net/session.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <algorithm>
#include <cstdlib>
#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;
using boost::system::error_code;
using tcp = asio::ip::tcp;

class EchoServer {
public:
    EchoServer(asio::io_context& io_context,
               boost::asio::thread_pool& business_pool,
               std::uint16_t port)
        : io_context_(io_context),
          dispatcher_(business_pool),
          // acceptor_ 持有监听 socket，负责接收新的 TCP 连接。
          acceptor_(io_context, tcp::endpoint(tcp::v4(), port)) {
        register_handlers();
    }

    void start() {
        LOG_INFO("Echo server listening on 0.0.0.0:{}", acceptor_.local_endpoint().port());
        do_accept();
    }

private:
    void register_handlers() {
        dispatcher_.register_handler(
            net::protocol::kHeartbeatRequest,
            [](const std::shared_ptr<net::Session>& session, std::string) {
                // 心跳包在业务线程池里处理，随后返回一个心跳响应。
                session->send(net::protocol::kHeartbeatResponse, "pong");
            });

        dispatcher_.register_handler(
            net::protocol::kEchoRequest,
            [](const std::shared_ptr<net::Session>& session, std::string body) {
                // Echo 逻辑仍然非常简单，只是现在通过消息号分发进来。
                session->send(net::protocol::kEchoResponse, std::move(body));
            });
    }

    void do_accept() {
        // async_accept 异步等待新连接，不会阻塞线程。
        acceptor_.async_accept([this](const error_code& ec, tcp::socket socket) {
            if (!ec) {
                auto session = std::make_shared<net::Session>(std::move(socket));
                const auto key = session.get();

                LOG_INFO("Accepted client {}", session->remote_endpoint());

                session->set_packet_handler(
                    [this](const std::shared_ptr<net::Session>& session_ptr,
                           std::uint16_t message_id,
                           std::string body) {
                        // Session 只负责拿到完整包，真正业务处理交给消息分发器和业务线程池。
                        dispatcher_.dispatch(session_ptr, message_id, std::move(body));
                    });

                session->set_close_handler(
                    [this, key](const std::shared_ptr<net::Session>&, const error_code&) {
                        sessions_.erase(key);
                    });

                sessions_.emplace(key, session);
                session->start();
            } else {
                LOG_ERROR("Accept failed: {}", ec.message());
            }

            // 立刻挂起下一次 accept，确保服务端可以持续处理多个客户端。
            do_accept();
        });
    }

    asio::io_context& io_context_;
    net::MessageDispatcher dispatcher_;
    tcp::acceptor acceptor_;
    std::map<const net::Session*, std::shared_ptr<net::Session>> sessions_;
};

int main(int argc, char* argv[]) {
    app::logging::init("echo_server");  // 在启动网络逻辑前初始化全局日志。

    const auto port = static_cast<std::uint16_t>(argc > 1 ? std::atoi(argv[1]) : 9000);

    asio::io_context io_context;  // 网络线程只负责驱动异步收发和连接状态机。
    boost::asio::thread_pool business_pool(std::max(2u, std::thread::hardware_concurrency()));

    EchoServer server(io_context, business_pool, port);
    server.start();

    // 启动多个网络线程，让多个连接可以并发推进。
    const auto io_thread_count =
        std::max(2u, std::thread::hardware_concurrency() == 0 ? 2u : std::thread::hardware_concurrency());

    std::vector<std::thread> io_workers;
    io_workers.reserve(io_thread_count);
    for (unsigned int i = 0; i < io_thread_count; ++i) {
        io_workers.emplace_back([&io_context]() {
            // run() 会阻塞在这里，把已完成的异步操作分发到当前网络线程执行。
            io_context.run();
        });
    }

    for (auto& worker : io_workers) {
        worker.join();  // 等待所有网络线程退出。
    }

    business_pool.join();  // 等待业务线程池完成收尾。
    return 0;
}
