// 管理工具演示：Admin 二进制消息（demo-only）、HTTP 存活桩/指标、审计日志
//
// 运行方式：
//   admin_demo.exe [config/gateway.json] [端口号]
//
// 测试管理指令（通过 echo_client 或自制客户端发送指定消息号）：
//   - 消息号 5001: 踢人 (body=user_id)
//   - 消息号 5002: 封禁 IP (body=ip_addr)
//   - 消息号 5003: 查询服务器状态
//   - 消息号 5004: 重载配置
//   - 消息号 5005: 接收管理指令响应
//
// HTTP 端口 (默认 9080) — L2 观测导出 + /health 存活桩（固定 ok；非就绪/业务健康）：
//   curl http://localhost:9080/health       → liveness stub
//   curl http://localhost:9080/metrics      → Prometheus 指标
//   curl http://localhost:9080/metrics/json → JSON 指标
//   curl http://localhost:9080/metrics/diagnostics → per-core 诊断视图
//   curl http://localhost:9080/metrics/diagnostics/json → 结构化 per-core 诊断 JSON

#include "app/audit_log.h"
#include "app/config.h"
#include "app/config_watcher.h"
#include "app/crash_handler.h"
#include "app/graceful_shutdown.h"
#include "app/logging.h"
#include "game/gateway/admin_service.h"
#include "game/gateway/gateway_metrics.h"
#include "game/gateway/gateway_metrics_exporter.h"
#include "game/gateway/gateway_server.h"
#include "game/gateway/gateway_service.h"
#include "game/gateway/push_service.h"
#include "game/gateway/session_manager.h"
#include "game/login/login_service.h"
#include "game/login/token_validator.h"
#include "game/persistence/player_store.h"
#include "game/room/room_manager.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"
#include "net/rate_limiter.h"
#include "v2/io/io_engine.h"

#include <boost/asio.hpp>
#include <boost/asio/thread_pool.hpp>

#include <atomic>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>

namespace asio = boost::asio;

int main(int argc, char* argv[]) {
    app::logging::init("admin_demo");
    app::crash::install_crash_handler();

    const auto config_path = argc > 1 ? argv[1] : "config/gateway.json";
    auto config = app::config::load_gateway_config(config_path);
    if (argc > 2) config.port = static_cast<std::uint16_t>(std::atoi(argv[2]));

    asio::io_context io;
    boost::asio::thread_pool pool(config.business_threads);
    net::MessageDispatcher dispatcher(pool);
    game::gateway::SessionManager session_mgr;
    game::gateway::GatewayMetrics metrics;
    game::gateway::PushService push;
    game::room::RoomManager room_mgr;
    game::battle::BattleManager battle_mgr;
    room_mgr.set_battle_active_query([&battle_mgr](const std::string& room_id) {
        return battle_mgr.battle_started(room_id);
    });

    // =================================================================
    // 1. Admin 二进制「管理」消息 — 消息号 5001-5005（demo-only，无任何权限校验）
    //    默认 GatewayServer **不注册**；本示例手工 register_handlers。
    //    - kick_player (5001):  踢出指定用户，body=user_id
    //    - ban_ip (5002):       封禁指定 IP，body=ip_addr
    //    - server_status (5003): 查询服务器运行状态 (JSON)
    //    - reload_config (5004): 触发配置热加载
    //    - admin_response (5005): 接收管理指令执行结果
    // =================================================================
    game::gateway::AdminService admin(session_mgr, metrics, push);

    admin.set_kick_callback([&](const std::string& user_id) {
        for (const auto& s : session_mgr.all_sessions()) {
            auto uid = session_mgr.user_id_of(s);
            if (uid && *uid == user_id) {
                s->stop();
                break;
            }
        }
    });

    admin.set_ban_callback([&](const std::string& ip, std::uint32_t duration) {
        LOG_WARN("已封禁 IP: {} ({} 秒)", ip, duration);
    });

    admin.set_status_callback([&] {
        auto snap = metrics.snapshot();
        return "{\"sessions\":" + std::to_string(session_mgr.snapshot().active_sessions) +
               ",\"rooms\":" + std::to_string(room_mgr.room_count()) +
               ",\"battles\":" + std::to_string(battle_mgr.active_battle_count()) +
               ",\"recv_packets\":" + std::to_string(snap.received_packets) +
               ",\"sent_packets\":" + std::to_string(snap.sent_packets) + "}";
    });

    admin.set_reload_callback([&] { AUDIT_LOG("config_reload", "admin_triggered"); });
    admin.register_handlers(dispatcher);

    // =================================================================
    // 2. L2 HTTP：/metrics*（观测）与 /health（存活桩）；无鉴权，非完整控制面
    //    配置 gateway.http_management_port = 9080
    //    可被 Prometheus 直接 scrape
    // =================================================================
    LOG_INFO("HTTP 观测端点: http://localhost:{}/health (stub)", config.http_management_port);

    // =================================================================
    // 3. 速率限制 — RateLimiter 连接预热 + 用户维度 + 消息类型维度
    // =================================================================
    net::RateLimiter limiter;
    LOG_INFO("速率限制: 正常 {} 条/秒, 预热 {} 条/秒 ({} 秒), 游客 {} 条/秒",
             net::RateLimitConfig{}.max_per_second,
             net::RateLimitConfig{}.warmup_max_per_second,
             net::RateLimitConfig{}.warmup_duration.count(),
             net::RateLimitConfig{}.guest_max_per_second);

    // =================================================================
    // 4. 登录 + 网关 + 持久化
    // =================================================================
    game::login::DevTokenValidator validator;
    game::login::LoginService login_svc(session_mgr, push, room_mgr, validator, metrics);
    login_svc.register_handlers(dispatcher);

    game::gateway::GatewayService gw_svc(session_mgr, metrics, push);
    gw_svc.register_handlers(dispatcher);

    game::persistence::JsonFilePlayerStore player_store("runtime/players");

    net::SessionOptions opts;
    opts.max_packet_size = config.session_max_packet_size;
    opts.max_pending_write_bytes = config.session_max_pending_write_bytes;

    game::gateway::GatewayServer server(io, dispatcher, session_mgr, room_mgr, battle_mgr,
                                         metrics, config.port, config.http_management_port,
                                         opts, config.metrics_log_interval,
                                         {},
                                         std::make_unique<v2::io::AsioIoEngine>(
                                             static_cast<std::uint32_t>(config.io_threads)));
    push.set_write_scheduler(
        [&server](const game::gateway::PushService::SessionPtr& session,
                  game::gateway::PushService::SessionWriteTask task) {
            return server.dispatch_to_session_core(session, task);
        });
    server.set_connection_limits(config.max_connections, config.per_ip_connection_limit);
    server.start();

    // =================================================================
    // 5. 运维能力 — 优雅关闭 + 配置热加载
    // =================================================================
    app::config::ConfigWatcher watcher(io.get_executor(), config_path,
        [&](const app::config::GatewayAppConfig& new_cfg) {
            AUDIT_LOG("config_reload", "文件变更触发");
            server.set_connection_limits(new_cfg.max_connections, new_cfg.per_ip_connection_limit);
        });
    watcher.start();

    std::atomic<bool> shutdown{false};
    app::GracefulShutdown sig_handler(io.get_executor(), [&] {
        shutdown.store(true);
        watcher.stop();
        AUDIT_LOG("shutdown", "signal");
        std::size_t saved = 0;
        for (const auto& s : session_mgr.all_sessions()) {
            auto uid = session_mgr.user_id_of(s);
            if (uid) {
                player_store.save({*uid, "player", 0,
                    std::chrono::duration_cast<std::chrono::seconds>(
                        std::chrono::system_clock::now().time_since_epoch()).count()});
                ++saved;
            }
        }
        LOG_INFO("关闭时保存 {} 条记录", saved);
        server.stop();
        io.stop();
    });
    sig_handler.start();

    LOG_INFO("=== 管理演示服务器已启动 :{} ===", server.local_port());
    LOG_INFO("IO cores: {}", server.io_core_count());
    LOG_INFO("功能展示（均为演示拼装）: Admin(5001-5005,demo-only) | /health(stub)+/metrics | 速率限制 | 审计日志 | 优雅关闭 | 配置热加载");

    std::thread control_worker([&] { io.run(); });
    control_worker.join();
    pool.join();
    watcher.stop();
    return 0;
}
