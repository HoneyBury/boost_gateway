#include "app/config.h"
#include "app/crash_handler.h"
#include "app/logging.h"
#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/push_service.h"
#include "game/gateway/session_manager.h"
#include "game/room/room_manager.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <thread>
#include <vector>

namespace asio = boost::asio;

int main(int argc, char* argv[]) {
    app::logging::init("room_server");
    app::crash::install_crash_handler();

    const auto config_path = argc > 1 ? std::filesystem::path(argv[1]) : std::filesystem::path("config/gateway.json");
    auto config = app::config::load_gateway_config(config_path);
    if (argc > 2) config.port = static_cast<std::uint16_t>(std::atoi(argv[2]));

    net::SessionOptions session_opts;
    session_opts.max_packet_size = config.session_max_packet_size;
    session_opts.max_pending_write_bytes = config.session_max_pending_write_bytes;

    asio::io_context io;
    boost::asio::thread_pool pool(config.business_threads);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager session_mgr;
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push;
    game::room::RoomManager room_mgr;
    game::battle::BattleManager battle_mgr;

    game::room::RoomService room_svc(session_mgr, push, battle_mgr, room_mgr, metrics);
    room_svc.register_handlers(dispatcher);

    game::gateway::GatewayServer server(io, dispatcher, session_mgr, room_mgr, battle_mgr,
                                         metrics, config.port, 0, session_opts);
    server.set_connection_limits(config.max_connections, config.per_ip_connection_limit);
    server.start();

    std::vector<std::thread> workers(config.io_threads);
    for (auto& w : workers) w = std::thread([&] { io.run(); });
    for (auto& w : workers) w.join();
    pool.join();
    return 0;
}
