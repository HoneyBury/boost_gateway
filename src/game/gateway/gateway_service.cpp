#include "game/gateway/gateway_service.h"

#include "net/protocol.h"

namespace game::gateway {

void GatewayService::register_handlers(net::MessageDispatcher& dispatcher) const {
    dispatcher.register_handler(
        net::protocol::kHeartbeatRequest,
        [](const std::shared_ptr<net::Session>& session, std::string) {
            // 心跳请求直接返回一个固定响应，刷新活跃状态由 Session 自己完成。
            session->send(net::protocol::kHeartbeatResponse, "pong");
        });
}

}  // namespace game::gateway
