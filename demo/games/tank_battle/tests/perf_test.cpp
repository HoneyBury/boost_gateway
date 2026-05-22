// Tank Battle Performance Smoke Test
//
// Measures tick throughput for 2/20/100 concurrent instances,
// each running 300 ticks with random inputs.  Outputs JSON metrics.
// Thresholds are intentionally conservative for CI/developer machines.

#include "demo/games/tank_battle/server/tank_simulation/tank_world.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/realtime/instance_plugin.h"

#include <nlohmann/json.hpp>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <random>
#include <string>
#include <vector>

namespace {

// ─── Perf Helper: TankPlugin wrapper ──────────────────────────────────

class PerfTankPlugin : public v2::realtime::InstancePlugin {
public:
    void on_instance_created(v2::realtime::InstanceContext& ctx) override {
        auto* world = new tank::TankWorld();
        std::vector<std::string> user_ids;
        for (const auto& p : ctx.players) {
            user_ids.push_back(p.user_id);
        }
        world->init(user_ids);
        ctx.plugin_state = world;
    }

    void on_player_join(v2::realtime::InstanceContext& /*ctx*/,
                        const v2::realtime::PlayerContext& /*player*/) override {}

    void on_player_leave(v2::realtime::InstanceContext& /*ctx*/,
                         const v2::realtime::PlayerContext& /*player*/) override {}

    v2::realtime::InputResult on_input(v2::realtime::InstanceContext& ctx,
                                        const v2::realtime::InputEnvelope& input) override {
        return {.accepted = true, .ack_seq = 1};
    }

    v2::realtime::TickStats on_tick(v2::realtime::InstanceContext& ctx,
                                     const v2::realtime::FrameContext& frame_ctx) override {
        auto* world = static_cast<tank::TankWorld*>(ctx.plugin_state);
        if (world) {
            // Convert inputs to tank format
            std::vector<tank::PlayerInput> inputs;
            for (const auto& env : frame_ctx.inputs_this_tick) {
                tank::PlayerInput pi;
                pi.user_id = env.user_id;
                pi.seq = env.seq;
                inputs.push_back(std::move(pi));
            }
            world->tick(inputs);
        }
        v2::realtime::TickStats stats;
        stats.frame_number = frame_ctx.frame_number;
        stats.inputs_processed = static_cast<std::uint32_t>(frame_ctx.inputs_this_tick.size());
        return stats;
    }

    v2::realtime::Snapshot build_snapshot(v2::realtime::InstanceContext& ctx,
                                           bool /*is_resume*/) override {
        auto* world = static_cast<tank::TankWorld*>(ctx.plugin_state);
        v2::realtime::Snapshot snap;
        snap.payload_type = "tank.snapshot";
        if (world) {
            snap.payload = world->snapshot().to_json().dump();
        }
        snap.is_full = true;
        return snap;
    }

    std::string build_settlement(v2::realtime::InstanceContext& ctx,
                                  const v2::realtime::SettlementContext& /*sctx*/) override {
        auto* world = static_cast<tank::TankWorld*>(ctx.plugin_state);
        if (world) {
            return world->build_settlement().dump();
        }
        return R"({"error":"no_world"})";
    }

    v2::realtime::Snapshot build_resume_snapshot(v2::realtime::InstanceContext& ctx,
                                                  const v2::realtime::PlayerContext& /*player*/) override {
        return build_snapshot(ctx, true);
    }
};

std::unique_ptr<v2::realtime::InstancePlugin> create_perf_plugin() {
    return std::make_unique<PerfTankPlugin>();
}

// ─── Benchmark runner ─────────────────────────────────────────────────

struct PerfResult {
    std::uint32_t num_instances;
    std::uint32_t ticks_per_instance;
    double total_duration_ms;
    double avg_tick_duration_us;
    double ticks_per_second;
    double avg_inputs_per_tick;
};

PerfResult run_benchmark(std::uint32_t num_instances,
                          std::uint32_t ticks_per_instance) {
    v2::realtime::InstanceRuntime runtime;
    runtime.register_plugin("tank", &create_perf_plugin);

    // Create instances
    for (std::uint32_t i = 0; i < num_instances; ++i) {
        std::string inst_id = "perf_" + std::to_string(i);
        std::string room_id = "room_" + std::to_string(i);
        std::vector<v2::realtime::PlayerContext> players;
        players.push_back({"alice_" + std::to_string(i)});
        players.push_back({"bob_" + std::to_string(i)});
        runtime.create_instance(inst_id, room_id, "tank", players);
    }

    // Pre-generate inputs to avoid RNG overhead skewing timing
    std::mt19937 rng{42};
    std::uniform_int_distribution<int> move_dist(-1, 1);
    std::uniform_int_distribution<int> dir_dist(0, 3);
    const std::array<int, 4> directions{0, 90, 180, 270};

    std::vector<std::vector<v2::realtime::InputEnvelope>> tick_inputs(ticks_per_instance);
    for (std::uint32_t t = 0; t < ticks_per_instance; ++t) {
        for (std::uint32_t i = 0; i < num_instances; ++i) {
            std::string inst_id = "perf_" + std::to_string(i);
            v2::realtime::InputEnvelope input;
            input.instance_id = inst_id;
            input.user_id = "alice_" + std::to_string(i);
            input.seq = t + 1;
            input.payload = R"({"actions":[{"type":"move","dx":)" +
                std::to_string(move_dist(rng)) + R"(,"dy":)" +
                std::to_string(move_dist(rng)) + "}]}";
            tick_inputs[t].push_back(std::move(input));
        }
    }

    // Warmup — one tick on all instances
    auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
    runtime.tick_all(now);
    runtime.tick_all(now + 33);

    // Benchmark ticks
    auto start = std::chrono::steady_clock::now();
    std::uint64_t total_inputs = 0;

    for (std::uint32_t t = 0; t < ticks_per_instance; ++t) {
        auto tick_time = now + static_cast<std::int64_t>(t) * 33;
        for (const auto& input : tick_inputs[t]) {
            runtime.submit_input(input);
        }
        auto results = runtime.tick_all(tick_time);
        for (const auto& rs : results) {
            total_inputs += rs.inputs_processed;
        }
    }

    auto end = std::chrono::steady_clock::now();
    double total_ms = std::chrono::duration<double, std::milli>(end - start).count();

    return PerfResult{
        .num_instances = num_instances,
        .ticks_per_instance = ticks_per_instance,
        .total_duration_ms = total_ms,
        .avg_tick_duration_us = (total_ms * 1000.0) / (num_instances * ticks_per_instance),
        .ticks_per_second = (num_instances * ticks_per_instance) / (total_ms / 1000.0),
        .avg_inputs_per_tick = static_cast<double>(total_inputs) / (num_instances * ticks_per_instance),
    };
}

nlohmann::json result_to_json(const PerfResult& r) {
    return nlohmann::json{
        {"num_instances", r.num_instances},
        {"ticks_per_instance", r.ticks_per_instance},
        {"total_duration_ms", std::round(r.total_duration_ms * 100.0) / 100.0},
        {"avg_tick_duration_us", std::round(r.avg_tick_duration_us * 100.0) / 100.0},
        {"ticks_per_second", std::round(r.ticks_per_second * 100.0) / 100.0},
        {"avg_inputs_per_tick", std::round(r.avg_inputs_per_tick * 100.0) / 100.0},
    };
}

}  // anonymous namespace

int main() {
    nlohmann::json report;
    report["suite"] = "tank_battle_perf_smoke";
    std::vector<nlohmann::json> results;

    // Benchmarks: 2 instances x 300 ticks, 20 x 300, 100 x 100
    struct BenchSpec { std::uint32_t instances; std::uint32_t ticks; };
    BenchSpec specs[] = {{2, 300}, {20, 300}, {100, 100}, {500, 50}};

    for (const auto& spec : specs) {
        auto r = run_benchmark(spec.instances, spec.ticks);
        auto j = result_to_json(r);

        // Conservative thresholds for CI/dev machines (~30 Hz tick rate)
        double min_tps = (spec.instances == 2) ? 200.0 :
                         (spec.instances == 20) ? 500.0 :
                         (spec.instances == 500) ? 100.0 : 200.0;
        j["passed"] = r.ticks_per_second >= min_tps;
        j["min_ticks_per_second"] = min_tps;
        results.push_back(j);
    }

    report["results"] = results;
    report["all_passed"] = std::all_of(results.begin(), results.end(),
        [](const nlohmann::json& j) { return j.value("passed", false); });

    std::cout << report.dump(2) << std::endl;
    return report["all_passed"] ? 0 : 1;
}
