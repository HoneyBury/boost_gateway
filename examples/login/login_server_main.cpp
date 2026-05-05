#include "app/config.h"
#include "app/crash_handler.h"
#include "app/logging.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/push_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/login_service.h"
#include "game/login/token_validator.h"
#include "game/login/http_token_validator.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <thread>
#include <vector>

namespace asio = boost::asio;

int main(int argc, char* argv[]) {
    app::logging::init("login_server");
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

    std::unique_ptr<game::login::TokenValidator> validator;
    if (config.auth_provider == "http") {
        validator = std::make_unique<game::login::HttpTokenValidator>(
            io.get_executor(), config.auth_http_endpoint, config.auth_http_timeout);
    } else if (config.auth_provider == "json_file") {
        auto fv = game::login::JsonFileTokenValidator::load_from_file(
            config.auth_users_path.value_or("config/auth_users.json"));
        if (fv) validator = std::make_unique<game::login::JsonFileTokenValidator>(std::move(*fv));
    }
    if (!validator) validator = std::make_unique<game::login::DevTokenValidator>();

    game::login::LoginService login_svc(session_mgr, push, room_mgr, *validator, metrics);
    login_svc.register_handlers(dispatcher);

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
