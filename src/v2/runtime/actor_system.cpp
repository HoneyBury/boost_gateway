#include "v2/runtime/actor_system.h"

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

std::size_t ActorSystem::dispatch_all() {
    std::size_t dispatched = 0;
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
    return dispatched;
}

void ActorSystem::shutdown() {
    if (shutting_down_) {
        return;
    }
    shutting_down_ = true;
    ready_actors_.clear();

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

}  // namespace v2::runtime
