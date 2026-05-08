#include <array>

#include <fmt/core.h>

#include "net/protocol.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

namespace {

void print_exchange(v2::gateway::SessionAdapter& adapter,
                    v2::gateway::ClientEnvelope envelope,
                    const char* label) {
    const auto writes = adapter.handle_incoming(std::move(envelope));
    fmt::print("{}\n", label);
    for (const auto& write : writes) {
        fmt::print("  session={} msg={} req={} err={} body={}\n",
                   write.envelope.session_id,
                   write.envelope.protocol_message_id,
                   write.envelope.request_id,
                   write.envelope.error_code,
                   write.envelope.body);
    }
}

}  // namespace

int main() {
    v2::runtime::ActorSystem actor_system;
    v2::gateway::SessionAdapter adapter(actor_system);
    v2::gateway::Runtime runtime(actor_system, adapter);
    adapter.bind_gateway(runtime.create_gateway_actor());

    fmt::print("v2 gateway demo bootstrap\n");

    print_exchange(adapter,
                   {.session_id = 100,
                    .protocol_message_id = net::protocol::kLoginRequest,
                    .request_id = 1,
                    .body = "owner|token:owner|Owner"},
                   "owner login");
    print_exchange(adapter,
                   {.session_id = 200,
                    .protocol_message_id = net::protocol::kLoginRequest,
                    .request_id = 2,
                    .body = "member|token:member|Member"},
                   "member login");
    print_exchange(adapter,
                   {.session_id = 100,
                    .protocol_message_id = net::protocol::kRoomCreateRequest,
                    .request_id = 3,
                    .body = "room_alpha"},
                   "create room");
    print_exchange(adapter,
                   {.session_id = 200,
                    .protocol_message_id = net::protocol::kRoomJoinRequest,
                    .request_id = 4,
                    .body = "room_alpha"},
                   "join room");
    print_exchange(adapter,
                   {.session_id = 100,
                    .protocol_message_id = net::protocol::kRoomReadyRequest,
                    .request_id = 5,
                    .body = "true"},
                   "owner ready");
    print_exchange(adapter,
                   {.session_id = 200,
                    .protocol_message_id = net::protocol::kRoomReadyRequest,
                    .request_id = 6,
                    .body = "true"},
                   "member ready");
    print_exchange(adapter,
                   {.session_id = 100,
                    .protocol_message_id = net::protocol::kBattleStartRequest,
                    .request_id = 7,
                    .body = "room_alpha"},
                   "battle start");

    return 0;
}
