#pragma once

#include "app/config.h"
#include "v2/gateway/packet_bridge.h"
#include "v2/gateway/battle_wire_parser.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

namespace v2::gateway {

class GatewayServerShadowBridge final : public PacketBridge,
                                        public DownstreamSessionWriteSink {
public:
    using SessionWriteTask = std::function<void()>;
    using SessionWriteScheduler = std::function<bool(const std::shared_ptr<net::Session>&, SessionWriteTask)>;
    struct DispatchStats {
        std::uint64_t mirrored_packets = 0;
        std::uint64_t emitted_writes = 0;
        std::uint64_t scheduled_writes = 0;
        std::uint64_t inline_writes = 0;
    };

    struct MirrorPolicy {
        constexpr MirrorPolicy(bool login_enabled = true,
                               bool room_enabled = true,
                               bool battle_enabled = true,
                               bool echo_enabled = false) noexcept
            : login(login_enabled),
              room(room_enabled),
              battle(battle_enabled),
              echo(echo_enabled) {}

        bool login;
        bool room;
        bool battle;
        bool echo;
    };

    struct EmitPolicy {
        constexpr EmitPolicy(bool battle_input_push_enabled = true,
                             bool battle_state_started_enabled = true,
                             bool battle_state_frame_enabled = true,
                             bool battle_state_settlement_enabled = true,
                             bool battle_state_finished_enabled = true) noexcept
            : battle_input_push(battle_input_push_enabled),
              battle_state_started(battle_state_started_enabled),
              battle_state_frame(battle_state_frame_enabled),
              battle_state_settlement(battle_state_settlement_enabled),
              battle_state_finished(battle_state_finished_enabled) {}

        bool battle_input_push;
        bool battle_state_started;
        bool battle_state_frame;
        bool battle_state_settlement;
        bool battle_state_finished;
    };

    struct Diagnostics {
        bool emit_responses = false;
        MirrorPolicy mirror_policy{};
        EmitPolicy emit_policy{};
        DispatchStats dispatch_stats{};
        std::uint64_t tracked_sessions = 0;
        std::uint64_t active_sessions = 0;
    };

    explicit GatewayServerShadowBridge(MirrorPolicy mirror_policy = {},
                                       EmitPolicy emit_policy = {},
                                       bool emit_responses = false)
        : adapter_(actor_system_, this),
          runtime_(actor_system_, adapter_),
          mirror_policy_(mirror_policy),
          emit_policy_(emit_policy),
          emit_responses_(emit_responses) {
        gateway_actor_ = runtime_.create_gateway_actor();
        adapter_.bind_gateway(gateway_actor_);
    }

    // v2 PacketBridge overrides — called from v2 pipeline
    void on_packet(SessionHandle session,
                   const net::Session::PacketMessage& message) override;
    void on_close(SessionHandle session) override;

    void deliver(SessionWrite write) override;
    void set_write_scheduler(SessionWriteScheduler scheduler);

    [[nodiscard]] bool emit_responses() const noexcept { return emit_responses_; }
    [[nodiscard]] const MirrorPolicy& mirror_policy() const noexcept { return mirror_policy_; }
    [[nodiscard]] const EmitPolicy& emit_policy() const noexcept { return emit_policy_; }
    [[nodiscard]] DispatchStats dispatch_stats() const noexcept;
    [[nodiscard]] Diagnostics diagnostics() const noexcept;
    [[nodiscard]] std::string diagnostics_json() const;
    [[nodiscard]] bool should_forward(std::uint16_t message_id) const noexcept;
    [[nodiscard]] bool should_emit(std::uint16_t message_id, std::string_view body) const noexcept;

private:
    [[nodiscard]] SessionId get_or_create_session_id(const std::shared_ptr<net::Session>& session);
    void dispatch_write(const std::shared_ptr<net::Session>& session, SessionWriteTask task);

    v2::runtime::ActorSystem actor_system_;
    SessionAdapter adapter_;
    Runtime runtime_;
    v2::actor::ActorRef gateway_actor_;
    MirrorPolicy mirror_policy_{};
    EmitPolicy emit_policy_{};
    bool emit_responses_ = false;
    mutable std::mutex scheduler_mutex_;
    mutable std::recursive_mutex state_mutex_;
    SessionWriteScheduler write_scheduler_;
    std::atomic<std::uint64_t> mirrored_packets_{0};
    std::atomic<std::uint64_t> emitted_writes_{0};
    std::atomic<std::uint64_t> scheduled_writes_{0};
    std::atomic<std::uint64_t> inline_writes_{0};
    std::unordered_map<net::Session*, SessionId> session_ids_by_ptr_;
    std::unordered_map<SessionId, std::weak_ptr<net::Session>> sessions_by_id_;
    SessionId next_session_id_ = 1;
};

[[nodiscard]] GatewayServerShadowBridge::MirrorPolicy make_shadow_bridge_policy(
    const app::config::GatewayAppConfig& config) noexcept;
[[nodiscard]] GatewayServerShadowBridge::EmitPolicy make_shadow_bridge_emit_policy(
    const app::config::GatewayAppConfig& config) noexcept;

}  // namespace v2::gateway
