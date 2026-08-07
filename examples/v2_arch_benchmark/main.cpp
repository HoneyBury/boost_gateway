#include "v2/actor/actor.h"
#include "v2/battle/runtime_world.h"
#include "v2/io/io_engine.h"
#include "v2/io/mailbox.h"
#include "v2/memory/arena.h"
#include "v2/memory/object_pool.h"
#include "v2/realtime/instance_runtime.h"
#include "v2/runtime/actor_system.h"
#include "v2/service/backend_envelope.h"
#include "v2/service/envelope_adapter.h"
#include "v3/proto/envelope_codec.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <functional>
#include <iostream>
#include <memory>
#include <optional>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

namespace {

using Clock = std::chrono::steady_clock;

std::uint64_t now_ns() {
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());
}

struct Options {
    std::size_t iterations = 10000;
    std::size_t actors = 10000;
    std::size_t actor_limit = 100000;
    std::size_t battles = 500;
    std::string output_path;
};

struct LatencyStats {
    std::size_t samples = 0;
    double min_us = 0.0;
    double p50_us = 0.0;
    double p90_us = 0.0;
    double p99_us = 0.0;
    double max_us = 0.0;
    double throughput_ops_per_sec = 0.0;
};

class BenchmarkIoEngine final : public v2::io::IoEngine {
public:
    [[nodiscard]] std::uint32_t num_io_cores() const noexcept override { return 2; }
    void dispatch_to_core(std::uint32_t, std::function<void()>) override {}
    void dispatch_to_all_cores(std::function<void(std::uint32_t)>) override {}
    [[nodiscard]] std::optional<std::uint32_t> current_core_id() const noexcept override {
        return current_core_id_;
    }
    std::unique_ptr<v2::io::IoAcceptor> listen(const char*,
                                               std::uint16_t,
                                               net::SessionOptions,
                                               v2::io::IoListenOptions) override {
        return {};
    }
    void run() override {}
    void stop() override {}
    void register_session(std::uint32_t) override {}
    void unregister_session(std::uint32_t) override {}
    [[nodiscard]] std::uint32_t session_count(std::uint32_t) const noexcept override { return 0; }
    [[nodiscard]] std::uint32_t total_session_count() const noexcept override { return 0; }
    bool post_mailbox(std::uint32_t core_id, v2::actor::Message message) override {
        if (core_id >= mailboxes_.size()) {
            return false;
        }
        mailboxes_[core_id].push_back(std::move(message));
        return true;
    }
    [[nodiscard]] std::vector<v2::actor::Message> drain_mailbox(std::uint32_t core_id) override {
        if (core_id >= mailboxes_.size()) {
            return {};
        }
        auto drained = std::move(mailboxes_[core_id]);
        mailboxes_[core_id].clear();
        return drained;
    }
    void set_actor_system(v2::runtime::ActorSystem*) override {}

    std::optional<std::uint32_t> current_core_id_;

private:
    std::vector<std::vector<v2::actor::Message>> mailboxes_{2};
};

class LatencyActor final : public v2::actor::Actor {
public:
    explicit LatencyActor(std::vector<double>& samples)
        : samples_(samples) {}

    void on_message(v2::actor::Message&& message) override {
        const auto elapsed_ns = now_ns() - message.header.created_at;
        samples_.push_back(static_cast<double>(elapsed_ns) / 1000.0);
    }

private:
    std::vector<double>& samples_;
};

class NoopActor final : public v2::actor::Actor {
public:
    void on_message(v2::actor::Message&&) override {}
};

class CountingActor final : public v2::actor::Actor {
public:
    explicit CountingActor(std::size_t& count)
        : count_(count) {}

    void on_message(v2::actor::Message&&) override { ++count_; }

private:
    std::size_t& count_;
};

class BenchmarkRealtimePlugin final : public v2::realtime::InstancePlugin {
public:
    void on_instance_created(v2::realtime::InstanceContext&) override {}
    void on_player_join(v2::realtime::InstanceContext&,
                        const v2::realtime::PlayerContext&) override {}
    void on_player_leave(v2::realtime::InstanceContext&,
                         const v2::realtime::PlayerContext&) override {}

    v2::realtime::InputResult on_input(
        v2::realtime::InstanceContext&,
        const v2::realtime::InputEnvelope& input) override {
        return {.accepted = true, .ack_seq = input.seq};
    }

    v2::realtime::TickStats on_tick(
        v2::realtime::InstanceContext&,
        const v2::realtime::FrameContext& frame_ctx) noexcept override {
        return {
            .frame_number = frame_ctx.frame_number,
            .inputs_processed = static_cast<std::uint32_t>(frame_ctx.inputs_this_tick.size()),
        };
    }

    v2::realtime::Snapshot build_snapshot(
        v2::realtime::InstanceContext&, bool) noexcept override {
        return {};
    }

    std::string build_settlement(
        v2::realtime::InstanceContext&,
        const v2::realtime::SettlementContext&) noexcept override {
        return {};
    }

    v2::realtime::Snapshot build_resume_snapshot(
        v2::realtime::InstanceContext&,
        const v2::realtime::PlayerContext&) noexcept override {
        return {};
    }
};

std::unique_ptr<v2::realtime::InstancePlugin> create_benchmark_realtime_plugin() {
    return std::make_unique<BenchmarkRealtimePlugin>();
}

struct PoolItem {
    std::uint64_t a = 0;
    std::uint64_t b = 0;
};

LatencyStats make_stats(std::vector<double> samples, double elapsed_seconds, std::size_t operations) {
    LatencyStats stats;
    stats.samples = samples.size();
    stats.throughput_ops_per_sec = elapsed_seconds > 0.0
        ? static_cast<double>(operations) / elapsed_seconds
        : 0.0;
    if (samples.empty()) {
        return stats;
    }

    std::sort(samples.begin(), samples.end());
    const auto pick = [&samples](double percentile) {
        const auto last_index = static_cast<double>(samples.size() - 1);
        const auto index = static_cast<std::size_t>(std::min(last_index, last_index * percentile));
        return samples[index];
    };
    stats.min_us = samples.front();
    stats.p50_us = pick(0.50);
    stats.p90_us = pick(0.90);
    stats.p99_us = pick(0.99);
    stats.max_us = samples.back();
    return stats;
}

v2::actor::Message make_message(v2::actor::ActorId target) {
    v2::actor::Message message;
    message.header.kind = v2::actor::MessageKind::kUser;
    message.header.target_actor = target;
    message.header.created_at = now_ns();
    message.payload = std::string("bench");
    return message;
}

LatencyStats run_local_tell_latency(std::size_t iterations) {
    v2::runtime::ActorSystem system;
    std::vector<double> samples;
    samples.reserve(iterations);
    auto actor = system.create_actor(std::make_unique<LatencyActor>(samples));

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        actor.tell(make_message(actor.actor_id()));
        (void)system.dispatch_all();
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, iterations);
}

LatencyStats run_cross_core_tell_latency(std::size_t iterations) {
    v2::runtime::ActorSystem system;
    BenchmarkIoEngine io_engine;
    io_engine.current_core_id_ = 0U;
    system.set_io_engine(&io_engine);

    std::vector<double> samples;
    samples.reserve(iterations);
    auto actor = system.create_actor(std::make_unique<LatencyActor>(samples), {}, 1U);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        actor.tell(make_message(actor.actor_id()));
        (void)system.drain_mailbox_and_dispatch(1U);
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, iterations);
}

LatencyStats run_actor_create_latency(std::size_t actors) {
    v2::runtime::ActorSystem system;
    std::vector<double> samples;
    samples.reserve(actors);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < actors; ++i) {
        const auto op_begin = now_ns();
        (void)system.create_actor(std::make_unique<NoopActor>());
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, actors);
}

LatencyStats run_actor_fan_in_throughput(std::size_t iterations) {
    v2::runtime::ActorSystem system;
    std::size_t received = 0;
    auto actor = system.create_actor(std::make_unique<CountingActor>(received));
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        actor.tell(make_message(actor.actor_id()));
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
    }
    (void)system.dispatch_all();
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, received);
}

LatencyStats run_actor_limit_smoke(std::size_t actor_count) {
    v2::runtime::ActorSystem system;
    std::vector<double> samples;
    samples.reserve(actor_count);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < actor_count; ++i) {
        const auto op_begin = now_ns();
        auto actor = system.create_actor(std::make_unique<NoopActor>());
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
        if (!actor.is_valid()) {
            break;
        }
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    const auto operations = samples.size();
    return make_stats(std::move(samples), elapsed, operations);
}

LatencyStats run_shutdown_latency(std::size_t actors) {
    v2::runtime::ActorSystem system;
    for (std::size_t i = 0; i < actors; ++i) {
        (void)system.create_actor(std::make_unique<NoopActor>());
    }

    const auto begin = Clock::now();
    system.shutdown();
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();

    std::vector<double> samples;
    samples.push_back((elapsed * 1000000.0) / static_cast<double>(actors == 0 ? 1 : actors));
    return make_stats(std::move(samples), elapsed, actors);
}

LatencyStats run_bump_arena_alloc_latency(std::size_t iterations) {
    v2::memory::BumpArena arena(iterations * 64 + 4096);
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        auto* ptr = arena.alloc(32);
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
        if (ptr == nullptr) {
            break;
        }
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, iterations);
}

LatencyStats run_object_pool_cycle_latency(std::size_t iterations) {
    v2::memory::ObjectPool<PoolItem, 1024> pool;
    pool.prefill(iterations);
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        auto* item = pool.acquire();
        if (item != nullptr) {
            item->a = i;
            pool.release(item);
        }
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, iterations);
}

LatencyStats run_spsc_queue_roundtrip_latency(std::size_t iterations) {
    v2::io::SpscQueue<std::uint64_t> queue(iterations + 1);
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        const auto enqueued = queue.try_enqueue(static_cast<std::uint64_t>(i));
        auto dequeued = queue.try_dequeue();
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
        if (!enqueued || !dequeued.has_value()) {
            break;
        }
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, iterations);
}

LatencyStats run_battle_world_tick_latency(std::size_t iterations) {
    std::vector<std::string> player_ids;
    player_ids.reserve(100);
    for (std::size_t i = 0; i < 100; ++i) {
        player_ids.push_back("player_" + std::to_string(i));
    }

    auto world = v2::battle::create_battle_world("bench_battle", "bench_room", player_ids, 0);
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto frame = static_cast<std::uint32_t>(i + 1);
        if ((i % 10) == 0) {
            (void)v2::battle::battle_world_process_input(
                *world,
                player_ids[i % player_ids.size()],
                "move:1,1",
                1,
                frame);
        }

        const auto op_begin = now_ns();
        (void)v2::battle::battle_world_advance_frame(*world, frame, "bench_tick");
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, iterations);
}

LatencyStats run_multi_battle_tick_latency(std::size_t battle_count) {
    std::vector<std::string> player_ids;
    player_ids.reserve(100);
    for (std::size_t i = 0; i < 100; ++i) {
        player_ids.push_back("player_" + std::to_string(i));
    }

    std::vector<std::unique_ptr<v2::ecs::World>> worlds;
    worlds.reserve(battle_count);
    for (std::size_t i = 0; i < battle_count; ++i) {
        worlds.push_back(v2::battle::create_battle_world(
            "bench_battle_" + std::to_string(i),
            "bench_room_" + std::to_string(i),
            player_ids,
            0));
    }

    std::vector<double> samples;
    samples.reserve(worlds.size());
    const auto begin = Clock::now();
    for (std::size_t i = 0; i < worlds.size(); ++i) {
        const auto op_begin = now_ns();
        (void)v2::battle::battle_world_advance_frame(*worlds[i], 1U, "multi_battle_tick");
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    return make_stats(std::move(samples), elapsed, worlds.size());
}

LatencyStats run_instance_runtime_tick_all_latency(
    std::size_t instance_count, std::size_t target_ticks) {
    instance_count = std::max<std::size_t>(instance_count, 1);
    const auto rounds = std::max<std::size_t>(10, target_ticks / instance_count);

    v2::realtime::RuntimeConfig config;
    config.max_instances = static_cast<std::uint32_t>(instance_count);
    config.enable_tick_timer = false;
    v2::realtime::InstanceRuntime runtime(config);
    runtime.register_plugin("benchmark", &create_benchmark_realtime_plugin);
    std::size_t snapshot_events = 0;
    (void)runtime.set_event_callback([&snapshot_events](const v2::realtime::InstanceEvent& event) {
        if (event.type == v2::realtime::InstanceEvent::Type::kSnapshotAvailable) {
            ++snapshot_events;
        }
    });

    v2::realtime::PlayerContext player;
    player.user_id = "benchmark_player";
    std::vector<std::string> instance_ids;
    instance_ids.reserve(instance_count);
    for (std::size_t i = 0; i < instance_count; ++i) {
        auto id = "benchmark_instance_" + std::to_string(i);
        if (runtime.create_instance(id, "benchmark_room", "benchmark", {player}).empty()) {
            throw std::runtime_error("failed to create realtime benchmark instance");
        }
        instance_ids.push_back(std::move(id));
    }

    std::vector<double> samples;
    samples.reserve(rounds);
    double elapsed_seconds = 0.0;
    std::size_t operations = 0;
    for (std::size_t round = 0; round < rounds; ++round) {
        for (const auto& id : instance_ids) {
            v2::realtime::InputEnvelope input;
            input.instance_id = id;
            input.user_id = player.user_id;
            input.seq = round + 1;
            input.payload_type = "benchmark.input";
            input.payload = "move";
            if (!runtime.submit_input(input).accepted) {
                throw std::runtime_error("failed to submit realtime benchmark input");
            }
        }

        const auto begin = Clock::now();
        const auto results = runtime.tick_all(static_cast<std::int64_t>(round + 1));
        const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
        if (results.size() != instance_count ||
            !std::all_of(results.begin(), results.end(), [](const auto& result) {
                return result.inputs_processed == 1;
            })) {
            throw std::runtime_error("realtime benchmark tick count mismatch");
        }
        elapsed_seconds += elapsed;
        operations += results.size();
        samples.push_back(
            elapsed * 1000000.0 / static_cast<double>(results.size()));
    }
    if (snapshot_events != operations) {
        throw std::runtime_error("realtime benchmark snapshot count mismatch");
    }
    return make_stats(std::move(samples), elapsed_seconds, operations);
}

v2::service::BackendEnvelope make_contract_backend_envelope() {
    return v2::service::BackendEnvelope{
        .correlation_id = 9001,
        .source_service = v2::service::ServiceId::kGateway,
        .target_service = v2::service::ServiceId::kMatchmaking,
        .kind = v2::service::MessageKind::kRequest,
        .timeout_ms = 250,
        .error_code = 0,
        .payload = R"({"user_id":"bench_user","mmr":1350,"mode":"1v1"})",
        .message_type = "match_join",
        .trace_id = 9002,
        .span_id = 9003,
    };
}

v3::proto::TypedEnvelope make_contract_typed_envelope() {
    return v3::proto::TypedEnvelope{
        .meta = {
            .correlation_id = 9001,
            .source_service = "gateway",
            .target_service = "match",
            .timeout_ms = 250,
            .error_code = 0,
            .trace_id = 9002,
            .span_id = 9003,
        },
        .message_kind = v3::proto::EnvelopeMessageKind::kMatchJoinRequest,
        .payload = {{"user_id", "bench_user"}, {"mmr", 1350}, {"mode", "1v1"}},
    };
}

LatencyStats run_backend_envelope_json_roundtrip_latency(std::size_t iterations) {
    const auto envelope = make_contract_backend_envelope();
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        const auto encoded = v2::service::to_json(envelope);
        const auto decoded = v2::service::from_json(encoded);
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
        if (!decoded.has_value()) {
            break;
        }
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    const auto operations = samples.size();
    return make_stats(std::move(samples), elapsed, operations);
}

LatencyStats run_typed_envelope_json_roundtrip_latency(std::size_t iterations) {
    const auto envelope = make_contract_typed_envelope();
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        const auto encoded = v3::proto::encode_typed_envelope(
            envelope.meta,
            envelope.message_kind,
            envelope.payload);
        const auto decoded = v3::proto::decode_typed_envelope(encoded);
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
        if (!decoded.has_value()) {
            break;
        }
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    const auto operations = samples.size();
    return make_stats(std::move(samples), elapsed, operations);
}

LatencyStats run_backend_typed_adapter_roundtrip_latency(std::size_t iterations) {
    const auto backend = make_contract_backend_envelope();
    std::vector<double> samples;
    samples.reserve(iterations);

    const auto begin = Clock::now();
    for (std::size_t i = 0; i < iterations; ++i) {
        const auto op_begin = now_ns();
        const auto typed = v2::service::to_typed_envelope(backend);
        if (!typed.has_value()) {
            break;
        }
        const auto converted = v2::service::to_backend_envelope(*typed);
        samples.push_back(static_cast<double>(now_ns() - op_begin) / 1000.0);
        if (converted.message_type.empty()) {
            break;
        }
    }
    const auto elapsed = std::chrono::duration<double>(Clock::now() - begin).count();
    const auto operations = samples.size();
    return make_stats(std::move(samples), elapsed, operations);
}

Options parse_args(int argc, char** argv) {
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        const auto require_value = [&](const char* name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error(std::string("missing value for ") + name);
            }
            return argv[++i];
        };

        if (arg == "--iterations") {
            options.iterations = static_cast<std::size_t>(std::stoull(require_value("--iterations")));
        } else if (arg == "--actors") {
            options.actors = static_cast<std::size_t>(std::stoull(require_value("--actors")));
        } else if (arg == "--actor-limit") {
            options.actor_limit = static_cast<std::size_t>(std::stoull(require_value("--actor-limit")));
        } else if (arg == "--battles") {
            options.battles = static_cast<std::size_t>(std::stoull(require_value("--battles")));
        } else if (arg == "--output") {
            options.output_path = require_value("--output");
        } else if (arg == "--help") {
            std::cout << "Usage: v2_arch_benchmark [--iterations N] [--actors N] "
                         "[--actor-limit N] [--battles N] [--output path]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    return options;
}

void append_stats_json(std::ostringstream& out, const char* name, const LatencyStats& stats, bool last) {
    out << "    {\n"
        << "      \"name\": \"" << name << "\",\n"
        << "      \"samples\": " << stats.samples << ",\n"
        << "      \"min_us\": " << stats.min_us << ",\n"
        << "      \"p50_us\": " << stats.p50_us << ",\n"
        << "      \"p90_us\": " << stats.p90_us << ",\n"
        << "      \"p99_us\": " << stats.p99_us << ",\n"
        << "      \"max_us\": " << stats.max_us << ",\n"
        << "      \"throughput_ops_per_sec\": " << stats.throughput_ops_per_sec << "\n"
        << "    }" << (last ? "\n" : ",\n");
}

std::string build_json(const Options& options,
                       const LatencyStats& local_tell,
                       const LatencyStats& cross_core_tell,
                       const LatencyStats& actor_create,
                       const LatencyStats& shutdown,
                       const LatencyStats& bump_arena_alloc,
                       const LatencyStats& object_pool_cycle,
                       const LatencyStats& spsc_queue_roundtrip,
                       const LatencyStats& battle_world_tick,
                       const LatencyStats& actor_fan_in,
                       const LatencyStats& actor_limit_smoke,
                       const LatencyStats& multi_battle_tick,
                       const LatencyStats& instance_runtime_tick_all,
                       const LatencyStats& backend_envelope_roundtrip,
                       const LatencyStats& typed_envelope_roundtrip,
                       const LatencyStats& backend_typed_adapter_roundtrip) {
    std::ostringstream out;
    out << "{\n"
        << "  \"tool\": \"v2_arch_benchmark\",\n"
        << "  \"iterations\": " << options.iterations << ",\n"
        << "  \"actors\": " << options.actors << ",\n"
        << "  \"actor_limit\": " << options.actor_limit << ",\n"
        << "  \"battles\": " << options.battles << ",\n"
        << "  \"results\": [\n";
    append_stats_json(out, "actor_local_tell_dispatch", local_tell, false);
    append_stats_json(out, "actor_cross_core_tell_drain_dispatch", cross_core_tell, false);
    append_stats_json(out, "actor_create", actor_create, false);
    append_stats_json(out, "actor_shutdown_per_actor", shutdown, false);
    append_stats_json(out, "bump_arena_alloc", bump_arena_alloc, false);
    append_stats_json(out, "object_pool_acquire_release", object_pool_cycle, false);
    append_stats_json(out, "spsc_queue_enqueue_dequeue", spsc_queue_roundtrip, false);
    append_stats_json(out, "battle_world_tick_100_entities", battle_world_tick, false);
    append_stats_json(out, "actor_fan_in_throughput", actor_fan_in, false);
    append_stats_json(out, "actor_100k_create_smoke", actor_limit_smoke, false);
    append_stats_json(out, "multi_battle_tick_100_entities", multi_battle_tick, false);
    append_stats_json(out, "instance_runtime_tick_all_one_input", instance_runtime_tick_all, false);
    append_stats_json(out, "backend_envelope_json_roundtrip", backend_envelope_roundtrip, false);
    append_stats_json(out, "typed_envelope_json_roundtrip", typed_envelope_roundtrip, false);
    append_stats_json(out, "backend_typed_adapter_roundtrip", backend_typed_adapter_roundtrip, true);
    out << "  ]\n"
        << "}\n";
    return out.str();
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const auto options = parse_args(argc, argv);
        const auto local_tell = run_local_tell_latency(options.iterations);
        const auto cross_core_tell = run_cross_core_tell_latency(options.iterations);
        const auto actor_create = run_actor_create_latency(options.actors);
        const auto shutdown = run_shutdown_latency(options.actors);
        const auto bump_arena_alloc = run_bump_arena_alloc_latency(options.iterations);
        const auto object_pool_cycle = run_object_pool_cycle_latency(options.iterations);
        const auto spsc_queue_roundtrip = run_spsc_queue_roundtrip_latency(options.iterations);
        const auto battle_world_tick = run_battle_world_tick_latency(options.iterations);
        const auto actor_fan_in = run_actor_fan_in_throughput(options.iterations);
        const auto actor_limit_smoke = run_actor_limit_smoke(options.actor_limit);
        const auto multi_battle_tick = run_multi_battle_tick_latency(options.battles);
        const auto instance_runtime_tick_all =
            run_instance_runtime_tick_all_latency(options.battles, options.iterations);
        const auto backend_envelope_roundtrip =
            run_backend_envelope_json_roundtrip_latency(options.iterations);
        const auto typed_envelope_roundtrip =
            run_typed_envelope_json_roundtrip_latency(options.iterations);
        const auto backend_typed_adapter_roundtrip =
            run_backend_typed_adapter_roundtrip_latency(options.iterations);
        const auto json = build_json(options,
                                     local_tell,
                                     cross_core_tell,
                                     actor_create,
                                     shutdown,
                                     bump_arena_alloc,
                                     object_pool_cycle,
                                     spsc_queue_roundtrip,
                                     battle_world_tick,
                                     actor_fan_in,
                                     actor_limit_smoke,
                                     multi_battle_tick,
                                     instance_runtime_tick_all,
                                     backend_envelope_roundtrip,
                                     typed_envelope_roundtrip,
                                     backend_typed_adapter_roundtrip);

        if (!options.output_path.empty()) {
            std::ofstream output(options.output_path, std::ios::binary);
            if (!output) {
                std::cerr << "failed to open output: " << options.output_path << "\n";
                return 2;
            }
            output << json;
        }
        std::cout << json;
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "v2_arch_benchmark failed: " << e.what() << "\n";
        return 1;
    }
}
