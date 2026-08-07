// Tank Battle Demo Server
//
// Production-grade standalone demo server that starts the realtime
// instance runtime, room backend service, and leaderboard backend
// service together in a single process.
//
// Usage:
//   tank_battle_demo [--port=9301] [--room-port=9302] [--leaderboard-port=9305]
//
// Architecture:
//   Port A (--port):         tank_create handler + demo command server
//   Port B (--room-port):    RoomBackendService (room/lobby management)
//   Port C (--leaderboard-port): LeaderboardService (score submission & ranking)
//
// When a battle finishes, the demo server automatically submits each
// player's score to the leaderboard using the idempotency key
// "tank_battle:<battle_id>:<user_id>".

#include "tank_plugin/tank_plugin.h"
#include "v2/leaderboard/leaderboard_service.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/room/room_backend_service.h"
#include "v2/service/backend_connection.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"

#ifdef BOOST_BUILD_GRPC
#include "v2/grpc/grpc_adapter.h"
#endif

#include <nlohmann/json.hpp>
#include "app/audit_log.h"
#include "app/logging.h"

#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>

namespace {

std::atomic<bool> g_running{true};
v2::realtime::InstanceRuntime* g_runtime = nullptr;
v2::service::BackendServer* g_server = nullptr;
v2::room::RoomBackendService* g_room = nullptr;
v2::leaderboard::LeaderboardService* g_leaderboard = nullptr;
std::string g_leaderboard_host = "127.0.0.1";
std::uint16_t g_leaderboard_port = 9305;

#ifdef BOOST_BUILD_GRPC
v2::grpc::GrpcGatewayAdapter* g_grpc_adapter = nullptr;
#endif

// ─── Signal handler ─────────────────────────────────────────────────────

void handle_signal(int) {
    if (!g_running.exchange(false)) {
        return;  // already stopping
    }
    std::cout << "\n=== tank_battle_demo: shutting down... ===" << std::endl;
}

// ─── CLI argument parsing ───────────────────────────────────────────────

struct DemoArgs {
    std::uint16_t port = 9301;
    std::uint16_t room_port = 9302;
    std::uint16_t leaderboard_port = 9305;
    bool grpc_mode = false;
    std::uint16_t grpc_port = 50051;
};

DemoArgs parse_args(int argc, char* argv[]) {
    DemoArgs args;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg.substr(0, 7) == "--port=") {
            args.port = static_cast<std::uint16_t>(
                std::stoi(arg.substr(7)));
        } else if (arg.substr(0, 12) == "--room-port=") {
            args.room_port = static_cast<std::uint16_t>(
                std::stoi(arg.substr(12)));
        } else if (arg.substr(0, 19) == "--leaderboard-port=") {
            args.leaderboard_port = static_cast<std::uint16_t>(
                std::stoi(arg.substr(19)));
        } else if (arg == "--grpc") {
            args.grpc_mode = true;
        } else if (arg.substr(0, 12) == "--grpc-port=") {
            args.grpc_port = static_cast<std::uint16_t>(
                std::stoi(arg.substr(12)));
            args.grpc_mode = true;
        } else if (arg.substr(0, 7) == "--help") {
            std::cout << "Usage: tank_battle_demo [options]\n"
                      << "  --port=PORT              tank command server (default: 9301)\n"
                      << "  --room-port=PORT         room backend service (default: 9302)\n"
                      << "  --leaderboard-port=PORT  leaderboard service (default: 9305)\n"
                      << "  --grpc                   enable gRPC gateway mode\n"
                      << "  --grpc-port=PORT         gRPC server port (default: 50051)\n"
                      << "  --help                   show this help\n";
            std::exit(0);
        }
    }
    return args;
}

// ─── Plugin factory ─────────────────────────────────────────────────────

std::unique_ptr<v2::realtime::InstancePlugin> create_tank_plugin() {
    return std::make_unique<tank::TankPlugin>();
}

// ─── tank_create handler ────────────────────────────────────────────────

v2::service::BackendEnvelope handle_tank_create(
    v2::realtime::InstanceRuntime& runtime,
    const v2::service::BackendEnvelope& request) {

    auto doc = nlohmann::json::parse(request.payload, nullptr, false);
    if (doc.is_discarded() || !doc.contains("room_id") || !doc.contains("player_ids")) {
        v2::service::BackendEnvelope err;
        err.kind = v2::service::MessageKind::kError;
        err.error_code = -1004;
        err.payload = R"({"status":"error","reason":"invalid_json"})";
        return err;
    }

    std::string room_id = doc["room_id"].get<std::string>();
    std::string instance_id = doc.value("instance_id", "tank_" + room_id);
    std::uint32_t max_frames = doc.value("max_frames", 0);
    std::uint32_t tick_interval = doc.value("tick_interval_ms", 33);

    std::vector<v2::realtime::PlayerContext> players;
    for (const auto& pid : doc["player_ids"]) {
        v2::realtime::PlayerContext pc;
        pc.user_id = pid.get<std::string>();
        players.push_back(std::move(pc));
    }

    auto result = runtime.create_instance(
        instance_id, room_id, "tank_battle", players,
        tick_interval, max_frames);

    if (result.empty()) {
        v2::service::BackendEnvelope err;
        err.kind = v2::service::MessageKind::kError;
        err.error_code = -2003;
        err.payload = R"({"status":"error","reason":"create_failed"})";
        return err;
    }

    std::cout << "[tank_battle_demo] instance created: " << result
              << " room=" << room_id
              << " players=" << players.size() << std::endl;

    v2::service::BackendEnvelope resp;
    resp.kind = v2::service::MessageKind::kResponse;
    nlohmann::json body{
        {"status", "ok"},
        {"instance_id", result},
        {"room_id", room_id},
    };
    resp.payload = body.dump();
    return resp;
}

// ─── Leaderboard submit helper ──────────────────────────────────────────
//
// Called when a battle finishes. Submits each player's score to the
// leaderboard with an idempotency key to prevent double-counting.

void submit_battle_to_leaderboard(const std::string& instance_id,
                                   const std::string& room_id,
                                   const std::string& settlement_json) {
    auto settlement = nlohmann::json::parse(settlement_json, nullptr, false);
    if (settlement.is_discarded() || !settlement.contains("players")) {
        std::cerr << "[leaderboard] invalid settlement payload for instance="
                  << instance_id << std::endl;
        return;
    }

    v2::service::BackendConnection conn(v2::service::BackendConnectionOptions{
        .host = g_leaderboard_host,
        .port = g_leaderboard_port,
        .timeout = std::chrono::milliseconds(3000),
        .connect_timeout = std::chrono::milliseconds(1000),
    });

    if (!conn.connect()) {
        std::cerr << "[leaderboard] cannot connect to leaderboard service on "
                  << g_leaderboard_host << ":" << g_leaderboard_port << std::endl;
        return;
    }

    for (const auto& player : settlement["players"]) {
        std::string user_id = player.value("user_id", "");
        if (user_id.empty()) continue;

        std::int64_t score = player.value("score", 0);
        std::string display_name = player.value("display_name", user_id);
        bool win = player.value("win", false);

        // Build idempotency key: tank_battle:<battle_id>:<user_id>
        std::string idempotency_key = "tank_battle:" + instance_id + ":" + user_id;

        nlohmann::json submit_body{
            {"user_id", user_id},
            {"display_name", display_name},
            {"score", score},
            {"idempotency_key", idempotency_key},
            {"metadata", {
                {"battle_id", instance_id},
                {"room_id", room_id},
                {"win", win},
                {"kills", player.value("kills", 0)},
                {"deaths", player.value("deaths", 0)},
                {"damage", player.value("damage", 0)},
            }},
        };

        v2::service::BackendEnvelope req;
        req.target_service = v2::service::ServiceId::kLeaderboard;
        req.kind = v2::service::MessageKind::kRequest;
        req.message_type = "leaderboard_submit";
        req.payload = submit_body.dump();

        auto response = conn.send_request(std::move(req));
        if (response && response->kind == v2::service::MessageKind::kResponse) {
            std::cout << "[leaderboard] submitted score for user=" << user_id
                      << " score=" << score
                      << " battle=" << instance_id << std::endl;
        } else {
            std::cerr << "[leaderboard] submit failed for user=" << user_id
                      << " score=" << score
                      << " error_code=" << (response ? response->error_code : -1) << std::endl;
        }
    }
}

}  // namespace

// ─── Main ───────────────────────────────────────────────────────────────

int main(int argc, char* argv[]) {
    app::logging::init("tank_battle_demo");

    auto args = parse_args(argc, argv);
    g_leaderboard_port = args.leaderboard_port;

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    std::cout << "=== Tank Battle Demo Server ===" << std::endl;
    std::cout << "  Command port:      " << args.port << std::endl;
    std::cout << "  Room service port: " << args.room_port << std::endl;
    std::cout << "  Leaderboard port:  " << args.leaderboard_port << std::endl;
    std::cout << std::endl;

    try {
        // ── 1. Realtime instance runtime ──────────────────────────────
        v2::realtime::InstanceRuntime runtime;
        g_runtime = &runtime;

        runtime.register_plugin("tank_battle", &create_tank_plugin);

        // Set up event callback with settlement → leaderboard bridge
        (void)runtime.set_event_callback([&runtime](const v2::realtime::InstanceEvent& event) {
            switch (event.type) {
                case v2::realtime::InstanceEvent::Type::kInstanceCreated:
                    std::cout << "[runtime] instance created: " << event.instance_id << std::endl;
                    break;

                case v2::realtime::InstanceEvent::Type::kInstanceFinished: {
                    std::cout << "[runtime] instance finished: " << event.instance_id << std::endl;

                    // ── Task 2: Settlement → Leaderboard auto-submit ──
                    if (!event.settlement.result_payload.empty()) {
                        submit_battle_to_leaderboard(
                            event.settlement.instance_id,
                            event.settlement.room_id,
                            event.settlement.result_payload);
                    }
                    break;
                }

                case v2::realtime::InstanceEvent::Type::kPlayerJoined:
                    std::cout << "[runtime] player joined: " << event.user_id
                              << " instance=" << event.instance_id << std::endl;
                    break;

                case v2::realtime::InstanceEvent::Type::kPlayerLeft:
                    std::cout << "[runtime] player left: " << event.user_id
                              << " instance=" << event.instance_id << std::endl;
                    break;

                default:
                    break;
            }
        });

        // ── 2. Room backend service ──────────────────────────────────
        v2::room::RoomBackendService room_service(args.room_port, 0, 300000, 60000);
        g_room = &room_service;
        room_service.start();
        std::cout << "[room] listening on port " << room_service.local_port() << std::endl;

        // ── 3. Leaderboard backend service ───────────────────────────
        v2::leaderboard::LeaderboardService leaderboard_service(args.leaderboard_port);
        g_leaderboard = &leaderboard_service;
        leaderboard_service.start();
        std::cout << "[leaderboard] listening on port "
                  << leaderboard_service.local_port() << std::endl;

        // ── 4. Tank command server ───────────────────────────────────
        v2::service::BackendServer::HandlerMap handlers;
        handlers["tank_create"] = [&runtime](const auto& req) {
            return handle_tank_create(runtime, req);
        };
        handlers["ping"] = [](const auto&) {
            v2::service::BackendEnvelope resp;
            resp.kind = v2::service::MessageKind::kResponse;
            resp.payload = R"({"status":"ok","pong":true})";
            return resp;
        };

        v2::service::BackendServer server(
            v2::service::BackendServerOptions{.port = args.port}, std::move(handlers));
        g_server = &server;
        server.start();

#ifdef BOOST_BUILD_GRPC
        // ── 4b. gRPC Gateway (optional, when --grpc flag is set) ──────
        v2::grpc::GrpcGatewayAdapter grpc_adapter(args.grpc_port);
        if (args.grpc_mode) {
            if (grpc_adapter.start()) {
                g_grpc_adapter = &grpc_adapter;
                std::cout << "[grpc] gateway listening on port "
                          << grpc_adapter.port() << std::endl;
            } else {
                std::cerr << "[grpc] failed to start gRPC gateway on port "
                          << args.grpc_port << std::endl;
            }
        }
#endif

        std::cout << std::endl;
        std::cout << "=== All services started ===" << std::endl;
        std::cout << "  tank_battle_demo: command server on port "
                  << server.local_port() << std::endl;
        std::cout << "  tank_battle_demo: room service on port "
                  << room_service.local_port() << std::endl;
        std::cout << "  tank_battle_demo: leaderboard on port "
                  << leaderboard_service.local_port() << std::endl;
#ifdef BOOST_BUILD_GRPC
        if (args.grpc_mode) {
            std::cout << "  tank_battle_demo: gRPC gateway on port "
                      << grpc_adapter.port() << std::endl;
        }
#endif
        std::cout << "=== Running (Ctrl+C to stop) ===" << std::endl;

        // ── 5. Main tick loop ────────────────────────────────────────
        constexpr auto tick_interval = std::chrono::milliseconds(33);
        auto next_tick = std::chrono::steady_clock::now();

        while (g_running) {
            auto now = std::chrono::steady_clock::now();
            if (now >= next_tick) {
                auto tick_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                    std::chrono::system_clock::now().time_since_epoch()).count();
                runtime.tick_all(tick_ms);
                next_tick = now + tick_interval;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }

        // ── 6. Graceful shutdown ─────────────────────────────────────
        std::cout << "\n=== Shutting down services... ===" << std::endl;

        server.stop();
        std::cout << "[command server] stopped" << std::endl;

        leaderboard_service.stop();
        std::cout << "[leaderboard] stopped" << std::endl;

        room_service.stop();
        std::cout << "[room service] stopped" << std::endl;

        g_runtime = nullptr;
        g_server = nullptr;
        g_room = nullptr;
        g_leaderboard = nullptr;

#ifdef BOOST_BUILD_GRPC
        if (g_grpc_adapter) {
            g_grpc_adapter->stop();
            g_grpc_adapter = nullptr;
            std::cout << "[grpc] gateway stopped" << std::endl;
        }
#endif

        std::cout << "=== tank_battle_demo: stopped ===" << std::endl;

    } catch (const std::exception& ex) {
        std::cerr << "tank_battle_demo: fatal error: " << ex.what() << std::endl;
        return 1;
    }

    return 0;
}
