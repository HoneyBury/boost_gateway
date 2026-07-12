#pragma once

#include <cstdint>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include <nlohmann/json.hpp>

#include "v2/auth/authorizer.h"
#include "v2/gateway/backend_metrics.h"
#include "v2/service/service_registry.h"
#include "v2/gateway/gateway_actor.h"
#include "v2/gateway/gateway_service_bridge.h"
#include "v2/gateway/runtime_helpers.h"
#include "v2/gateway/schema_validator.h"
#include "v2/battle/battle_actor.h"
#include "v2/match/match_protocol.h"
#include "v2/match/matchmaking_service.h"
#include "v2/player/player_actor.h"
#include "v2/room/room_actor.h"
#include "v2/runtime/actor_system.h"

namespace v2::diagnostics {
class DiagnosticsManager;
class HealthCheck;
}  // namespace v2::diagnostics

namespace v2::gateway {

class BattleArchiveSink;

class Runtime final : public GatewayCommandSink,
                      public v2::battle::BattleEventSink,
                      public v2::player::PlayerEventSink,
                      public v2::room::RoomEventSink {
public:
    struct BattleRouteDiagnostics {
        std::uint64_t completed_tasks = 0;
        std::uint64_t queued_tasks = 0;
        std::uint64_t total_queue_wait_us = 0;
        std::uint64_t max_queue_wait_us = 0;
        std::uint64_t total_task_execution_us = 0;
        std::uint64_t max_task_execution_us = 0;
        std::uint64_t total_backend_route_us = 0;
        std::uint64_t max_backend_route_us = 0;
        std::uint64_t total_response_dispatch_us = 0;
        std::uint64_t max_response_dispatch_us = 0;
    };

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
            BattleArchiveSink* archive_sink = nullptr);
    ~Runtime();

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

    // ── Diagnostics integration ─────────────────────────────────────────
    [[nodiscard]] v2::diagnostics::DiagnosticsManager& diagnostics() noexcept { return *diagnostics_; }
    [[nodiscard]] const v2::diagnostics::DiagnosticsManager& diagnostics() const noexcept { return *diagnostics_; }
    [[nodiscard]] v2::diagnostics::HealthCheck& health_check() noexcept { return *health_check_; }
    [[nodiscard]] const v2::diagnostics::HealthCheck& health_check() const noexcept { return *health_check_; }

    // Wire diagnostics data sources. These delegate to the internal DiagnosticsManager.
    void set_backend_metrics_for_diagnostics(std::shared_ptr<BackendMetrics> m);
    void set_service_registry_for_diagnostics(std::shared_ptr<v2::service::ServiceRegistry> r);
    [[nodiscard]] BattleRouteDiagnostics battle_route_diagnostics() const noexcept;

    // ── Authorizer integration ─────────────────────────────────────────
    // Store the role for a session after successful authentication.
    void set_session_role(SessionId session_id, v2::auth::Role role);
    // Check if a session is authorized for a given message. If no role is
    // stored for the session, the check is skipped (allowed by default).
    [[nodiscard]] bool is_session_allowed(SessionId session_id,
                                          std::uint16_t protocol_message_id) const;

    // ── Match-found handling ──────────────────────────────────────────
    // Called when a match is found (either via MatchFoundCallback in-process
    // or via match_found push from the matchmaking backend).
    // Automatically creates a room, joins matched players, readies them,
    // and starts the battle. Idempotent for the same match_id.
    void on_match_found(const v2::match::MatchResult& result);

private:
    struct PendingSettlementAck {
        int expected_acks = 0;
        int received_acks = 0;
    };

    // Tracks a player waiting in the match queue for auto-polling.
    struct MatchWaitEntry {
        std::string user_id;
        std::string mode;
        std::int64_t mmr;
        SessionId session_id;
        std::uint32_t request_id;
    };

    // ── Idempotency tracking ─────────────────────────────────────────
    // Tracks which match_ids have already been processed for room creation.
    // Ensures that duplicate MatchFound callbacks do not create duplicate rooms.
    std::unordered_set<std::string> processed_match_ids_;

    [[nodiscard]] v2::actor::ActorRef get_or_create_player(const std::string& user_id);
    [[nodiscard]] std::string session_user_id(SessionId session_id) const;
    [[nodiscard]] std::string session_room_id(SessionId session_id) const;
    [[nodiscard]] std::string battle_id_for_room(const std::string& room_id) const;
    [[nodiscard]] const std::unordered_map<SessionId, std::string>& session_users() const;
    [[nodiscard]] std::optional<SessionId> session_id_for_user(const std::string& user_id) const;
    void mark_session_authenticated(SessionId session_id,
                                    const std::string& user_id,
                                    v2::auth::Role role);
    void mark_session_room(SessionId session_id, const std::string& room_id);
    void clear_session_room(SessionId session_id);
    void mark_room_battle(const std::string& room_id, const std::string& battle_id);
    void archive_battle(const v2::battle::BattleSettlementPreparedMsg& settlement);
    void submit_battle_finished_push_to_leaderboard(const nlohmann::json& push,
                                                     const std::string& room_id);
    void submit_battle_settlement_to_leaderboard(
        const std::string& battle_id,
        const std::string& room_id,
        const std::string& reason,
        const std::vector<v2::battle::BattleScore>& scores);
    void process_battle_finished(const v2::battle::BattleFinishedMsg& finished);
    void process_deferred_finished(const std::string& battle_id);

    // ── Auto match-found flow helpers ─────────────────────────────────
    // Sends kMatchFoundPush to all matched players.
    void send_match_found_pushes(const v2::match::MatchResult& result,
                                  const std::string& room_id);

    // Creates a room from a MatchResult and joins all players.
    // Returns the room_id on success, empty string on failure.
    std::string create_room_from_match(const v2::match::MatchResult& result);

    // Sets all matched players ready in the room.
    void ready_all_players(const v2::match::MatchResult& result,
                           const std::string& room_id);

    // Starts a battle for the given room (via bridge or local).
    void start_battle_for_room(const std::string& room_id,
                               const std::string& user_id);

    void emit(std::uint16_t message_id,
              SessionId session_id,
              std::uint32_t request_id,
              std::int32_t error_code,
              std::string body);
    void broadcast_to_room(const std::string& room_id,
                           std::uint16_t message_id,
                           std::string body);
    [[nodiscard]] bool should_emit_battle_frame_push(const std::string& room_id,
                                                     std::uint64_t frame_number);
    [[nodiscard]] bool battle_route_offload_enabled();
    void enqueue_battle_route_task(std::function<void()> task);
    void start_battle_route_workers();
    void stop_battle_route_workers();

    v2::runtime::ActorSystem& actor_system_;
    SessionWriteSink& write_sink_;
    SessionLookup lookup_;
    std::unordered_map<std::string, v2::actor::ActorRef> battles_by_room_id_;
    std::unordered_map<std::string, std::string> room_to_battle_id_;
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
    std::unordered_set<std::string> leaderboard_settlement_keys_;
    std::uint64_t next_battle_id_ = 1;
    std::uint32_t battle_frame_push_every_ = 0;
    std::uint32_t battle_route_worker_count_ = 0;
    std::unordered_map<std::string, std::uint64_t> last_emitted_battle_frame_;
    std::mutex battle_frame_push_mutex_;
    std::mutex battle_route_mutex_;
    std::condition_variable battle_route_cv_;
    std::deque<std::function<void()>> battle_route_tasks_;
    std::vector<std::thread> battle_route_workers_;
    std::atomic<bool> battle_route_stopping_{false};
    std::atomic<std::uint64_t> battle_route_completed_tasks_{0};
    std::atomic<std::uint64_t> battle_route_queued_tasks_{0};
    std::atomic<std::uint64_t> battle_route_total_queue_wait_us_{0};
    std::atomic<std::uint64_t> battle_route_max_queue_wait_us_{0};
    std::atomic<std::uint64_t> battle_route_total_task_execution_us_{0};
    std::atomic<std::uint64_t> battle_route_max_task_execution_us_{0};
    std::atomic<std::uint64_t> battle_route_total_backend_route_us_{0};
    std::atomic<std::uint64_t> battle_route_max_backend_route_us_{0};
    std::atomic<std::uint64_t> battle_route_total_response_dispatch_us_{0};
    std::atomic<std::uint64_t> battle_route_max_response_dispatch_us_{0};
    BattleArchiveSink* archive_sink_ = nullptr;
    std::unique_ptr<GatewayServiceBridge> bridge_;
    SchemaValidator schema_validator_;

    // ── Diagnostics & health check ─────────────────────────────────────
    std::unique_ptr<v2::diagnostics::DiagnosticsManager> diagnostics_;
    std::unique_ptr<v2::diagnostics::HealthCheck> health_check_;

    // ── Session role tracking (Authorizer) ──────────────────────────────
    std::unordered_map<SessionId, v2::auth::Role> session_roles_;
};

}  // namespace v2::gateway
