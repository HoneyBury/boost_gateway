#pragma once

#include "net/message_dispatcher.h"

#include <cstdint>
#include <functional>
#include <vector>

namespace net {

// Convenience helper for services to declare and register handlers in bulk.
class HandlerRegistry {
public:
    using Handler = MessageDispatcher::Handler;

    HandlerRegistry& add(std::uint16_t message_id, Handler handler) {
        entries_.push_back({message_id, std::move(handler)});
        return *this;
    }

    void register_all(MessageDispatcher& dispatcher) const {
        for (const auto& entry : entries_) {
            dispatcher.register_handler(entry.message_id, entry.handler);
        }
    }

private:
    struct Entry {
        std::uint16_t message_id;
        Handler handler;
    };
    std::vector<Entry> entries_;
};

}  // namespace net
