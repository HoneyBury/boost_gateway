// Tank Battle Demo Server
//
// Standalone demo server that starts the realtime instance runtime
// with the tank plugin. For testing and verification purposes.
//
// Usage: tank_battle_demo [port]

#include "tank_plugin/tank_plugin.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/backend_server.h"
#include "v2/service/envelope_adapter.h"

#include <nlohmann/json.hpp>
#include "app/audit_log.h"
#include "app/logging.h"

#include <atomic>
#include <chrono>
#include <csignal>
#include <iostream>
#include <memory>
#include <thread>

namespace {

std::atomic<bool> g_running{true};

void handle_signal(int) {
    g_running = false;
    std::cout << "\ntank_battle_demo: shutting down..." << std::endl;
}

std::unique_ptr<v2::realtime::InstancePlugin> create_tank_plugin() {
    return std::make_unique<tank::TankPlugin>();
}

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

}  // namespace

int main(int argc, char* argv[]) {
    app::logging::init("tank_battle_demo");

    std::uint16_t port = 9301;
    if (argc > 1) {
        port = static_cast<std::uint16_t>(std::stoi(argv[1]));
    }

    std::signal(SIGINT, handle_signal);
    std::signal(SIGTERM, handle_signal);

    try {
        // Set up the realtime instance runtime
        v2::realtime::InstanceRuntime runtime;

        runtime.register_plugin("tank_battle", &create_tank_plugin);

        // Set up event callback
        runtime.set_event_callback([](const v2::realtime::InstanceEvent& event) {
            switch (event.type) {
                case v2::realtime::InstanceEvent::Type::kInstanceCreated:
                    std::cout << "[tank] instance created: " << event.instance_id << std::endl;
                    break;
                case v2::realtime::InstanceEvent::Type::kInstanceFinished:
                    std::cout << "[tank] instance finished: " << event.instance_id << std::endl;
                    break;
                default:
                    break;
            }
        });

        // Create a handler to accept instance lifecycle commands
        v2::service::BackendServer::HandlerMap handlers;
        handlers["tank_create"] = [&runtime](const auto& req) {
            return handle_tank_create(runtime, req);
        };

        // Create backend server for demo commands
        v2::service::BackendServer server(
            v2::service::BackendServerOptions{.port = port}, std::move(handlers));
        server.start();

        std::cout << "tank_battle_demo: listening on port " << server.local_port() << std::endl;
        std::cout << "tank_battle_demo: running (Ctrl+C to stop)" << std::endl;

        // Tick loop
        constexpr auto tick_interval = std::chrono::milliseconds(33);  // ~30 Hz
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

        server.stop();
        std::cout << "tank_battle_demo: stopped" << std::endl;
    } catch (const std::exception& ex) {
        std::cerr << "tank_battle_demo: error: " << ex.what() << std::endl;
        return 1;
    }

    return 0;
}
