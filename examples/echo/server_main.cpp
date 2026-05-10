#include "app/audit_log.h"
#include "app/config.h"
#include "app/config_watcher.h"
#include "app/crash_handler.h"
#include "app/graceful_shutdown.h"
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
#include "game/persistence/player_store.h"
#include "game/room/room_manager.h"
#include "game/room/room_service.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"
#include "v2/gateway/gateway_server_bridge.h"
#include "v2/io/io_engine.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <algorithm>
#include <atomic>
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
    room_manager.set_battle_active_query([&battle_manager](const std::string& room_id) {
        return battle_manager.battle_started(room_id);
    });
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push_service;
    std::unique_ptr<game::login::TokenValidator> token_validator;
    std::shared_ptr<v2::gateway::GatewayServerShadowBridge> shadow_bridge;

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

    game::gateway::GatewayService gateway_service(session_manager, metrics, push_service);
    game::login::LoginService login_service(session_manager, push_service, room_manager, *token_validator, metrics);
    game::room::RoomService room_service(session_manager, push_service, battle_manager, room_manager, metrics);
    game::battle::BattleService battle_service(session_manager, push_service, room_manager, battle_manager, metrics);

    gateway_service.register_handlers(dispatcher);
    login_service.register_handlers(dispatcher);
    room_service.register_handlers(dispatcher);
    battle_service.register_handlers(dispatcher);

    dispatcher.register_handler(
        net::protocol::kEchoRequest,
        [&push_service](const net::DispatchContext& context) {
            // Echo 示例仍然保留，用于快速验证网络链路是否通畅。
            push_service.send_ok(context.session,
                                 net::protocol::kEchoResponse,
                                 context.request_id,
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
        },
        std::make_unique<v2::io::AsioIoEngine>(static_cast<std::uint32_t>(config.io_threads)));
    push_service.set_write_scheduler(
        [&server](const game::gateway::PushService::SessionPtr& session,
                  game::gateway::PushService::SessionWriteTask task) {
            return server.dispatch_to_session_core(session, task);
        });
    if (config.v2_shadow_bridge_enabled) {
        const auto mirror_policy = v2::gateway::make_shadow_bridge_policy(config);
        const auto emit_policy = v2::gateway::make_shadow_bridge_emit_policy(config);
        shadow_bridge = std::make_shared<v2::gateway::GatewayServerShadowBridge>(
            mirror_policy,
            emit_policy,
            config.v2_shadow_bridge_emit_responses);
        shadow_bridge->set_write_scheduler(
            [&server](const std::shared_ptr<net::Session>& session,
                      v2::gateway::GatewayServerShadowBridge::SessionWriteTask task) {
                return server.dispatch_to_session_core(session, std::move(task));
            });
        server.set_packet_bridge(shadow_bridge);
        LOG_INFO("Enabled v2 shadow bridge (emit_responses={}, login={}, room={}, battle={}, echo={}, battle_input_push={}, state_started={}, state_frame={}, state_settlement={}, state_finished={})",
                 config.v2_shadow_bridge_emit_responses ? "true" : "false",
                 config.v2_shadow_bridge_login ? "true" : "false",
                 config.v2_shadow_bridge_room ? "true" : "false",
                 config.v2_shadow_bridge_battle ? "true" : "false",
                 config.v2_shadow_bridge_echo ? "true" : "false",
                 config.v2_shadow_bridge_emit_battle_input_push ? "true" : "false",
                 config.v2_shadow_bridge_emit_battle_state_started ? "true" : "false",
                 config.v2_shadow_bridge_emit_battle_state_frame ? "true" : "false",
                 config.v2_shadow_bridge_emit_battle_state_settlement ? "true" : "false",
                 config.v2_shadow_bridge_emit_battle_state_finished ? "true" : "false");
    }
    server.set_connection_limits(config.max_connections, config.per_ip_connection_limit);
    server.start();

    // Persistence: setup player store for shutdown save
    game::persistence::JsonFilePlayerStore player_store("runtime/players");

    // Config hot-reload
    app::config::ConfigWatcher watcher(io_context.get_executor(), config_path,
        [&](const app::config::GatewayAppConfig& new_cfg) {
            LOG_INFO("Config hot-reload applied");
            AUDIT_LOG("config_reload", "Config file changed, reloaded");
            server.set_connection_limits(new_cfg.max_connections, new_cfg.per_ip_connection_limit);
            if (new_cfg.v2_shadow_bridge_enabled != config.v2_shadow_bridge_enabled ||
                new_cfg.v2_shadow_bridge_emit_responses != config.v2_shadow_bridge_emit_responses ||
                new_cfg.v2_shadow_bridge_login != config.v2_shadow_bridge_login ||
                new_cfg.v2_shadow_bridge_room != config.v2_shadow_bridge_room ||
                new_cfg.v2_shadow_bridge_battle != config.v2_shadow_bridge_battle ||
                new_cfg.v2_shadow_bridge_echo != config.v2_shadow_bridge_echo ||
                new_cfg.v2_shadow_bridge_emit_battle_input_push != config.v2_shadow_bridge_emit_battle_input_push ||
                new_cfg.v2_shadow_bridge_emit_battle_state_started != config.v2_shadow_bridge_emit_battle_state_started ||
                new_cfg.v2_shadow_bridge_emit_battle_state_frame != config.v2_shadow_bridge_emit_battle_state_frame ||
                new_cfg.v2_shadow_bridge_emit_battle_state_settlement != config.v2_shadow_bridge_emit_battle_state_settlement ||
                new_cfg.v2_shadow_bridge_emit_battle_state_finished != config.v2_shadow_bridge_emit_battle_state_finished) {
                LOG_WARN("v2 shadow bridge settings are startup-only and were not hot-reloaded");
            }
        });
    watcher.start();

    // Graceful shutdown: save player data before exit
    std::atomic<bool> shutdown{false};
    app::GracefulShutdown sig_handler(io_context.get_executor(), [&]() {
        shutdown.store(true);
        watcher.stop();
        AUDIT_LOG("shutdown", "Graceful shutdown initiated");
        // Save authenticated player data
        std::size_t saved = 0;
        for (const auto& session : session_manager.all_sessions()) {
            auto uid = session_manager.user_id_of(session);
            if (uid) {
                auto ctx = session_manager.login_context_of(session);
                game::persistence::PlayerRecord rec;
                rec.user_id = *uid;
                rec.display_name = ctx ? ctx->display_name : *uid;
                rec.last_login_ts = std::chrono::duration_cast<std::chrono::seconds>(
                    std::chrono::system_clock::now().time_since_epoch()).count();
                if (player_store.save(rec)) ++saved;
            }
        }
        LOG_INFO("Saved {} player records on shutdown", saved);
        server.stop();
        io_context.stop();
    });
    sig_handler.start();

    std::thread control_worker([&io_context]() { io_context.run(); });

    control_worker.join();

    business_pool.join();
    watcher.stop();
    return 0;
}
