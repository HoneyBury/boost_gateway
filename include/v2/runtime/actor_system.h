#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <memory>
#include <optional>
#include <unordered_map>
#include <vector>

#include "v2/actor/actor.h"

namespace v2::io {
class IoEngine;
}  // namespace v2::io

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

    v2::actor::ActorRef create_actor(
        std::unique_ptr<v2::actor::Actor> actor,
        v2::actor::ActorRef parent,
        std::optional<std::uint32_t> affinity_core);

    void send(v2::actor::Message message);
    void send_after(v2::actor::Message message, std::size_t dispatch_delay);
    void send_after(v2::actor::Message message, Duration delay);
    void send_at(v2::actor::Message message, TimePoint ready_at);
    [[nodiscard]] ScheduleId schedule_after(v2::actor::Message message, Duration delay);
    [[nodiscard]] ScheduleId schedule_after(v2::actor::Message message, TimePoint ready_at);
    [[nodiscard]] ScheduleId schedule_every(v2::actor::Message message, Duration initial_delay, Duration interval);
    [[nodiscard]] ScheduleId schedule_every(v2::actor::Message message,
                                            Duration initial_delay,
                                            Duration interval,
                                            std::size_t max_repetitions);
    bool cancel_schedule(ScheduleId schedule_id) noexcept;
    std::size_t dispatch_all();
    void shutdown();

    void set_io_engine(v2::io::IoEngine* io_engine);
    std::size_t drain_mailbox_and_dispatch(std::uint32_t core_id);
    [[nodiscard]] std::optional<std::uint32_t> dispatch_owner_core() const noexcept;

#ifndef NDEBUG
    [[nodiscard]] bool is_on_owner_core() const noexcept;
#endif

private:
    struct ActorCell {
        std::unique_ptr<v2::actor::Actor> actor;
        std::deque<v2::actor::Message> mailbox;
        bool started = false;
        bool queued = false;
        std::optional<std::uint32_t> core_id;
    };

    ActorCell* find_cell(v2::actor::ActorId actor_id) noexcept;
    void enqueue_ready_actor(v2::actor::ActorId actor_id, ActorCell& cell);
    void promote_scheduled_messages();
    std::size_t dispatch_ready(std::optional<std::uint32_t> owner_core);

    struct ScheduledMessage {
        ScheduleId schedule_id = 0;
        bool use_wall_clock = false;
        std::size_t ready_after_dispatch = 0;
        TimePoint ready_at{};
        Duration repeat_interval{};
        std::size_t max_repetitions = 0;
        std::size_t repetitions_fired = 0;
        v2::actor::Message message;
    };

    std::unordered_map<v2::actor::ActorId, ActorCell> actors_;
    std::deque<v2::actor::ActorId> ready_actors_;
    std::vector<ScheduledMessage> scheduled_messages_;
    std::size_t dispatch_round_ = 0;
    ScheduleId next_schedule_id_ = 1;
    v2::actor::ActorId next_actor_id_ = 1;
    bool shutting_down_ = false;
    v2::io::IoEngine* io_engine_ = nullptr;
    std::optional<std::uint32_t> dispatch_owner_core_;
};

class ScheduleHandle {
public:
    ScheduleHandle() = default;
    ScheduleHandle(ActorSystem* system, ActorSystem::ScheduleId schedule_id) noexcept;
    ~ScheduleHandle();

    ScheduleHandle(const ScheduleHandle&) = delete;
    ScheduleHandle& operator=(const ScheduleHandle&) = delete;
    ScheduleHandle(ScheduleHandle&& other) noexcept;
    ScheduleHandle& operator=(ScheduleHandle&& other) noexcept;

    [[nodiscard]] explicit operator bool() const noexcept { return system_ != nullptr && schedule_id_ != 0; }
    [[nodiscard]] ActorSystem::ScheduleId id() const noexcept { return schedule_id_; }
    bool release() noexcept;

private:
    ActorSystem* system_ = nullptr;
    ActorSystem::ScheduleId schedule_id_ = 0;
};

}  // namespace v2::runtime
