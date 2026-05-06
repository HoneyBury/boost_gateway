// 登录系统演示：展示三种鉴权模式、Token 生命周期、重复登录处理
//
// 运行方式：
//   login_demo.exe [config/gateway.json] [端口号]
//
// 启动后可通过 pressure 工具测试登录流程：
//   gateway_pressure.exe 127.0.0.1 <port> 3 1 echo

#include "app/audit_log.h"
#include "app/config.h"
#include "app/config_watcher.h"
#include "app/crash_handler.h"
#include "app/graceful_shutdown.h"
#include "app/logging.h"
#include "game/gateway/admin_service.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/push_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/http_token_validator.h"
#include "game/login/login_service.h"
#include "game/login/token_validator.h"
#include "game/persistence/player_store.h"
#include "game/room/room_manager.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <atomic>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>

namespace asio = boost::asio;

int main(int argc, char* argv[]) {
    app::logging::init("login_demo");
    app::crash::install_crash_handler();

    const auto config_path = argc > 1 ? argv[1] : "config/gateway.json";
    auto config = app::config::load_gateway_config(config_path);
    if (argc > 2) config.port = static_cast<std::uint16_t>(std::atoi(argv[2]));

    // =================================================================
    // 1. 鉴权模式选择 — 演示三种 provider 的切换
    // =================================================================
    asio::io_context io;
    boost::asio::thread_pool pool(config.business_threads);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager session_mgr;
    game::room::RoomManager room_mgr;
    game::battle::BattleManager battle_mgr;
    room_mgr.set_battle_active_query([&battle_mgr](const std::string& room_id) {
        return battle_mgr.battle_started(room_id);
    });
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push;

    std::unique_ptr<game::login::TokenValidator> validator;
    if (config.auth_provider == "http") {
        LOG_INFO("使用 HTTP 远程鉴权: {}", config.auth_http_endpoint);
        validator = std::make_unique<game::login::HttpTokenValidator>(
            io.get_executor(), config.auth_http_endpoint, config.auth_http_timeout);
    } else if (config.auth_provider == "json_file") {
        auto fv = game::login::JsonFileTokenValidator::load_from_file(
            config.auth_users_path.value_or("config/auth_users.json"));
        if (fv) {
            LOG_INFO("使用 JSON 文件鉴权，用户数: {}", fv->user_count());
            validator = std::make_unique<game::login::JsonFileTokenValidator>(std::move(*fv));
        }
    }
    if (!validator) {
        LOG_INFO("使用开发模式鉴权 (dev token)");
        validator = std::make_unique<game::login::DevTokenValidator>();
    }

    // =================================================================
    // 2. 注册登录业务 — 展示 LoginService 组装
    //    - Token 生命周期: 开发模式 1h TTL, 正式模式 24h TTL
    //    - 重复登录: 旧连接收到 kSessionKickedPush 并被踢下线
    //    - 房间恢复: 新连接自动恢复原房间归属
    // =================================================================
    game::login::LoginService login_svc(session_mgr, push, room_mgr, *validator, metrics);
    login_svc.register_handlers(dispatcher);

    // =================================================================
    // 3. 审计日志 — 所有登录事件自动记录到 logs/audit.log
    //    - login_success: 登录成功
    //    - login_failure: 登录失败（错误 token / token 过期）
    //    - 可通过 tail -f logs/audit.log 实时查看
    // =================================================================
    LOG_INFO("审计日志已启用，输出到 logs/audit.log");

    // =================================================================
    // 4. 二进制 Admin — demo-only、无权限；默认主链不注册（见 docs/v1-governance-layers.md §6）
    // =================================================================
    game::gateway::AdminService admin(session_mgr, metrics);
    admin.set_status_callback([&] {
        return "{\"auth_provider\":\"" + config.auth_provider +
               "\",\"sessions\":" + std::to_string(session_mgr.snapshot().active_sessions) + "}";
    });
    admin.register_handlers(dispatcher);

    // =================================================================
    // 5. 网关 + 安全层 — 连接限制 + 登录防护
    // =================================================================
    game::gateway::GatewayService gw_svc(session_mgr, metrics);
    gw_svc.register_handlers(dispatcher);

    net::SessionOptions session_opts;
    session_opts.max_packet_size = config.session_max_packet_size;
    session_opts.max_pending_write_bytes = config.session_max_pending_write_bytes;

    game::gateway::GatewayServer server(io, dispatcher, session_mgr, room_mgr, battle_mgr,
                                         metrics, config.port, config.http_management_port,
                                         session_opts, config.metrics_log_interval);
    server.set_connection_limits(config.max_connections, config.per_ip_connection_limit);
    server.start();

    // =================================================================
    // 6. 运维能力 — 优雅关闭 + 配置热加载 + 状态持久化
    // =================================================================
    game::persistence::JsonFilePlayerStore player_store("runtime/players");

    app::config::ConfigWatcher watcher(io.get_executor(), config_path,
        [&](const app::config::GatewayAppConfig& new_cfg) {
            LOG_INFO("配置热加载生效");
            AUDIT_LOG("config_reload", "配置文件变更");
            server.set_connection_limits(new_cfg.max_connections, new_cfg.per_ip_connection_limit);
        });
    watcher.start();

    std::atomic<bool> shutdown{false};
    app::GracefulShutdown sig_handler(io.get_executor(), [&] {
        shutdown.store(true);
        watcher.stop();
        AUDIT_LOG("shutdown", "优雅关闭");
        std::size_t saved = 0;
        for (const auto& s : session_mgr.all_sessions()) {
            auto uid = session_mgr.user_id_of(s);
            if (uid) {
                player_store.save({*uid, session_mgr.login_context_of(s)->display_name, 0,
                    std::chrono::duration_cast<std::chrono::seconds>(
                        std::chrono::system_clock::now().time_since_epoch()).count()});
                ++saved;
            }
        }
        LOG_INFO("关闭时保存了 {} 条玩家记录", saved);
        server.stop();
        io.stop();
    });
    sig_handler.start();

    LOG_INFO("=== 登录演示服务器已启动 :{} ===", server.local_port());
    LOG_INFO("功能展示: dev/json_file/http 鉴权 | Token TTL | 顶号踢线 | 审计日志 | 优雅关闭");

    std::vector<std::thread> workers(config.io_threads);
    for (auto& w : workers) w = std::thread([&] { io.run(); });
    for (auto& w : workers) w.join();
    pool.join();
    watcher.stop();
    return 0;
}
