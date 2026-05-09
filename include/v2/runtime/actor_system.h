#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <unordered_map>
#include <vector>

#include "v2/actor/actor.h"

namespace v2::runtime {

class ActorSystem {
public:
    using Clock = std::chrono::steady_clock;
    using Duration = Clock::duration;
    using TimePoint = Clock::time_point;
    using ScheduleId = std::uint64_t;

    ActorSystem() = default;
    ~ActorSystem();

    ActorSystem(const ActorSystem&) = delete;
    ActorSystem& operator=(const ActorSystem&) = delete;

    v2::actor::ActorRef create_actor(
        std::unique_ptr<v2::actor::Actor> actor,
        v2::actor::ActorRef parent = {});

    void send(v2::actor::Message message);
    void send_after(v2::actor::Message message, std::size_t dispatch_delay);
    void send_after(v2::actor::Message message, Duration delay);
    void send_at(v2::actor::Message message, TimePoint ready_at);
    [[nodiscard]] ScheduleId schedule_after(v2::actor::Message message, Duration delay);
    [[nodiscard]] ScheduleId schedule_every(v2::actor::Message message, Duration initial_delay, Duration interval);
    bool cancel_schedule(ScheduleId schedule_id) noexcept;
    std::size_t dispatch_all();
    void shutdown();

private:
    struct ActorCell {
        std::unique_ptr<v2::actor::Actor> actor;
        std::deque<v2::actor::Message> mailbox;
        bool started = false;
        bool queued = false;
    };

    ActorCell* find_cell(v2::actor::ActorId actor_id) noexcept;
    void enqueue_ready_actor(v2::actor::ActorId actor_id, ActorCell& cell);
    void promote_scheduled_messages();

    struct ScheduledMessage {
        ScheduleId schedule_id = 0;
        bool use_wall_clock = false;
        std::size_t ready_after_dispatch = 0;
        TimePoint ready_at{};
        Duration repeat_interval{};
        v2::actor::Message message;
    };

    std::unordered_map<v2::actor::ActorId, ActorCell> actors_;
    std::deque<v2::actor::ActorId> ready_actors_;
    std::vector<ScheduledMessage> scheduled_messages_;
    std::size_t dispatch_round_ = 0;
    ScheduleId next_schedule_id_ = 1;
    v2::actor::ActorId next_actor_id_ = 1;
    bool shutting_down_ = false;
};

}  // namespace v2::runtime
