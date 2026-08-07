#include "v2/perf/hot_path.h"
#include "v2/realtime/instance_runtime.h"
#include "app/audit_log.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <exception>
#include <memory>
#include <mutex>
#include <queue>
#include <string>
#include <unordered_map>
#include <vector>

namespace v2::realtime {

// ─── Internal instance data ─────────────────────────────────────────

struct InternalInstance {
    // Per-instance state must remain ordered, but unrelated battles should
    // not contend on the runtime's instance-table mutex.
    std::mutex mutex;
    InstanceContext ctx;
    InstanceState state = InstanceState::kCreating;
    std::unique_ptr<InstancePlugin> plugin;
    std::queue<InputEnvelope> input_queue;
    std::uint32_t current_frame = 0;
    std::int64_t running_since_ms = 0;
    std::uint32_t ack_seq_counter = 0;
    std::atomic<bool> registered{true};

    // Per-player input tracking
    struct PlayerInputState {
        std::uint64_t last_seq = 0;
        std::uint32_t last_acked_frame = 0;
    };
    std::unordered_map<std::string, PlayerInputState> player_input_state;

    ~InternalInstance() {
        if (plugin != nullptr) {
            plugin->on_instance_destroyed(ctx);
        }
        ctx.plugin_state = nullptr;
    }
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

    BOOST_COLD_PATH
    bool set_event_callback(InstanceEventCallback callback) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (callback_frozen_) return false;
        event_callback_ = std::move(callback);
        return true;
    }

    BOOST_COLD_PATH
    std::string create_instance(
        const std::string& instance_id,
        const std::string& room_id,
        const std::string& instance_type,
        const std::vector<PlayerContext>& players,
        std::uint32_t tick_interval_ms,
        std::uint32_t max_frames,
        std::uint32_t resume_window_ms) {
        InstancePluginFactory factory = nullptr;
        {
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
            const auto factory_it = plugin_factories_.find(instance_type);
            if (factory_it == plugin_factories_.end()) {
                AUDIT_LOG("instance_create_failure",
                          "instance_id=" + instance_id +
                          " reason=unknown_plugin_type type=" + instance_type);
                return {};
            }
            factory = factory_it->second;
        }
        if (factory == nullptr) {
            AUDIT_LOG("instance_create_failure",
                      "instance_id=" + instance_id + " reason=null_plugin_factory");
            return {};
        }

        auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch()).count();

        auto inst = std::make_shared<InternalInstance>();
        inst->ctx.instance_id = instance_id;
        inst->ctx.room_id = room_id;
        inst->ctx.instance_type = instance_type;
        inst->ctx.players = players;
        inst->ctx.tick_interval_ms = tick_interval_ms;
        inst->ctx.max_frames = max_frames;
        inst->ctx.input_queue_limit = 64;
        inst->ctx.resume_window_ms = resume_window_ms;
        inst->ctx.created_at_ms = now_ms;
        inst->plugin = factory();
        if (inst->plugin == nullptr) {
            AUDIT_LOG("instance_create_failure",
                      "instance_id=" + instance_id + " reason=null_plugin");
            return {};
        }
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

        {
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
            instances_.emplace(instance_id, inst);
            callback_frozen_ = true;
        }

        AUDIT_LOG("instance_created",
                  "instance_id=" + instance_id + " type=" + instance_type +
                  " players=" + std::to_string(players.size()));

        InstanceEvent created_event;
        created_event.type = InstanceEvent::Type::kInstanceCreated;
        created_event.instance_id = instance_id;
        emit_event(created_event);

        return instance_id;
    }

    BOOST_COLD_PATH
    void destroy_instance(const std::string& instance_id) {
        std::shared_ptr<InternalInstance> removed;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            auto it = instances_.find(instance_id);
            if (it == instances_.end()) return;
            removed = it->second;
            removed->registered.store(false, std::memory_order_release);
            instances_.erase(it);
        }
        AUDIT_LOG("instance_destroyed", "instance_id=" + instance_id);
    }

    BOOST_COLD_PATH
    PlayerLifecycleResult attach_player(const std::string& instance_id,
                                        const PlayerContext& player) {
        if (player.user_id.empty()) {
            return {.applied = false, .reject_reason = "empty_user_id"};
        }
        const auto inst = find_instance_shared(instance_id);
        if (!inst) {
            return {.applied = false, .reject_reason = "instance_not_found"};
        }
        {
            std::lock_guard<std::mutex> lock(inst->mutex);
            if (!inst->registered.load(std::memory_order_acquire)) {
                return {.applied = false, .reject_reason = "instance_not_found"};
            }
            if (inst->state != InstanceState::kRunning &&
                inst->state != InstanceState::kWaitingPlayers) {
                return {.applied = false, .reject_reason = "instance_not_active"};
            }
            if (inst->ctx.find_player(player.user_id) != nullptr) {
                return {.applied = false, .reject_reason = "player_already_attached"};
            }
            try {
                inst->plugin->on_player_join(inst->ctx, player);
            } catch (const std::exception& e) {
                AUDIT_LOG("plugin_on_player_join_exception",
                          "instance_id=" + instance_id +
                          " user_id=" + player.user_id +
                          " what=" + std::string(e.what()));
            } catch (...) {
                AUDIT_LOG("plugin_on_player_join_unknown_exception",
                          "instance_id=" + instance_id +
                          " user_id=" + player.user_id);
            }
            inst->ctx.players.push_back(player);
            inst->player_input_state.try_emplace(player.user_id);
        }

        InstanceEvent event;
        event.type = InstanceEvent::Type::kPlayerJoined;
        event.instance_id = instance_id;
        event.user_id = player.user_id;
        emit_event(std::move(event));
        return {.applied = true};
    }

    BOOST_COLD_PATH
    PlayerLifecycleResult detach_player(const std::string& instance_id,
                                        const std::string& user_id) {
        const auto inst = find_instance_shared(instance_id);
        if (!inst) {
            return {.applied = false, .reject_reason = "instance_not_found"};
        }
        {
            std::lock_guard<std::mutex> lock(inst->mutex);
            if (!inst->registered.load(std::memory_order_acquire)) {
                return {.applied = false, .reject_reason = "instance_not_found"};
            }
            if (inst->state != InstanceState::kRunning &&
                inst->state != InstanceState::kWaitingPlayers) {
                return {.applied = false, .reject_reason = "instance_not_active"};
            }
            const auto player_it = std::find_if(
                inst->ctx.players.begin(), inst->ctx.players.end(),
                [&user_id](const PlayerContext& player) {
                    return player.user_id == user_id;
                });
            if (player_it == inst->ctx.players.end()) {
                return {.applied = false, .reject_reason = "player_not_attached"};
            }
            const auto player = *player_it;
            try {
                inst->plugin->on_player_leave(inst->ctx, player);
            } catch (const std::exception& e) {
                AUDIT_LOG("plugin_on_player_leave_exception",
                          "instance_id=" + instance_id +
                          " user_id=" + user_id +
                          " what=" + std::string(e.what()));
            } catch (...) {
                AUDIT_LOG("plugin_on_player_leave_unknown_exception",
                          "instance_id=" + instance_id +
                          " user_id=" + user_id);
            }
            inst->ctx.players.erase(player_it);
            inst->player_input_state.erase(user_id);

            std::queue<InputEnvelope> retained_inputs;
            while (!inst->input_queue.empty()) {
                auto input = std::move(inst->input_queue.front());
                inst->input_queue.pop();
                if (input.user_id != user_id) {
                    retained_inputs.push(std::move(input));
                }
            }
            inst->input_queue.swap(retained_inputs);
        }

        InstanceEvent event;
        event.type = InstanceEvent::Type::kPlayerLeft;
        event.instance_id = instance_id;
        event.user_id = user_id;
        emit_event(std::move(event));
        return {.applied = true};
    }

    InputResult submit_input(const InputEnvelope& input) {
        const auto inst = find_instance_shared(input.instance_id);
        if (!inst) {
            return InputResult{.accepted = false, .reject_reason = "instance_not_found"};
        }
        std::lock_guard<std::mutex> lock(inst->mutex);

        if (!inst->registered.load(std::memory_order_acquire)) {
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

    InputResult process_input_immediate(const InputEnvelope& input) {
        const auto inst = find_instance_shared(input.instance_id);
        if (!inst) {
            return InputResult{.accepted = false, .reject_reason = "instance_not_found"};
        }
        std::lock_guard<std::mutex> lock(inst->mutex);
        if (!inst->registered.load(std::memory_order_acquire)) {
            return InputResult{.accepted = false, .reject_reason = "instance_not_found"};
        }
        if (inst->state != InstanceState::kRunning &&
            inst->state != InstanceState::kWaitingPlayers) {
            return InputResult{.accepted = false, .reject_reason = "instance_not_active"};
        }
        try {
            return inst->plugin->on_input(inst->ctx, input);
        } catch (const std::exception& e) {
            AUDIT_LOG("plugin_on_input_exception",
                      "instance_id=" + input.instance_id +
                          " user_id=" + input.user_id +
                          " what=" + std::string(e.what()));
        } catch (...) {
            AUDIT_LOG("plugin_on_input_unknown_exception",
                      "instance_id=" + input.instance_id +
                          " user_id=" + input.user_id);
        }
        return InputResult{.accepted = false, .reject_reason = "input_processing_failed"};
    }

    BOOST_COLD_PATH
    void finish_instance(const std::string& instance_id,
                         FinishReason reason) {
        const auto inst = find_instance_shared(instance_id);
        if (!inst) return;
        SettlementContext settlement_ctx;
        {
            std::lock_guard<std::mutex> lock(inst->mutex);
            if (!inst->registered.load(std::memory_order_acquire)) return;
            if (inst->state == InstanceState::kFinished ||
                inst->state == InstanceState::kClosed) return;

            inst->state = InstanceState::kFinishing;
            settlement_ctx.instance_id = instance_id;
            settlement_ctx.room_id = inst->ctx.room_id;
            settlement_ctx.reason = reason;
            settlement_ctx.total_frames = inst->current_frame;

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
        }

        InstanceEvent finished_event;
        finished_event.type = InstanceEvent::Type::kInstanceFinished;
        finished_event.instance_id = instance_id;
        finished_event.settlement = std::move(settlement_ctx);
        emit_event(std::move(finished_event));
    }

    BOOST_COLD_PATH
    Snapshot get_resume_snapshot(const std::string& instance_id,
                                 const std::string& user_id) {
        const auto inst = find_instance_shared(instance_id);
        if (!inst) return {};
        std::lock_guard<std::mutex> lock(inst->mutex);
        if (!inst->registered.load(std::memory_order_acquire)) return {};

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
        const auto inst = find_instance_shared(instance_id);
        if (!inst) {
            return TickStats{.frame_number = frame_number, .should_finish = true,
                            .finish_reason = FinishReason::kError};
        }
        return tick_instance(inst, frame_number, tick_start_ms);
    }

    BOOST_HOT_PATH
    TickStats tick_instance(const std::shared_ptr<InternalInstance>& inst,
                            std::uint32_t frame_number,
                            std::int64_t tick_start_ms) {
        std::unique_lock<std::mutex> lock(inst->mutex);
        if (!inst->registered.load(std::memory_order_acquire)) {
            return TickStats{.frame_number = frame_number, .should_finish = true,
                            .finish_reason = FinishReason::kError};
        }
        const auto& instance_id = inst->ctx.instance_id;

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
        frame_ctx.inputs_this_tick.reserve(inst->input_queue.size());

        while (!inst->input_queue.empty()) {
            auto& input = inst->input_queue.front();

            // Error isolation for on_input
            try {
                auto result = inst->plugin->on_input(inst->ctx, input);
                if (result.accepted) {
                    auto& ps = inst->player_input_state[input.user_id];
                    ps.last_acked_frame = frame_number;

                    input.seq = result.ack_seq;  // update to ack seq
                    frame_ctx.inputs_this_tick.push_back(std::move(input));
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
        lock.unlock();
        emit_event(std::move(snapshot_event));

        if (tick_result.should_finish) {
            finish_instance(instance_id, tick_result.finish_reason);
        }

        return tick_result;
    }

    std::vector<TickStats> tick_all(std::int64_t tick_start_ms) {
        std::vector<TickStats> results;

        // Hold table references briefly, then inspect/tick instances under
        // their own locks so separate battles can run concurrently.
        std::vector<std::pair<std::shared_ptr<InternalInstance>, std::uint32_t>> to_tick;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            to_tick.reserve(instances_.size());
            for (const auto& [_, inst] : instances_) {
                to_tick.emplace_back(inst, 0U);
            }
        }
        std::size_t active_count = 0;
        for (std::size_t i = 0; i < to_tick.size(); ++i) {
            const auto& inst = to_tick[i].first;
            std::lock_guard<std::mutex> lock(inst->mutex);
            if (!inst->registered.load(std::memory_order_acquire) ||
                (inst->state != InstanceState::kRunning &&
                 inst->state != InstanceState::kWaitingPlayers)) {
                continue;
            }
            to_tick[i].second = inst->current_frame + 1;
            if (active_count != i) {
                to_tick[active_count] = std::move(to_tick[i]);
            }
            ++active_count;
        }
        to_tick.resize(active_count);

        results.reserve(to_tick.size());
        for (const auto& [inst, frame] : to_tick) {
            auto stats = tick_instance(inst, frame, tick_start_ms);
            results.push_back(std::move(stats));
        }

        return results;
    }

    bool contains_instance(const std::string& instance_id) const {
        return static_cast<bool>(find_instance_shared(instance_id));
    }

    InstanceState get_instance_state(const std::string& instance_id) const {
        const auto inst = find_instance_shared(instance_id);
        if (!inst) return InstanceState::kClosed;
        std::lock_guard<std::mutex> lock(inst->mutex);
        if (!inst->registered.load(std::memory_order_acquire)) {
            return InstanceState::kClosed;
        }
        return inst->state;
    }

    std::vector<InstanceSnapshot> list_instances() const {
        std::vector<std::shared_ptr<InternalInstance>> instances;
        {
            std::lock_guard<std::mutex> lock(mutex_);
            instances.reserve(instances_.size());
            for (const auto& [_, inst] : instances_) {
                instances.push_back(inst);
            }
        }
        std::vector<InstanceSnapshot> result;
        result.reserve(instances.size());
        for (const auto& inst : instances) {
            std::lock_guard<std::mutex> lock(inst->mutex);
            if (!inst->registered.load(std::memory_order_acquire)) continue;
            InstanceSnapshot snap;
            snap.instance_id = inst->ctx.instance_id;
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
    std::unordered_map<std::string, std::shared_ptr<InternalInstance>> instances_;
    std::unordered_map<std::string, InstancePluginFactory> plugin_factories_;
    InstanceEventCallback event_callback_;
    bool callback_frozen_ = false;

    std::shared_ptr<InternalInstance> find_instance_shared(const std::string& id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = instances_.find(id);
        return it != instances_.end() ? it->second : nullptr;
    }

    void emit_event(InstanceEvent event) {
        if (!event_callback_) return;
        try {
            event_callback_(event);
        } catch (const std::exception& e) {
            AUDIT_LOG("instance_event_callback_exception",
                      "instance_id=" + event.instance_id +
                      " what=" + std::string(e.what()));
        } catch (...) {
            AUDIT_LOG("instance_event_callback_unknown_exception",
                      "instance_id=" + event.instance_id);
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

bool InstanceRuntime::set_event_callback(InstanceEventCallback callback) {
    return impl_->set_event_callback(std::move(callback));
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

PlayerLifecycleResult InstanceRuntime::attach_player(
    const std::string& instance_id, const PlayerContext& player) {
    return impl_->attach_player(instance_id, player);
}

PlayerLifecycleResult InstanceRuntime::detach_player(
    const std::string& instance_id, const std::string& user_id) {
    return impl_->detach_player(instance_id, user_id);
}

InputResult InstanceRuntime::submit_input(const InputEnvelope& input) {
    return impl_->submit_input(input);
}

InputResult InstanceRuntime::process_input_immediate(const InputEnvelope& input) {
    return impl_->process_input_immediate(input);
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

bool InstanceRuntime::contains_instance(const std::string& instance_id) const {
    return impl_->contains_instance(instance_id);
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
