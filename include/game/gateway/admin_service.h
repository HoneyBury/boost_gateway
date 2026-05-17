#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/push_service.h"
#include "game/gateway/session_manager.h"

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

namespace net {

struct DispatchContext;
class MessageDispatcher;

}  // namespace net

namespace game::gateway {

// TCP 消息号 5001–5005：演示/示例用；默认 GatewayServer **不注册**。
// **v1.1.11**：调用前提与最小审计口径见 **`docs/v1-admin-audit-rules.md`**。
// **v3.3.x/P2**：默认启用最小 ACL。集成方必须配置共享密钥、可信 peer，
// 或在 demo-only 入口显式关闭 ACL。
class AdminService {
public:
    using KickCallback = std::function<void(const std::string& user_id)>;
    using BanCallback = std::function<void(const std::string& ip, std::uint32_t duration_sec)>;
    using StatusCallback = std::function<std::string()>;
    using ReloadCallback = std::function<void()>;

    struct AccessControl {
        bool enabled = true;
        std::string shared_secret;
        std::vector<std::string> trusted_peer_prefixes;
    };

    AdminService(SessionManager& sm, GatewayMetrics& m, PushService& push_service)
        : push_service_(push_service) {
        (void)sm;
        (void)m;
    }

    void set_kick_callback(KickCallback cb) { on_kick_ = std::move(cb); }
    void set_ban_callback(BanCallback cb) { on_ban_ = std::move(cb); }
    void set_status_callback(StatusCallback cb) { on_status_ = std::move(cb); }
    void set_reload_callback(ReloadCallback cb) { on_reload_ = std::move(cb); }
    void set_access_control(AccessControl acl) { acl_ = std::move(acl); }

    void register_handlers(net::MessageDispatcher& dispatcher);

private:
    [[nodiscard]] bool is_authorized(const net::DispatchContext& ctx) const;
    [[nodiscard]] std::string strip_auth_prefix(const std::string& body) const;

    PushService& push_service_;
    AccessControl acl_;
    KickCallback on_kick_;
    BanCallback on_ban_;
    StatusCallback on_status_;
    ReloadCallback on_reload_;
};

}  // namespace game::gateway
