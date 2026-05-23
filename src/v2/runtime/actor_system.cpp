#include "v2/runtime/actor_system.h"
#include "v2/io/io_engine.h"

#include <algorithm>
#include <cassert>
#include <utility>

#include <spdlog/spdlog.h>

namespace v2::runtime {

ScheduleHandle::ScheduleHandle(ActorSystem* system, ActorSystem::ScheduleId schedule_id) noexcept
    : system_(system), schedule_id_(schedule_id) {}

ScheduleHandle::~ScheduleHandle() {
    if (system_ != nullptr && schedule_id_ != 0) {
        system_->cancel_schedule(schedule_id_);
    }
}

ScheduleHandle::ScheduleHandle(ScheduleHandle&& other) noexcept
    : system_(other.system_), schedule_id_(other.schedule_id_) {
    other.system_ = nullptr;
    other.schedule_id_ = 0;
}

ScheduleHandle& ScheduleHandle::operator=(ScheduleHandle&& other) noexcept {
    if (this != &other) {
        (void)release();
        system_ = other.system_;
        schedule_id_ = other.schedule_id_;
        other.system_ = nullptr;
        other.schedule_id_ = 0;
    }
    return *this;
}

bool ScheduleHandle::release() noexcept {
    if (system_ == nullptr || schedule_id_ == 0) {
        return false;
    }
    system_ = nullptr;
    schedule_id_ = 0;
    return true;
}

ActorSystem::~ActorSystem() {
    shutdown();
}

v2::actor::ActorRef ActorSystem::create_actor(
    std::unique_ptr<v2::actor::Actor> actor,
    v2::actor::ActorRef parent) {
    return create_actor(std::move(actor), std::move(parent), std::nullopt);
}

v2::actor::ActorRef ActorSystem::create_actor(
    std::unique_ptr<v2::actor::Actor> actor,
    v2::actor::ActorRef parent,
    std::optional<std::uint32_t> affinity_core) {
    if (!actor || shutting_down_) {
        return {};
    }

    const v2::actor::ActorId actor_id = next_actor_id_++;
    auto [it, inserted] = actors_.try_emplace(actor_id);
    if (!inserted) {
        return {};
    }

    auto self_ref = v2::actor::ActorRef(this, actor_id, affinity_core);
    it->second.actor = std::move(actor);
    it->second.core_id = affinity_core;
    it->second.actor->bind(self_ref, parent);
    it->second.actor->on_start();
    it->second.started = true;
    return self_ref;
}

#ifndef NDEBUG
bool ActorSystem::is_on_owner_core() const noexcept {
    if (!io_engine_ || !dispatch_owner_core_.has_value()) {
        // No io_engine or not in a dispatch context — cannot verify, assume OK.
        return true;
    }
    const auto current = io_engine_->current_core_id();
    return current.has_value() && *current == *dispatch_owner_core_;
}
#endif

void ActorSystem::send(v2::actor::Message message) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return;
    }

    auto* cell = find_cell(message.header.target_actor);
    if (cell == nullptr) {
        return;
    }

    if (io_engine_ && cell->core_id.has_value()) {
        auto current = io_engine_->current_core_id();
        if (current.has_value() && *current != *cell->core_id) {
            io_engine_->post_mailbox(*cell->core_id, std::move(message));
            return;
        }
    }

#ifndef NDEBUG
    // If we reach local delivery with an io_engine, target has core affinity,
    // and we're in a dispatch context, verify we haven't bypassed cross-core routing.
    if (io_engine_ && cell->core_id.has_value() && dispatch_owner_core_.has_value()) {
        assert(*dispatch_owner_core_ == *cell->core_id);
    }
#endif

    cell->mailbox.push_back(std::move(message));
    enqueue_ready_actor(message.header.target_actor, *cell);
}

void ActorSystem::send_after(v2::actor::Message message, std::size_t dispatch_delay) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return;
    }
    if (dispatch_delay == 0) {
        send(std::move(message));
        return;
    }

    scheduled_messages_.push_back(ScheduledMessage{
        .use_wall_clock = false,
        .ready_after_dispatch = dispatch_round_ + dispatch_delay,
        .message = std::move(message),
    });
}

void ActorSystem::send_after(v2::actor::Message message, Duration delay) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return;
    }
    if (delay <= Duration::zero()) {
        send(std::move(message));
        return;
    }

    (void)schedule_after(std::move(message), delay);
}

void ActorSystem::send_at(v2::actor::Message message, TimePoint ready_at) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return;
    }

    scheduled_messages_.push_back(ScheduledMessage{
        .schedule_id = next_schedule_id_++,
        .use_wall_clock = true,
        .ready_at = ready_at,
        .message = std::move(message),
    });
}

ActorSystem::ScheduleId ActorSystem::schedule_after(v2::actor::Message message, Duration delay) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return 0;
    }
    if (delay <= Duration::zero()) {
        send(std::move(message));
        return 0;
    }

    const auto schedule_id = next_schedule_id_++;
    scheduled_messages_.push_back(ScheduledMessage{
        .schedule_id = schedule_id,
        .use_wall_clock = true,
        .ready_at = Clock::now() + delay,
        .message = std::move(message),
    });
    return schedule_id;
}

ActorSystem::ScheduleId ActorSystem::schedule_after(v2::actor::Message message, TimePoint ready_at) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return 0;
    }
    if (ready_at <= Clock::now()) {
        send(std::move(message));
        return 0;
    }
    const auto schedule_id = next_schedule_id_++;
    scheduled_messages_.push_back(ScheduledMessage{
        .schedule_id = schedule_id,
        .use_wall_clock = true,
        .ready_at = ready_at,
        .message = std::move(message),
    });
    return schedule_id;
}

ActorSystem::ScheduleId ActorSystem::schedule_every(v2::actor::Message message,
                                                    Duration initial_delay,
                                                    Duration interval) {
    if (shutting_down_ || message.header.target_actor == 0 || interval <= Duration::zero()) {
        return 0;
    }
    const auto schedule_id = next_schedule_id_++;
    scheduled_messages_.push_back(ScheduledMessage{
        .schedule_id = schedule_id,
        .use_wall_clock = true,
        .ready_at = Clock::now() + (initial_delay <= Duration::zero() ? interval : initial_delay),
        .repeat_interval = interval,
        .message = std::move(message),
    });
    return schedule_id;
}

ActorSystem::ScheduleId ActorSystem::schedule_every(v2::actor::Message message,
                                                    Duration initial_delay,
                                                    Duration interval,
                                                    std::size_t max_repetitions) {
    if (shutting_down_ || message.header.target_actor == 0 || interval <= Duration::zero() || max_repetitions == 0) {
        return 0;
    }
    const auto schedule_id = next_schedule_id_++;
    scheduled_messages_.push_back(ScheduledMessage{
        .schedule_id = schedule_id,
        .use_wall_clock = true,
        .ready_at = Clock::now() + (initial_delay <= Duration::zero() ? interval : initial_delay),
        .repeat_interval = interval,
        .max_repetitions = max_repetitions,
        .message = std::move(message),
    });
    return schedule_id;
}

bool ActorSystem::cancel_schedule(ScheduleId schedule_id) noexcept {
    const auto before = scheduled_messages_.size();
    scheduled_messages_.erase(
        std::remove_if(
            scheduled_messages_.begin(),
            scheduled_messages_.end(),
            [schedule_id](const ScheduledMessage& scheduled) { return scheduled.schedule_id == schedule_id; }),
        scheduled_messages_.end());
    return before != scheduled_messages_.size();
}

std::size_t ActorSystem::dispatch_all() {
    return dispatch_ready(io_engine_ ? io_engine_->current_core_id() : std::nullopt);
}

std::size_t ActorSystem::dispatch_ready(std::optional<std::uint32_t> owner_core) {
    if (shutting_down_) {
        dispatch_owner_core_.reset();
        return 0;
    }

#ifndef NDEBUG
    // If already in a dispatch context, verify we haven't migrated cores
    // (reentrant dispatch from a different IO thread).
    assert(is_on_owner_core());
#endif

    std::size_t dispatched = 0;
    const auto previous_owner_core = dispatch_owner_core_;
    dispatch_owner_core_ = owner_core;
    promote_scheduled_messages();
    while (!ready_actors_.empty()) {
        const auto actor_id = ready_actors_.front();
        ready_actors_.pop_front();

        auto* cell = find_cell(actor_id);
        if (cell == nullptr) {
            continue;
        }
        cell->queued = false;

        if (cell->mailbox.empty()) {
            continue;
        }

        auto message = std::move(cell->mailbox.front());
        cell->mailbox.pop_front();
        try {
            cell->actor->on_message(std::move(message));
        } catch (const std::exception& e) {
            SPDLOG_ERROR("Actor {} threw in on_message: {}", actor_id, e.what());
        } catch (...) {
            SPDLOG_ERROR("Actor {} threw unknown exception in on_message", actor_id);
        }
        ++dispatched;
        if (shutting_down_) {
            dispatch_owner_core_ = previous_owner_core;
            return dispatched;
        }
        if (!cell->mailbox.empty()) {
            enqueue_ready_actor(actor_id, *cell);
        }
    }
    ++dispatch_round_;
    dispatch_owner_core_ = previous_owner_core;
    return dispatched;
}

void ActorSystem::set_io_engine(v2::io::IoEngine* io_engine) {
    io_engine_ = io_engine;
    if (io_engine_) {
        io_engine_->set_actor_system(this);
    }
}

std::size_t ActorSystem::drain_mailbox_and_dispatch(std::uint32_t core_id) {
    if (!io_engine_) return 0;

    auto messages = io_engine_->drain_mailbox(core_id);
    for (auto& msg : messages) {
        auto* cell = find_cell(msg.header.target_actor);
        if (cell == nullptr) continue;
        cell->mailbox.push_back(std::move(msg));
        enqueue_ready_actor(msg.header.target_actor, *cell);
    }

    return dispatch_ready(core_id);
}

std::optional<std::uint32_t> ActorSystem::dispatch_owner_core() const noexcept {
    return dispatch_owner_core_;
}

void ActorSystem::shutdown() {
    if (shutting_down_) {
        return;
    }
    shutting_down_ = true;
    dispatch_owner_core_.reset();
    ready_actors_.clear();
    scheduled_messages_.clear();

    for (auto& [actor_id, cell] : actors_) {
        (void)actor_id;
        if (cell.started && cell.actor) {
            cell.actor->cancel_all_owned_schedules();
            cell.actor->on_stop();
        }
    }
    actors_.clear();
}

ActorSystem::ActorCell* ActorSystem::find_cell(v2::actor::ActorId actor_id) noexcept {
    auto it = actors_.find(actor_id);
    if (it == actors_.end()) {
        return nullptr;
    }
    return &it->second;
}

void ActorSystem::enqueue_ready_actor(v2::actor::ActorId actor_id, ActorCell& cell) {
    if (cell.queued) {
        return;
    }
    ready_actors_.push_back(actor_id);
    cell.queued = true;
}

void ActorSystem::promote_scheduled_messages() {
    std::vector<ScheduledMessage> pending;
    pending.reserve(scheduled_messages_.size());

    for (auto& scheduled : scheduled_messages_) {
        if (scheduled.use_wall_clock) {
            if (scheduled.ready_at <= Clock::now()) {
                send(scheduled.message);
                if (scheduled.repeat_interval > Duration::zero()) {
                    ++scheduled.repetitions_fired;
                    if (scheduled.max_repetitions == 0 ||
                        scheduled.repetitions_fired < scheduled.max_repetitions) {
                        scheduled.ready_at += scheduled.repeat_interval;
                        pending.push_back(std::move(scheduled));
                    }
                }
            } else {
                pending.push_back(std::move(scheduled));
            }
            continue;
        }

        if (scheduled.ready_after_dispatch <= dispatch_round_) {
            send(std::move(scheduled.message));
        } else {
            pending.push_back(std::move(scheduled));
        }
    }

    scheduled_messages_ = std::move(pending);
}

}  // namespace v2::runtime
