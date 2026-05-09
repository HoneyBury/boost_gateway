#include <cstdlib>
#include <filesystem>
#include <string>
#include <thread>
#include <vector>

#include <boost/asio.hpp>
#include <fmt/core.h>

#include "app/crash_handler.h"
#include "app/graceful_shutdown.h"
#include "app/logging.h"
#include "net/protocol.h"
#include "v2/gateway/demo_server.h"
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

bool is_script_mode(int argc, char* argv[]) {
    return argc > 1 && std::string(argv[1]) == "--script";
}

}  // namespace

int main(int argc, char* argv[]) {
    app::logging::init("v2_gateway_demo");
    app::crash::install_crash_handler();

    try {
        // Local scripted run remains useful for quick smoke checks without sockets.
        if (is_script_mode(argc, argv)) {
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
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kBattleInputRequest,
                            .request_id = 8,
                            .body = "move:1,2"},
                           "owner battle input");
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kBattleInputRequest,
                            .request_id = 9,
                            .body = "move:2,2"},
                           "owner battle input 2");
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kBattleInputRequest,
                            .request_id = 10,
                            .body = "move:3,2"},
                           "owner battle input 3");
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kBattleStartRequest,
                            .request_id = 11,
                            .body = "room_alpha"},
                           "battle restart after finish");
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kRoomReadyRequest,
                            .request_id = 12,
                            .body = "true"},
                           "owner ready again");
            print_exchange(adapter,
                           {.session_id = 200,
                            .protocol_message_id = net::protocol::kRoomReadyRequest,
                            .request_id = 13,
                            .body = "true"},
                           "member ready again");
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kBattleStartRequest,
                            .request_id = 14,
                            .body = "room_alpha"},
                           "battle restart");
            print_exchange(adapter,
                           {.session_id = 100,
                            .protocol_message_id = net::protocol::kBattleInputRequest,
                            .request_id = 15,
                            .body = "finish:surrender"},
                           "owner finish battle");
            return 0;
        }

        boost::asio::io_context io_context;
        v2::gateway::DemoServer server(io_context, 9201);
        server.start();
        app::GracefulShutdown shutdown(io_context.get_executor(), [&]() {
            server.stop();
            io_context.stop();
        });
        shutdown.start();

        fmt::print("v2 gateway demo listening on port {}\n", server.local_port());
        io_context.run();
        return 0;
    } catch (const std::exception& ex) {
        fmt::print(stderr, "v2_gateway_demo failed: {}\n", ex.what());
        return EXIT_FAILURE;
    }
}
