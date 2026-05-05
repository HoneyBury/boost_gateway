#include "app/logging.h"
#include "net/session.h"

#include <boost/asio.hpp>

#include <cstdlib>
#include <cstdint>
#include <functional>
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
    EchoServer(asio::io_context& io_context, std::uint16_t port)
        : io_context_(io_context),
          // `acceptor_` 持有监听 socket，负责接收新的 TCP 连接。
          acceptor_(io_context, tcp::endpoint(tcp::v4(), port)) {}

    void start() {
        LOG_INFO("Echo server listening on 0.0.0.0:{}", acceptor_.local_endpoint().port());
        do_accept();
    }

private:
    void do_accept() {
        // `async_accept` 异步等待新连接，不会阻塞线程。
        acceptor_.async_accept([this](const error_code& ec, tcp::socket socket) {
            if (!ec) {
                auto session = std::make_shared<net::Session>(std::move(socket));
                const auto key = session.get();

                LOG_INFO("Accepted client {}", session->remote_endpoint());

                session->set_message_handler(
                    [](const std::shared_ptr<net::Session>& session_ptr, std::string message) {
                        // 收到什么就原样写回，保持 echo 语义不变。
                        session_ptr->send(std::move(message));
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
    tcp::acceptor acceptor_;
    std::map<const net::Session*, std::shared_ptr<net::Session>> sessions_;
};

int main(int argc, char* argv[]) {
    app::logging::init("echo_server");  // 在启动网络逻辑前初始化全局日志。

    const auto port = static_cast<std::uint16_t>(argc > 1 ? std::atoi(argv[1]) : 9000);

    asio::io_context io_context;  // 事件循环核心对象，负责驱动异步操作完成回调。
    EchoServer server(io_context, port);
    server.start();

    // 启动多个事件循环线程，让多个客户端连接可以并发推进。
    const auto thread_count =
        std::max(2u, std::thread::hardware_concurrency() == 0 ? 2u : std::thread::hardware_concurrency());

    std::vector<std::thread> workers;
    workers.reserve(thread_count);
    for (unsigned int i = 0; i < thread_count; ++i) {
        workers.emplace_back([&io_context]() {
            // `run()` 会阻塞在这里，把已完成的异步操作分发到当前线程执行。
            io_context.run();
        });
    }

    for (auto& worker : workers) {
        worker.join();  // 等待所有工作线程，保证服务进程持续运行。
    }

    return 0;
}
