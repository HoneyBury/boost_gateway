#pragma once

#include "v2/actor/message.h"

namespace v2::actor {

}  // namespace v2::actor

namespace v2::runtime {

class ActorSystem;

}  // namespace v2::runtime

namespace v2::actor {

class ActorRef {
public:
    ActorRef() = default;

    ActorId actor_id() const noexcept { return actor_id_; }
    bool is_valid() const noexcept { return system_ != nullptr && actor_id_ != 0; }

    void tell(Message message) const;

private:
    friend class v2::runtime::ActorSystem;

    ActorRef(v2::runtime::ActorSystem* system, ActorId actor_id) noexcept
        : system_(system), actor_id_(actor_id) {}

    v2::runtime::ActorSystem* system_ = nullptr;
    ActorId actor_id_ = 0;
};

}  // namespace v2::actor
