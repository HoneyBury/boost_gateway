// v2 Inter-Actor Message Catalog — Frozen 2026-05-09
// =====================================================
//
// This file defines the complete typed message interface for v2 M1 actor runtime.
// All inter-actor communication uses MessagePayload (std::variant of typed structs).
// String-based parsing exists ONLY at gateway boundaries (wire protocol → typed structs).
//
// Message Flow Graph:
//
//   Client ──ClientEnvelope──▶ GatewayActor
//                                  │
//                    GatewayCommandSink::handle()
//                                  │
//                                  ▼
//   Runtime ─────────────────────────────────────────────────────────────
//     │                                                                 │
//     ├─▶ PlayerActor:  BindSessionMsg, LoginRequestMsg,                │
//     │                 RoomAssignedMsg, BattleAssignedMsg,              │
//     │                 BattleSettlementMsg, BattleEndedMsg,             │
//     │                 SessionClosedMsg                                │
//     │                                                                 │
//     ├─▶ RoomActor:    CreateRoomMsg, JoinRoomMsg, SetReadyMsg,        │
//     │                 StartBattleMsg, BattleStartedMsg,               │
//     │                 BattleSettlementMsg, BattleEndedMsg             │
//     │                                                                 │
//     └─▶ BattleActor:  CreateBattleMsg, SubmitBattleInputMsg,          │
//                       TickBattleMsg, EndBattleMsg,                    │
//                       PlayerDisconnectedMsg                           │
//                                                                       │
//   PlayerActor ──PlayerEvent──▶ Runtime                                │
//     LoginAcceptedMsg, SessionKickPushMsg, SessionResumePushMsg,       │
//     BattleSettlementAppliedMsg                                        │
//                                                                       │
//   RoomActor ──RoomEvent──▶ Runtime                                    │
//     BattleStartRequestedMsg, BattleStartRejectedMsg,                  │
//     BattleSettlementAppliedMsg                                        │
//                                                                       │
//   BattleActor ──BattleEvent──▶ Runtime                                │
//     BattleCreatedMsg, BattleInputAcceptedMsg, BattleFrameAdvancedMsg, │
//     BattleSettlementPreparedMsg, BattleFinishedMsg                    │
//
// Every MessagePayload alternative has a handler in at least one actor:
//   GatewayActor:     ClientEnvelope
//   PlayerActor:      BindSessionMsg, LoginRequestMsg, RoomAssignedMsg,
//                     BattleAssignedMsg, BattleSettlementMsg,
//                     BattleEndedMsg, SessionClosedMsg
//   RoomActor:        CreateRoomMsg, JoinRoomMsg, SetReadyMsg,
//                     StartBattleMsg, BattleStartedMsg,
//                     BattleSettlementMsg, BattleEndedMsg
//   BattleActor:      CreateBattleMsg, SubmitBattleInputMsg,
//                     TickBattleMsg, EndBattleMsg, PlayerDisconnectedMsg
//
// std::string is a bootstrap/test payload only — not for production routing.

#pragma once

#include <cstdint>
#include <string>
#include <variant>

#include "v2/gateway/message_types.h"
#include "v2/battle/message_types.h"
#include "v2/player/message_types.h"
#include "v2/room/message_types.h"

namespace v2::actor {

using ActorId = std::uint64_t;
using TraceId = std::uint64_t;
using RequestId = std::uint32_t;

enum class MessageKind : std::uint16_t {
    kUnknown = 0,
    kUser = 1,
};

using MessagePayload = std::variant<std::monostate,
                                    std::string,  // bootstrap/test payload only — see catalog above
                                    v2::gateway::ClientEnvelope,
                                    v2::player::LoginRequestMsg,
                                    v2::player::BindSessionMsg,
                                    v2::player::RoomAssignedMsg,
                                    v2::player::BattleAssignedMsg,
                                    v2::player::BattleSettlementMsg,
                                    v2::player::BattleEndedMsg,
                                    v2::player::SessionClosedMsg,
                                    v2::room::CreateRoomMsg,
                                    v2::room::JoinRoomMsg,
                                    v2::room::SetReadyMsg,
                                    v2::room::StartBattleMsg,
                                    v2::room::BattleStartedMsg,
                                    v2::room::BattleSettlementMsg,
                                    v2::room::BattleEndedMsg,
                                    v2::battle::CreateBattleMsg,
                                    v2::battle::SubmitBattleInputMsg,
                                    v2::battle::TickBattleMsg,
                                    v2::battle::EndBattleMsg,
                                    v2::battle::PlayerDisconnectedMsg>;

struct MessageHeader {
    MessageKind kind = MessageKind::kUnknown;
    TraceId trace_id = 0;
    RequestId request_id = 0;
    ActorId source_actor = 0;
    ActorId target_actor = 0;
    std::uint64_t created_at = 0;
};

struct Message {
    MessageHeader header;
    MessagePayload payload;
};

}  // namespace v2::actor
