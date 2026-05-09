#include "v2/runtime/actor_system.h"

#include <algorithm>
#include <utility>

namespace v2::runtime {

ActorSystem::~ActorSystem() {
    shutdown();
}

v2::actor::ActorRef ActorSystem::create_actor(
    std::unique_ptr<v2::actor::Actor> actor,
    v2::actor::ActorRef parent) {
    if (!actor || shutting_down_) {
        return {};
    }

    const v2::actor::ActorId actor_id = next_actor_id_++;
    auto [it, inserted] = actors_.try_emplace(actor_id);
    if (!inserted) {
        return {};
    }

    auto self_ref = v2::actor::ActorRef(this, actor_id);
    it->second.actor = std::move(actor);
    it->second.actor->bind(self_ref, parent);
    it->second.actor->on_start();
    it->second.started = true;
    return self_ref;
}

void ActorSystem::send(v2::actor::Message message) {
    if (shutting_down_ || message.header.target_actor == 0) {
        return;
    }

    auto* cell = find_cell(message.header.target_actor);
    if (cell == nullptr) {
        return;
    }

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
    std::size_t dispatched = 0;
    promote_scheduled_messages();
    while (!ready_actors_.empty()) {
        const auto actor_id = ready_actors_.front();
        ready_actors_.pop_front();

        auto* cell = find_cell(actor_id);
        if (cell == nullptr) {
            continue;
        }
        cell->queued = false;

        while (!cell->mailbox.empty()) {
            auto message = std::move(cell->mailbox.front());
            cell->mailbox.pop_front();
            cell->actor->on_message(std::move(message));
            ++dispatched;
        }
    }
    ++dispatch_round_;
    return dispatched;
}

void ActorSystem::shutdown() {
    if (shutting_down_) {
        return;
    }
    shutting_down_ = true;
    ready_actors_.clear();
    scheduled_messages_.clear();

    for (auto& [actor_id, cell] : actors_) {
        (void)actor_id;
        if (cell.started && cell.actor) {
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
                if (scheduled.repeat_interval > Duration::zero()) {
                    send(scheduled.message);
                    scheduled.ready_at += scheduled.repeat_interval;
                    pending.push_back(std::move(scheduled));
                } else {
                    send(std::move(scheduled.message));
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
