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
#include "net/http_manager.h"
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

std::uint16_t parse_http_port(int argc, char* argv[]) {
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--http-port") {
            const auto parsed = std::atoi(argv[i + 1]);
            return parsed >= 0 ? static_cast<std::uint16_t>(parsed) : 0U;
        }
    }
    return 0;
}

bool has_flag(int argc, char* argv[], const char* flag) {
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == flag) {
            return true;
        }
    }
    return false;
}

std::string render_demo_server_diagnostics_text(const v2::gateway::DemoServerDiagnostics& diagnostics) {
    std::string text;
    text += "v2_demo_diagnostics\n";
    text += fmt::format("local_port={}\n", diagnostics.local_port);
    text += fmt::format("io_core_count={}\n", diagnostics.io_core_count);
    text += fmt::format("acceptor_core_id={}\n", diagnostics.acceptor_core_id.value_or(0));
    text += fmt::format("total_active_sessions={}\n", diagnostics.total_active_sessions);
    text += fmt::format("total_accepted_sessions={}\n", diagnostics.total_accepted_sessions);
    text += fmt::format("total_outbound_dispatches={}\n", diagnostics.total_outbound_dispatches);
    for (const auto& core : diagnostics.io_cores) {
        text += fmt::format(
            "io_core id={} active_sessions={} accepted_sessions={} outbound_dispatches={}\n",
            core.core_id,
            core.active_sessions,
            core.accepted_sessions,
            core.outbound_dispatches);
    }
    return text;
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
        const auto http_port = parse_http_port(argc, argv);
        auto io_engine = std::make_unique<v2::io::AsioIoEngine>(io_cores);
        v2::gateway::DemoServer server(9201,
                                       {},
                                       v2::gateway::DemoServerOptions{
                                           .acceptor_core_id = acceptor_core,
                                       },
                                       std::move(io_engine));
        boost::asio::io_context management_io;
        std::unique_ptr<net::HttpManager> http_manager;
        std::thread management_thread;
        if (http_port != 0) {
            http_manager = std::make_unique<net::HttpManager>(management_io.get_executor(), http_port);
            http_manager->set_metrics_provider([&server]() {
                const auto diagnostics = server.diagnostics();
                const auto diagnostics_text = render_demo_server_diagnostics_text(diagnostics);
                const auto diagnostics_json = server.diagnostics_json();
                return net::HttpMetricsSnapshot{
                    .prometheus_text = diagnostics_text,
                    .json_text = diagnostics_json,
                    .diagnostics_text = diagnostics_text,
                    .diagnostics_json_text = diagnostics_json,
                };
            });
        }
        server.start();
        if (http_manager) {
            http_manager->start();
            management_thread = std::thread([&management_io]() { management_io.run(); });
        }
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
        if (http_manager) {
            fmt::print("v2 gateway diagnostics HTTP listening on port {}\n", http_manager->local_port());
            fmt::print("  GET /health\n");
            fmt::print("  GET /metrics\n");
            fmt::print("  GET /metrics/json\n");
            fmt::print("  GET /metrics/diagnostics\n");
            fmt::print("  GET /metrics/diagnostics/json\n");
        }
        if (print_diagnostics_json) {
            fmt::print("{}\n", server.diagnostics_json());
        }
        while (g_keep_running.load(std::memory_order_relaxed)) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
        }
        if (http_manager) {
            http_manager->stop();
        }
        server.stop();
        management_io.stop();
        if (management_thread.joinable()) {
            management_thread.join();
        }
        return 0;
    } catch (const std::exception& ex) {
        fmt::print(stderr, "v2_gateway_demo failed: {}\n", ex.what());
        return EXIT_FAILURE;
    }
}
