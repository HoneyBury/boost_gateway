#include "app/config.h"
#include "app/crash_handler.h"
#include "app/logging.h"
#include "game/battle/battle_service.h"
#include "game/battle/battle_manager.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/push_service.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/login_service.h"
#include "game/login/http_token_validator.h"
#include "game/login/token_validator.h"
#include "game/room/room_manager.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <thread>
#include <vector>

namespace asio = boost::asio;

namespace {

bool is_numeric_arg(const char* value) {
    if (value == nullptr || *value == '\0') {
        return false;
    }

    for (const auto* current = value; *current != '\0'; ++current) {
        if (!std::isdigit(static_cast<unsigned char>(*current))) {
            return false;
        }
    }

    return true;
}

}  // namespace

int main(int argc, char* argv[]) {
    app::logging::init("echo_server");
    app::crash::install_crash_handler();

    std::filesystem::path config_path = "config/gateway.json";
    std::uint16_t port_override = 0;
    if (argc > 1) {
        if (is_numeric_arg(argv[1])) {
            port_override = static_cast<std::uint16_t>(std::atoi(argv[1]));
        } else {
            config_path = argv[1];
        }
    }
    if (argc > 2 && is_numeric_arg(argv[2])) {
        port_override = static_cast<std::uint16_t>(std::atoi(argv[2]));
    }

    auto config = app::config::load_gateway_config(config_path);
    if (port_override != 0) {
        config.port = port_override;
    }
    net::SessionOptions session_options;
    session_options.max_packet_size = config.session_max_packet_size;
    session_options.max_pending_write_bytes = config.session_max_pending_write_bytes;
    session_options.heartbeat_check_interval = config.session_heartbeat_check_interval;
    session_options.heartbeat_timeout = config.session_heartbeat_timeout;

    asio::io_context io_context;
    boost::asio::thread_pool business_pool(config.business_threads);
    net::MessageDispatcher dispatcher(business_pool);
    game::gateway::SessionManager session_manager;
    game::room::RoomManager room_manager;
    game::battle::BattleManager battle_manager;
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push_service;
    std::unique_ptr<game::login::TokenValidator> token_validator;

    if (config.auth_provider == "json_file") {
        const auto auth_path = config.auth_users_path.value_or(std::filesystem::path("config/auth_users.json"));
        const auto file_validator = game::login::JsonFileTokenValidator::load_from_file(auth_path);
        if (!file_validator) {
            LOG_ERROR("Failed to load json auth users from {}", auth_path.string());
            return 1;
        }

        LOG_INFO("Using json file token validator with {} users from {}",
                 file_validator->user_count(),
                 auth_path.string());
        token_validator = std::make_unique<game::login::JsonFileTokenValidator>(std::move(*file_validator));
    } else if (config.auth_provider == "http") {
        LOG_INFO("Using HTTP token validator at {}", config.auth_http_endpoint);
        token_validator = std::make_unique<game::login::HttpTokenValidator>(
            io_context.get_executor(),
            config.auth_http_endpoint,
            config.auth_http_timeout);
    } else {
        LOG_INFO("Using development token validator");
        token_validator = std::make_unique<game::login::DevTokenValidator>();
    }

    game::gateway::GatewayService gateway_service(session_manager, metrics);
    game::login::LoginService login_service(session_manager, push_service, room_manager, *token_validator, metrics);
    game::room::RoomService room_service(session_manager, push_service, battle_manager, room_manager, metrics);
    game::battle::BattleService battle_service(session_manager, push_service, room_manager, battle_manager, metrics);

    gateway_service.register_handlers(dispatcher);
    login_service.register_handlers(dispatcher);
    room_service.register_handlers(dispatcher);
    battle_service.register_handlers(dispatcher);

    dispatcher.register_handler(
        net::protocol::kEchoRequest,
        [](const net::DispatchContext& context) {
            // Echo 示例仍然保留，用于快速验证网络链路是否通畅。
            context.session->send(net::protocol::kEchoResponse,
                                  context.request_id,
                                  static_cast<std::int32_t>(net::protocol::ErrorCode::kOk),
                                  context.body);
        });

    game::gateway::GatewayServer server(
        io_context,
        dispatcher,
        session_manager,
        room_manager,
        battle_manager,
        metrics,
        config.port,
        config.http_management_port,
        session_options,
        config.metrics_log_interval,
        {
            .prometheus_path = config.metrics_prometheus_path,
            .json_path = config.metrics_json_path,
        });
    server.set_connection_limits(config.max_connections, config.per_ip_connection_limit);
    server.start();

    const auto io_thread_count = config.io_threads;

    std::vector<std::thread> io_workers;
    io_workers.reserve(io_thread_count);
    for (unsigned int i = 0; i < io_thread_count; ++i) {
        io_workers.emplace_back([&io_context]() { io_context.run(); });
    }

    for (auto& worker : io_workers) {
        worker.join();
    }

    business_pool.join();
    return 0;
}
