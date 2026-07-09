#include "v2/gateway/gateway_server_bridge.h"

#include "net/protocol.h"

#include <nlohmann/json.hpp>

#include <atomic>
#include <utility>

namespace v2::gateway {

GatewayServerShadowBridge::MirrorPolicy make_shadow_bridge_policy(
    const app::config::GatewayAppConfig& config) noexcept {
    return GatewayServerShadowBridge::MirrorPolicy(
        config.v2_shadow_bridge_login,
        config.v2_shadow_bridge_room,
        config.v2_shadow_bridge_battle,
        config.v2_shadow_bridge_echo);
}

GatewayServerShadowBridge::EmitPolicy make_shadow_bridge_emit_policy(
    const app::config::GatewayAppConfig& config) noexcept {
    return GatewayServerShadowBridge::EmitPolicy(
        config.v2_shadow_bridge_emit_battle_input_push,
        config.v2_shadow_bridge_emit_battle_state_started,
        config.v2_shadow_bridge_emit_battle_state_frame,
        config.v2_shadow_bridge_emit_battle_state_settlement,
        config.v2_shadow_bridge_emit_battle_state_finished);
}

bool GatewayServerShadowBridge::should_forward(std::uint16_t message_id) const noexcept {
    switch (message_id) {
        case net::protocol::kLoginRequest:
            return mirror_policy_.login;
        case net::protocol::kRoomCreateRequest:
        case net::protocol::kRoomJoinRequest:
        case net::protocol::kRoomReadyRequest:
            return mirror_policy_.room;
        case net::protocol::kBattleStartRequest:
        case net::protocol::kBattleInputRequest:
            return mirror_policy_.battle;
        case net::protocol::kEchoRequest:
            return mirror_policy_.echo;
        default:
            return false;
    }
}

bool GatewayServerShadowBridge::should_emit(std::uint16_t message_id, std::string_view body) const noexcept {
    if (message_id == net::protocol::kBattleInputPush) {
        return emit_policy_.battle_input_push;
    }
    if (message_id != net::protocol::kBattleStatePush) {
        return true;
    }

    const auto parsed = parse_battle_wire_body(body);
    if (!parsed || !std::holds_alternative<ParsedBattleStateBody>(*parsed)) {
        return true;
    }

    const auto& state = std::get<ParsedBattleStateBody>(*parsed);
    if (state.kind == "started") {
        return emit_policy_.battle_state_started;
    }
    if (state.kind == "frame") {
        return emit_policy_.battle_state_frame;
    }
    if (state.kind == "settlement") {
        return emit_policy_.battle_state_settlement;
    }
    if (state.kind == "finished") {
        return emit_policy_.battle_state_finished;
    }
    return true;
}

// ─── v2 PacketBridge overrides ───────────────────────────────────────────

void GatewayServerShadowBridge::on_packet(SessionHandle session,
                                          const net::Session::PacketMessage& message) {
    if (!should_forward(message.message_id)) {
        return;
    }

    std::scoped_lock lock(state_mutex_);
    mirrored_packets_.fetch_add(1, std::memory_order_relaxed);

    (void)adapter_.handle_incoming(ClientEnvelope{
        .session_id = static_cast<SessionId>(session),
        .protocol_message_id = message.message_id,
        .request_id = message.request_id,
        .error_code = message.error_code,
        .flags = message.flags,
        .body = message.body,
    });
}

void GatewayServerShadowBridge::on_close(SessionHandle session) {
    runtime_.on_session_closed(static_cast<SessionId>(session));
}

// ─── Write scheduling ────────────────────────────────────────────────────

void GatewayServerShadowBridge::set_write_scheduler(SessionWriteScheduler scheduler) {
    std::scoped_lock lock(scheduler_mutex_);
    write_scheduler_ = std::move(scheduler);
}

GatewayServerShadowBridge::DispatchStats GatewayServerShadowBridge::dispatch_stats() const noexcept {
    return DispatchStats{
        .mirrored_packets = mirrored_packets_.load(std::memory_order_relaxed),
        .emitted_writes = emitted_writes_.load(std::memory_order_relaxed),
        .scheduled_writes = scheduled_writes_.load(std::memory_order_relaxed),
        .inline_writes = inline_writes_.load(std::memory_order_relaxed),
    };
}

GatewayServerShadowBridge::Diagnostics GatewayServerShadowBridge::diagnostics() const noexcept {
    std::scoped_lock lock(state_mutex_);
    std::uint64_t active_sessions = 0;
    for (const auto& [session_id, session] : sessions_by_id_) {
        (void)session_id;
        if (!session.expired()) {
            ++active_sessions;
        }
    }

    return Diagnostics{
        .emit_responses = emit_responses_,
        .mirror_policy = mirror_policy_,
        .emit_policy = emit_policy_,
        .dispatch_stats = dispatch_stats(),
        .tracked_sessions = static_cast<std::uint64_t>(sessions_by_id_.size()),
        .active_sessions = active_sessions,
    };
}

std::string GatewayServerShadowBridge::diagnostics_json() const {
    const auto snapshot = diagnostics();
    nlohmann::json doc;
    doc["emit_responses"] = snapshot.emit_responses;
    doc["mirror_policy"] = {
        {"login", snapshot.mirror_policy.login},
        {"room", snapshot.mirror_policy.room},
        {"battle", snapshot.mirror_policy.battle},
        {"echo", snapshot.mirror_policy.echo},
    };
    doc["emit_policy"] = {
        {"battle_input_push", snapshot.emit_policy.battle_input_push},
        {"battle_state_started", snapshot.emit_policy.battle_state_started},
        {"battle_state_frame", snapshot.emit_policy.battle_state_frame},
        {"battle_state_settlement", snapshot.emit_policy.battle_state_settlement},
        {"battle_state_finished", snapshot.emit_policy.battle_state_finished},
    };
    doc["dispatch_stats"] = {
        {"mirrored_packets", snapshot.dispatch_stats.mirrored_packets},
        {"emitted_writes", snapshot.dispatch_stats.emitted_writes},
        {"scheduled_writes", snapshot.dispatch_stats.scheduled_writes},
        {"inline_writes", snapshot.dispatch_stats.inline_writes},
    };
    doc["tracked_sessions"] = snapshot.tracked_sessions;
    doc["active_sessions"] = snapshot.active_sessions;
    return doc.dump();
}

void GatewayServerShadowBridge::dispatch_write(const std::shared_ptr<net::Session>& session,
                                               SessionWriteTask task) {
    if (!session || !task) {
        return;
    }

    SessionWriteScheduler scheduler;
    {
        std::scoped_lock lock(scheduler_mutex_);
        scheduler = write_scheduler_;
    }

    if (scheduler && scheduler(session, task)) {
        scheduled_writes_.fetch_add(1, std::memory_order_relaxed);
        return;
    }

    inline_writes_.fetch_add(1, std::memory_order_relaxed);
    task();
}

void GatewayServerShadowBridge::deliver(SessionWrite write) {
    if (!emit_responses_) {
        return;
    }
    if (!should_emit(write.envelope.protocol_message_id, write.envelope.body)) {
        return;
    }

    std::scoped_lock lock(state_mutex_);
    auto it = sessions_by_id_.find(write.envelope.session_id);
    if (it == sessions_by_id_.end()) {
        return;
    }

    auto session = it->second.lock();
    if (!session) {
        return;
    }

    emitted_writes_.fetch_add(1, std::memory_order_relaxed);

    dispatch_write(
        session,
        [session, envelope = std::move(write.envelope)]() mutable {
            session->send(envelope.protocol_message_id,
                          envelope.request_id,
                          envelope.error_code,
                          std::move(envelope.body),
                          envelope.flags);
        });
}

SessionId GatewayServerShadowBridge::get_or_create_session_id(const std::shared_ptr<net::Session>& session) {
    auto it = session_ids_by_ptr_.find(session.get());
    if (it != session_ids_by_ptr_.end()) {
        return it->second;
    }

    const auto session_id = next_session_id_++;
    session_ids_by_ptr_.emplace(session.get(), session_id);
    sessions_by_id_.emplace(session_id, session);
    return session_id;
}

}  // namespace v2::gateway
