#include "v2/perf/hot_path.h"
#include "v2/realtime/instance_runtime.h"
#include "app/audit_log.h"

#include <algorithm>
#include <chrono>
#include <exception>
#include <mutex>
#include <queue>
#include <string>
#include <unordered_map>
#include <vector>

namespace v2::realtime {

// ─── Internal instance data ─────────────────────────────────────────

struct InternalInstance {
    InstanceContext ctx;
    InstanceState state = InstanceState::kCreating;
    std::unique_ptr<InstancePlugin> plugin;
    std::queue<InputEnvelope> input_queue;
    std::uint32_t current_frame = 0;
    std::int64_t running_since_ms = 0;
    std::uint32_t ack_seq_counter = 0;

    // Per-player input tracking
    struct PlayerInputState {
        std::uint64_t last_seq = 0;
        std::uint32_t last_acked_frame = 0;
    };
    std::unordered_map<std::string, PlayerInputState> player_input_state;
};

// ─── Runtime implementation ─────────────────────────────────────────

class InstanceRuntime::Impl {
public:
    explicit Impl(RuntimeConfig config)
        : config_(config) {}

    void register_plugin(const std::string& instance_type,
                         InstancePluginFactory factory) {
        std::lock_guard<std::mutex> lock(mutex_);
        plugin_factories_[instance_type] = factory;
    }

    void set_event_callback(InstanceEventCallback callback) {
        std::lock_guard<std::mutex> lock(mutex_);
        event_callback_ = std::move(callback);
    }

    std::string create_instance(
        const std::string& instance_id,
        const std::string& room_id,
        const std::string& instance_type,
        const std::vector<PlayerContext>& players,
        std::uint32_t tick_interval_ms,
        std::uint32_t max_frames,
        std::uint32_t resume_window_ms) {

        std::lock_guard<std::mutex> lock(mutex_);

        if (instances_.find(instance_id) != instances_.end()) {
            AUDIT_LOG("instance_create_failure",
                      "instance_id=" + instance_id + " reason=already_exists");
            return {};
        }

        if (instances_.size() >= config_.max_instances) {
            AUDIT_LOG("instance_create_failure",
                      "reason=max_instances_reached count=" +
                      std::to_string(instances_.size()));
            return {};
        }

        auto factory_it = plugin_factories_.find(instance_type);
        if (factory_it == plugin_factories_.end()) {
            AUDIT_LOG("instance_create_failure",
                      "instance_id=" + instance_id +
                      " reason=unknown_plugin_type type=" + instance_type);
            return {};
        }

        auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();

        auto inst = std::make_unique<InternalInstance>();
        inst->ctx.instance_id = instance_id;
        inst->ctx.room_id = room_id;
        inst->ctx.instance_type = instance_type;
        inst->ctx.players = players;
        inst->ctx.tick_interval_ms = tick_interval_ms;
        inst->ctx.max_frames = max_frames;
        inst->ctx.input_queue_limit = 64;
        inst->ctx.resume_window_ms = resume_window_ms;
        inst->ctx.created_at_ms = now_ms;
        inst->plugin = factory_it->second();
        inst->state = InstanceState::kWaitingPlayers;

        // Let the plugin initialise its state
        // Error isolation: if the plugin throws, we log and abort creation
        try {
            inst->plugin->on_instance_created(inst->ctx);
        } catch (const std::exception& e) {
            AUDIT_LOG("instance_create_failure",
                      "instance_id=" + instance_id +
                      " reason=plugin_on_instance_created_exception what=" +
                      std::string(e.what()));
            return {};
        } catch (...) {
            AUDIT_LOG("instance_create_failure",
                      "instance_id=" + instance_id +
                      " reason=plugin_on_instance_created_unknown_exception");
            return {};
        }

        instances_[instance_id] = std::move(inst);

        AUDIT_LOG("instance_created",
                  "instance_id=" + instance_id + " type=" + instance_type +
                  " players=" + std::to_string(players.size()));

        InstanceEvent created_event;
        created_event.type = InstanceEvent::Type::kInstanceCreated;
        created_event.instance_id = instance_id;
        emit_event(created_event);

        return instance_id;
    }

    void destroy_instance(const std::string& instance_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = instances_.find(instance_id);
        if (it == instances_.end()) return;
        instances_.erase(it);
        AUDIT_LOG("instance_destroyed", "instance_id=" + instance_id);
    }

    InputResult submit_input(const InputEnvelope& input) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto* inst = find_instance_locked(input.instance_id);
        if (inst == nullptr) {
            return InputResult{.accepted = false, .reject_reason = "instance_not_found"};
        }

        if (inst->state != InstanceState::kRunning &&
            inst->state != InstanceState::kWaitingPlayers) {
            return InputResult{.accepted = false, .reject_reason = "instance_not_active"};
        }

        // Check input seq for ordering
        auto& player_state = inst->player_input_state[input.user_id];
        if (input.seq > 0 && input.seq <= player_state.last_seq) {
            return InputResult{.accepted = false, .reject_reason = "duplicate_seq"};
        }

        // Check input queue limit
        if (inst->input_queue.size() >= inst->ctx.input_queue_limit) {
            return InputResult{.accepted = false, .reject_reason = "input_queue_full"};
        }

        player_state.last_seq = input.seq;

        // Enqueue input
        inst->input_queue.push(input);
        inst->ack_seq_counter++;

        InputResult input_result;
        input_result.accepted = true;
        input_result.ack_seq = inst->ack_seq_counter;
        return input_result;
    }

    void finish_instance(const std::string& instance_id,
                         FinishReason reason) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto* inst = find_instance_locked(instance_id);
        if (inst == nullptr) return;
        if (inst->state == InstanceState::kFinished ||
            inst->state == InstanceState::kClosed) return;

        inst->state = InstanceState::kFinishing;

        SettlementContext settlement_ctx;
        settlement_ctx.instance_id = instance_id;
        settlement_ctx.room_id = inst->ctx.room_id;
        settlement_ctx.reason = reason;
        settlement_ctx.total_frames = inst->current_frame;

        // Build settlement with error isolation
        // Note: build_settlement is noexcept by contract, but we wrap
        // it for defence-in-depth.
        try {
            settlement_ctx.result_payload = inst->plugin->build_settlement(
                inst->ctx, settlement_ctx);
        } catch (const std::exception& e) {
            AUDIT_LOG("instance_settlement_failure",
                      "instance_id=" + instance_id +
                      " reason=plugin_build_settlement_exception what=" +
                      std::string(e.what()));
            settlement_ctx.result_payload = R"({"error":"settlement_failed"})";
        } catch (...) {
            AUDIT_LOG("instance_settlement_failure",
                      "instance_id=" + instance_id +
                      " reason=plugin_build_settlement_unknown_exception");
            settlement_ctx.result_payload = R"({"error":"settlement_failed"})";
        }

        inst->state = InstanceState::kFinished;

        AUDIT_LOG("instance_finished",
                  "instance_id=" + instance_id +
                  " reason=" + to_string(reason) +
                  " frames=" + std::to_string(inst->current_frame));

        InstanceEvent finished_event;
        finished_event.type = InstanceEvent::Type::kInstanceFinished;
        finished_event.instance_id = instance_id;
        finished_event.settlement = std::move(settlement_ctx);
        emit_event(finished_event);
    }

    Snapshot get_resume_snapshot(const std::string& instance_id,
                                  const std::string& user_id) {
        std::lock_guard<std::mutex> lock(mutex_);

        auto* inst = find_instance_locked(instance_id);
        if (inst == nullptr) return {};

        auto* player = inst->ctx.find_player(user_id);
        if (player == nullptr) return {};

        // Build resume snapshot with error isolation
        // Note: build_resume_snapshot is noexcept by contract, but we
        // wrap it for defence-in-depth.
        try {
            return inst->plugin->build_resume_snapshot(inst->ctx, *player);
        } catch (const std::exception& e) {
            AUDIT_LOG("instance_resume_snapshot_failure",
                      "instance_id=" + instance_id +
                      " user_id=" + user_id +
                      " reason=plugin_build_resume_snapshot_exception what=" +
                      std::string(e.what()));
            return {};
        } catch (...) {
            AUDIT_LOG("instance_resume_snapshot_failure",
                      "instance_id=" + instance_id +
                      " user_id=" + user_id +
                      " reason=plugin_build_resume_snapshot_unknown_exception");
            return {};
        }
    }

    BOOST_HOT_PATH
    TickStats tick_instance(const std::string& instance_id,
                            std::uint32_t frame_number,
                            std::int64_t tick_start_ms) {
        std::unique_lock<std::mutex> lock(mutex_);

        auto* inst = find_instance_locked(instance_id);
        if (inst == nullptr) {
            return TickStats{.frame_number = frame_number, .should_finish = true,
                            .finish_reason = FinishReason::kError};
        }

        if (inst->state != InstanceState::kRunning &&
            inst->state != InstanceState::kWaitingPlayers) {
            return TickStats{.frame_number = frame_number};
        }

        // Transition to running on first tick after players are ready
        if (inst->state == InstanceState::kWaitingPlayers) {
            inst->state = InstanceState::kRunning;
            inst->running_since_ms = tick_start_ms;
        }

        inst->current_frame = frame_number;

        // Drain inputs from queue
        FrameContext frame_ctx;
        frame_ctx.frame_number = frame_number;
        frame_ctx.tick_start_ms = tick_start_ms;

        while (!inst->input_queue.empty()) {
            auto& input = inst->input_queue.front();

            // Error isolation for on_input
            try {
                auto result = inst->plugin->on_input(inst->ctx, input);
                if (result.accepted) {
                    input.seq = result.ack_seq;  // update to ack seq
                    frame_ctx.inputs_this_tick.push_back(std::move(input));

                    auto& ps = inst->player_input_state[input.user_id];
                    ps.last_acked_frame = frame_number;
                }
            } catch (const std::exception& e) {
                AUDIT_LOG("plugin_on_input_exception",
                          "instance_id=" + instance_id +
                          " user_id=" + input.user_id +
                          " seq=" + std::to_string(input.seq) +
                          " what=" + std::string(e.what()));
                // Input is rejected, not added to inputs_this_tick
            } catch (...) {
                AUDIT_LOG("plugin_on_input_unknown_exception",
                          "instance_id=" + instance_id +
                          " user_id=" + input.user_id +
                          " seq=" + std::to_string(input.seq));
                // Input is rejected, not added to inputs_this_tick
            }

            inst->input_queue.pop();
        }

        // Forward tick to plugin
        // Note: on_tick is noexcept by contract; try-catch is defence-in-depth.
        TickStats tick_result;
        try {
            tick_result = inst->plugin->on_tick(inst->ctx, frame_ctx);
        } catch (const std::exception& e) {
            AUDIT_LOG("plugin_on_tick_exception",
                      "instance_id=" + instance_id +
                      " frame=" + std::to_string(frame_number) +
                      " what=" + std::string(e.what()));
            tick_result = TickStats{
                .frame_number = frame_number,
                .should_finish = true,
                .finish_reason = FinishReason::kError,
            };
        } catch (...) {
            AUDIT_LOG("plugin_on_tick_unknown_exception",
                      "instance_id=" + instance_id +
                      " frame=" + std::to_string(frame_number));
            tick_result = TickStats{
                .frame_number = frame_number,
                .should_finish = true,
                .finish_reason = FinishReason::kError,
            };
        }

        // Check frame limit
        if (inst->ctx.max_frames > 0 &&
            frame_number >= inst->ctx.max_frames &&
            !tick_result.should_finish) {
            tick_result.should_finish = true;
            tick_result.finish_reason = FinishReason::kFrameLimit;
        }

        // Build and emit snapshot
        // Note: build_snapshot is noexcept by contract; try-catch is defence-in-depth.
        Snapshot snapshot;
        try {
            snapshot = inst->plugin->build_snapshot(inst->ctx);
        } catch (const std::exception& e) {
            AUDIT_LOG("plugin_build_snapshot_exception",
                      "instance_id=" + instance_id +
                      " frame=" + std::to_string(frame_number) +
                      " what=" + std::string(e.what()));
            snapshot = Snapshot{
                .frame_number = frame_number,
                .payload_type = "error",
                .payload = R"({"error":"snapshot_failed"})",
            };
        } catch (...) {
            AUDIT_LOG("plugin_build_snapshot_unknown_exception",
                      "instance_id=" + instance_id +
                      " frame=" + std::to_string(frame_number));
            snapshot = Snapshot{
                .frame_number = frame_number,
                .payload_type = "error",
                .payload = R"({"error":"snapshot_failed"})",
            };
        }
        snapshot.frame_number = frame_number;

        InstanceEvent snapshot_event;
        snapshot_event.type = InstanceEvent::Type::kSnapshotAvailable;
        snapshot_event.instance_id = instance_id;
        snapshot_event.snapshot = std::move(snapshot);
        emit_event(snapshot_event);

        // Handle finish request from plugin — release lock to avoid re-entrancy
        if (tick_result.should_finish) {
            lock.unlock();
            finish_instance(instance_id, tick_result.finish_reason);
            lock.lock();
        }

        return tick_result;
    }

    std::vector<TickStats> tick_all(std::int64_t tick_start_ms) {
        std::vector<TickStats> results;

        // Collect instance IDs + next frame under lock, tick outside to avoid deadlock
        std::vector<std::pair<std::string, std::uint32_t>> to_tick;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            for (const auto& [id, inst] : instances_) {
                if (inst->state == InstanceState::kRunning ||
                    inst->state == InstanceState::kWaitingPlayers) {
                    to_tick.push_back({id, inst->current_frame + 1});
                }
            }
        }

        for (const auto& [id, frame] : to_tick) {
            auto stats = tick_instance(id, frame, tick_start_ms);
            results.push_back(std::move(stats));
        }

        return results;
    }

    InstanceContext* find_instance(const std::string& instance_id) {
        std::lock_guard<std::mutex> lock(mutex_);
        auto* inst = find_instance_locked(instance_id);
        return inst ? &inst->ctx : nullptr;
    }

    InstanceState get_instance_state(const std::string& instance_id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = instances_.find(instance_id);
        return it != instances_.end() ? it->second->state : InstanceState::kClosed;
    }

    std::vector<InstanceSnapshot> list_instances() const {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<InstanceSnapshot> result;
        for (const auto& [id, inst] : instances_) {
            InstanceSnapshot snap;
            snap.instance_id = id;
            snap.instance_type = inst->ctx.instance_type;
            snap.state = inst->state;
            snap.frame_number = inst->current_frame;
            snap.player_count = static_cast<std::uint32_t>(inst->ctx.players.size());
            snap.input_queue_size = static_cast<std::uint32_t>(inst->input_queue.size());
            snap.created_at_ms = inst->ctx.created_at_ms;
            snap.running_since_ms = inst->running_since_ms;
            result.push_back(std::move(snap));
        }
        return result;
    }

    std::size_t instance_count() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return instances_.size();
    }

private:
    RuntimeConfig config_;
    mutable std::mutex mutex_;
    std::unordered_map<std::string, std::unique_ptr<InternalInstance>> instances_;
    std::unordered_map<std::string, InstancePluginFactory> plugin_factories_;
    InstanceEventCallback event_callback_;

    InternalInstance* find_instance_locked(const std::string& id) {
        auto it = instances_.find(id);
        return it != instances_.end() ? it->second.get() : nullptr;
    }

    void emit_event(InstanceEvent event) {
        if (event_callback_) {
            event_callback_(std::move(event));
        }
    }
};

// ─── Public API ─────────────────────────────────────────────────────

InstanceRuntime::InstanceRuntime(RuntimeConfig config)
    : impl_(std::make_unique<Impl>(std::move(config))) {}

InstanceRuntime::~InstanceRuntime() = default;

void InstanceRuntime::register_plugin(const std::string& instance_type,
                                       InstancePluginFactory factory) {
    impl_->register_plugin(instance_type, factory);
}

void InstanceRuntime::set_event_callback(InstanceEventCallback callback) {
    impl_->set_event_callback(std::move(callback));
}

std::string InstanceRuntime::create_instance(
    const std::string& instance_id,
    const std::string& room_id,
    const std::string& instance_type,
    const std::vector<PlayerContext>& players,
    std::uint32_t tick_interval_ms,
    std::uint32_t max_frames,
    std::uint32_t resume_window_ms) {
    return impl_->create_instance(
        instance_id, room_id, instance_type, players,
        tick_interval_ms, max_frames, resume_window_ms);
}

void InstanceRuntime::destroy_instance(const std::string& instance_id) {
    impl_->destroy_instance(instance_id);
}

InputResult InstanceRuntime::submit_input(const InputEnvelope& input) {
    return impl_->submit_input(input);
}

void InstanceRuntime::finish_instance(const std::string& instance_id,
                                       FinishReason reason) {
    impl_->finish_instance(instance_id, reason);
}

Snapshot InstanceRuntime::get_resume_snapshot(
    const std::string& instance_id, const std::string& user_id) {
    return impl_->get_resume_snapshot(instance_id, user_id);
}

BOOST_HOT_PATH
TickStats InstanceRuntime::tick_instance(
    const std::string& instance_id, std::uint32_t frame_number,
    std::int64_t tick_start_ms) {
    return impl_->tick_instance(instance_id, frame_number, tick_start_ms);
}

std::vector<TickStats> InstanceRuntime::tick_all(std::int64_t tick_start_ms) {
    return impl_->tick_all(tick_start_ms);
}

InstanceContext* InstanceRuntime::find_instance(const std::string& instance_id) {
    return impl_->find_instance(instance_id);
}

InstanceState InstanceRuntime::get_instance_state(const std::string& instance_id) const {
    return impl_->get_instance_state(instance_id);
}

std::vector<InstanceSnapshot> InstanceRuntime::list_instances() const {
    return impl_->list_instances();
}

std::size_t InstanceRuntime::instance_count() const {
    return impl_->instance_count();
}

}  // namespace v2::realtime
