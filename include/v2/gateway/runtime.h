#pragma once

#include <string>
#include <unordered_map>

#include "v2/gateway/gateway_actor.h"
#include "v2/battle/battle_actor.h"
#include "v2/player/player_actor.h"
#include "v2/room/room_actor.h"
#include "v2/runtime/actor_system.h"

namespace v2::gateway {

class Runtime final : public GatewayCommandSink,
                      public v2::battle::BattleEventSink,
                      public v2::player::PlayerEventSink,
                      public v2::room::RoomEventSink {
public:
    Runtime(v2::runtime::ActorSystem& actor_system, SessionWriteSink& write_sink)
        : actor_system_(actor_system), write_sink_(write_sink) {}

    [[nodiscard]] v2::actor::ActorRef create_gateway_actor();
    [[nodiscard]] bool is_authenticated(const GatewayCommand& command) const;

    bool handle(const GatewayCommand& command) override;
    void push(v2::battle::BattleEvent event) override;
    void push(v2::player::PlayerEvent event) override;
    void push(v2::room::RoomEvent event) override;

private:
    struct PendingResponse {
        SessionId session_id = 0;
        std::uint32_t request_id = 0;
    };

    [[nodiscard]] v2::actor::ActorRef get_or_create_player(const std::string& user_id);
    [[nodiscard]] std::string parse_login_user_id(const std::string& body) const;
    [[nodiscard]] std::string parse_display_name(const std::string& body) const;
    [[nodiscard]] std::string session_user_id(SessionId session_id) const;

    void emit(std::uint16_t message_id,
              SessionId session_id,
              std::uint32_t request_id,
              std::int32_t error_code,
              std::string body);

    v2::runtime::ActorSystem& actor_system_;
    SessionWriteSink& write_sink_;
    std::unordered_map<std::string, v2::actor::ActorRef> players_by_user_id_;
    std::unordered_map<SessionId, std::string> users_by_session_id_;
    std::unordered_map<SessionId, std::string> rooms_by_session_id_;
    std::unordered_map<std::string, v2::actor::ActorRef> rooms_by_room_id_;
    std::unordered_map<std::string, v2::actor::ActorRef> battles_by_room_id_;
    std::unordered_map<SessionId, PendingResponse> pending_login_;
    std::unordered_map<std::string, PendingResponse> pending_room_create_;
    std::unordered_map<std::string, PendingResponse> pending_room_join_;
    std::unordered_map<std::string, PendingResponse> pending_room_ready_;
    std::unordered_map<std::string, PendingResponse> pending_battle_start_;
    std::uint64_t next_battle_id_ = 1;
};

}  // namespace v2::gateway
