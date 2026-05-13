#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <unordered_map>
#include <vector>

#include "v2/gateway/gateway_actor.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/gateway/runtime_helpers.h"
#include "v2/battle/battle_actor.h"
#include "v2/player/player_actor.h"
#include "v2/room/room_actor.h"
#include "v2/runtime/actor_system.h"

namespace v2::gateway {

class BattleArchiveSink;

class Runtime final : public GatewayCommandSink,
                      public v2::battle::BattleEventSink,
                      public v2::player::PlayerEventSink,
                      public v2::room::RoomEventSink {
public:
    struct BattleArchive {
        std::string battle_id;
        std::string room_id;
        std::string reason;
        std::string triggering_user_id;
        std::uint32_t total_frames = 0;
        std::vector<std::string> participant_user_ids;
        std::string replay_payload;
        v2::battle::BattleResultSummary result;
    };

    Runtime(v2::runtime::ActorSystem& actor_system,
            SessionWriteSink& write_sink,
            BattleArchiveSink* archive_sink = nullptr)
        : actor_system_(actor_system), write_sink_(write_sink), archive_sink_(archive_sink) {}

    [[nodiscard]] v2::actor::ActorRef create_gateway_actor();
    [[nodiscard]] bool is_authenticated(const GatewayCommand& command) const;
    void on_session_closed(SessionId session_id);

    bool handle(const GatewayCommand& command) override;
    void push(v2::battle::BattleEvent event) override;
    void push(v2::player::PlayerEvent event) override;
    void push(v2::room::RoomEvent event) override;

    [[nodiscard]] std::optional<BattleArchive> archived_battle(std::string_view battle_id) const;
    void set_archive_sink(BattleArchiveSink* sink) noexcept { archive_sink_ = sink; }
    void set_service_bridge(std::unique_ptr<GatewayServiceBridge> bridge);
    [[nodiscard]] GatewayServiceBridge* service_bridge() const noexcept { return bridge_.get(); }

private:
    struct PendingSettlementAck {
        int expected_acks = 0;
        int received_acks = 0;
    };

    [[nodiscard]] v2::actor::ActorRef get_or_create_player(const std::string& user_id);
    [[nodiscard]] std::string session_user_id(SessionId session_id) const;
    [[nodiscard]] std::optional<SessionId> session_id_for_user(const std::string& user_id) const;
    void archive_battle(const v2::battle::BattleSettlementPreparedMsg& settlement);
    void process_battle_finished(const v2::battle::BattleFinishedMsg& finished);
    void process_deferred_finished(const std::string& battle_id);

    void emit(std::uint16_t message_id,
              SessionId session_id,
              std::uint32_t request_id,
              std::int32_t error_code,
              std::string body);

    v2::runtime::ActorSystem& actor_system_;
    SessionWriteSink& write_sink_;
    SessionLookup lookup_;
    std::unordered_map<std::string, v2::actor::ActorRef> battles_by_room_id_;
    std::unordered_map<std::string, std::string> battle_ids_by_room_id_;  // bridge mode: room_id → battle_id
    std::unordered_map<SessionId, PendingResponse> pending_login_;
    std::unordered_map<std::string, PendingResponse> pending_room_create_;
    std::unordered_map<std::string, PendingResponse> pending_room_join_;
    std::unordered_map<std::string, PendingResponse> pending_room_ready_;
    std::unordered_map<std::string, PendingResponse> pending_battle_start_;
    std::unordered_map<SessionId, PendingResponse> pending_battle_input_;
    std::unordered_map<SessionId, PendingResponse> pending_battle_end_;
    std::unordered_map<std::string, BattleArchive> archived_battles_;
    std::unordered_map<std::string, PendingSettlementAck> pending_settlement_acks_;
    std::unordered_map<std::string, v2::battle::BattleFinishedMsg> deferred_finished_events_;
    std::unordered_map<std::string, v2::runtime::ScheduleHandle> pending_battle_timeout_;
    std::uint64_t next_battle_id_ = 1;
    BattleArchiveSink* archive_sink_ = nullptr;
    std::unique_ptr<GatewayServiceBridge> bridge_;
};

}  // namespace v2::gateway
