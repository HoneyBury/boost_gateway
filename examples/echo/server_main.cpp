#include "app/logging.h"
#include "game/battle/battle_service.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/login/login_service.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <algorithm>
#include <cstdlib>
#include <cstdint>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;

int main(int argc, char* argv[]) {
    app::logging::init("echo_server");

    const auto port = static_cast<std::uint16_t>(argc > 1 ? std::atoi(argv[1]) : 9000);

    asio::io_context io_context;
    boost::asio::thread_pool business_pool(std::max(2u, std::thread::hardware_concurrency()));

    net::MessageDispatcher dispatcher(business_pool);

    game::gateway::GatewayService gateway_service;
    game::login::LoginService login_service;
    game::room::RoomService room_service;
    game::battle::BattleService battle_service;

    gateway_service.register_handlers(dispatcher);
    login_service.register_handlers(dispatcher);
    room_service.register_handlers(dispatcher);
    battle_service.register_handlers(dispatcher);

    dispatcher.register_handler(
        net::protocol::kEchoRequest,
        [](const std::shared_ptr<net::Session>& session, std::string body) {
            // Echo 示例仍然保留，但已经被放到 examples 并走统一消息分发链路。
            session->send(net::protocol::kEchoResponse, std::move(body));
        });

    game::gateway::GatewayServer server(io_context, dispatcher, port);
    server.start();

    const auto io_thread_count =
        std::max(2u, std::thread::hardware_concurrency() == 0 ? 2u : std::thread::hardware_concurrency());

    std::vector<std::thread> io_workers;
    io_workers.reserve(io_thread_count);
    for (unsigned int i = 0; i < io_thread_count; ++i) {
        io_workers.emplace_back([&io_context]() {
            io_context.run();
        });
    }

    for (auto& worker : io_workers) {
        worker.join();
    }

    business_pool.join();
    return 0;
}
