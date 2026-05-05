#pragma once

#include "game/gateway/gateway_metrics.h"
#include "game/gateway/session_manager.h"
#include "net/message_dispatcher.h"
#include "net/protocol.h"

#include <cstdint>
#include <functional>
#include <string>

namespace game::gateway {

class AdminService {
public:
    using KickCallback = std::function<void(const std::string& user_id)>;
    using BanCallback = std::function<void(const std::string& ip, std::uint32_t duration_sec)>;
    using StatusCallback = std::function<std::string()>;
    using ReloadCallback = std::function<void()>;

    AdminService(SessionManager& sm, GatewayMetrics& m)
        : session_manager_(sm), metrics_(m) {}

    void set_kick_callback(KickCallback cb) { on_kick_ = std::move(cb); }
    void set_ban_callback(BanCallback cb) { on_ban_ = std::move(cb); }
    void set_status_callback(StatusCallback cb) { on_status_ = std::move(cb); }
    void set_reload_callback(ReloadCallback cb) { on_reload_ = std::move(cb); }

    void register_handlers(net::MessageDispatcher& dispatcher) {
        dispatcher.register_handler(net::protocol::kAdminServerStatus,
            [this](const net::DispatchContext& ctx) {
                auto status = on_status_ ? on_status_() : "{}";
                ctx.session->send(net::protocol::kAdminResponse, ctx.request_id, 0, std::move(status));
            });

        dispatcher.register_handler(net::protocol::kAdminReloadConfig,
            [this](const net::DispatchContext& ctx) {
                if (on_reload_) on_reload_();
                ctx.session->send(net::protocol::kAdminResponse, ctx.request_id, 0, "reload_ok");
            });

        dispatcher.register_handler(net::protocol::kAdminKickPlayer,
            [this](const net::DispatchContext& ctx) {
                if (on_kick_) on_kick_(ctx.body);
                ctx.session->send(net::protocol::kAdminResponse, ctx.request_id, 0, "kick_ok");
            });

        dispatcher.register_handler(net::protocol::kAdminBanIp,
            [this](const net::DispatchContext& ctx) {
                if (on_ban_) on_ban_(ctx.body, 3600);
                ctx.session->send(net::protocol::kAdminResponse, ctx.request_id, 0, "ban_ok");
            });
    }

private:
    SessionManager& session_manager_;
    GatewayMetrics& metrics_;
    KickCallback on_kick_;
    BanCallback on_ban_;
    StatusCallback on_status_;
    ReloadCallback on_reload_;
};

}  // namespace game::gateway
