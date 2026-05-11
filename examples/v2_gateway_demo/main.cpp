#include <cstdlib>
#include <atomic>
#include <chrono>
#include <csignal>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <thread>
#include <vector>

#include <fmt/core.h>

#include "app/crash_handler.h"
#include "app/logging.h"
#include "net/protocol.h"
#include "v2/gateway/demo_server.h"
#include "v2/io/io_engine.h"
#include "v2/gateway/runtime.h"
#include "v2/gateway/session_adapter.h"

namespace {

std::atomic<bool> g_keep_running{true};

void handle_signal(int) {
    g_keep_running.store(false, std::memory_order_relaxed);
}

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

std::uint32_t parse_io_cores(int argc, char* argv[]) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--io-cores") {
            const auto parsed = std::atoi(argv[i + 1]);
            return parsed > 0 ? static_cast<std::uint32_t>(parsed) : 1U;
        }
    }
    return 1U;
}

std::optional<std::uint32_t> parse_acceptor_core(int argc, char* argv[]) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--acceptor-core") {
            const auto parsed = std::atoi(argv[i + 1]);
            if (parsed >= 0) {
                return static_cast<std::uint32_t>(parsed);
            }
        }
    }
    return std::nullopt;
}

bool has_flag(int argc, char* argv[], const char* flag) {
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == flag) {
            return true;
        }
    }
    return false;
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

        const auto io_cores = parse_io_cores(argc, argv);
        const auto acceptor_core = parse_acceptor_core(argc, argv);
        auto io_engine = std::make_unique<v2::io::AsioIoEngine>(io_cores);
        v2::gateway::DemoServer server(9201,
                                       {},
                                       v2::gateway::DemoServerOptions{
                                           .acceptor_core_id = acceptor_core,
                                       },
                                       std::move(io_engine));
        server.start();
        std::signal(SIGINT, handle_signal);
        std::signal(SIGTERM, handle_signal);
        const auto print_diagnostics_json = has_flag(argc, argv, "--diagnostics-json");
        fmt::print("v2 gateway demo listening on port {} with {} io cores acceptor_core={}\n",
                   server.local_port(),
                   server.io_core_count(),
                   server.acceptor_core_id().value_or(0));
        for (const auto& snapshot : server.io_core_snapshot()) {
            fmt::print("  io_core={} active_sessions={} accepted_sessions={} outbound_dispatches={}\n",
                       snapshot.core_id,
                       snapshot.active_sessions,
                       snapshot.accepted_sessions,
                       snapshot.outbound_dispatches);
        }
        if (print_diagnostics_json) {
            fmt::print("{}\n", server.diagnostics_json());
        }
        while (g_keep_running.load(std::memory_order_relaxed)) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
        server.stop();
        return 0;
    } catch (const std::exception& ex) {
        fmt::print(stderr, "v2_gateway_demo failed: {}\n", ex.what());
        return EXIT_FAILURE;
    }
}
