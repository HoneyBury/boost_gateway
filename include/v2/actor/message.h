#pragma once

#include <cstdint>
#include <string>
#include <variant>

#include "v2/gateway/message_types.h"
#include "v2/player/message_types.h"

namespace v2::actor {

using ActorId = std::uint64_t;
using TraceId = std::uint64_t;
using RequestId = std::uint32_t;

enum class MessageKind : std::uint16_t {
    kUnknown = 0,
    kUser = 1,
};

using MessagePayload = std::variant<std::monostate,
                                    std::string,
                                    v2::gateway::ClientEnvelope,
                                    v2::player::LoginRequestMsg,
                                    v2::player::BindSessionMsg,
                                    v2::player::RoomAssignedMsg,
                                    v2::player::SessionClosedMsg>;

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
