#pragma once

#include <utility>

#include "v2/actor/actor_ref.h"

namespace v2::actor {

}  // namespace v2::actor

namespace v2::runtime {

class ActorSystem;

}  // namespace v2::runtime

namespace v2::actor {

class Actor {
public:
    virtual ~Actor() = default;

    virtual void on_start() {}
    virtual void on_stop() {}
    virtual void on_message(Message&& message) = 0;

    ActorId id() const noexcept { return self_.actor_id(); }
    ActorRef self() const noexcept { return self_; }
    ActorRef parent() const noexcept { return parent_; }

protected:
    void tell(const ActorRef& target, Message message) const;

private:
    friend class v2::runtime::ActorSystem;

    void bind(ActorRef self_ref, ActorRef parent_ref) noexcept {
        self_ = self_ref;
        parent_ = parent_ref;
    }

    ActorRef self_;
    ActorRef parent_;
};

}  // namespace v2::actor
