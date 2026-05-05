#pragma once

#include "app/logging.h"
#include "net/session.h"

#include <boost/asio/post.hpp>
#include <boost/asio/thread_pool.hpp>

#include <cstdint>
#include <functional>
#include <string>
#include <unordered_map>

namespace net {

class MessageDispatcher {
public:
    using Handler = std::function<void(const std::shared_ptr<Session>&, std::string)>;

    explicit MessageDispatcher(boost::asio::thread_pool& business_pool)
        : business_pool_(business_pool) {}

    bool register_handler(std::uint16_t message_id, Handler handler) {
        return handlers_.emplace(message_id, std::move(handler)).second;
    }

    bool unregister_handler(std::uint16_t message_id) {
        return handlers_.erase(message_id) > 0;
    }

    [[nodiscard]] bool has_handler(std::uint16_t message_id) const {
        return handlers_.find(message_id) != handlers_.end();
    }

    [[nodiscard]] std::size_t handler_count() const {
        return handlers_.size();
    }

    bool dispatch(const std::shared_ptr<Session>& session,
                  std::uint16_t message_id,
                  std::string body) const {
        const auto it = handlers_.find(message_id);
        if (it == handlers_.end()) {
            if (session) {
                LOG_WARN("No handler for message id {} from {}",
                         message_id,
                         session->remote_endpoint());
            } else {
                LOG_WARN("No handler for message id {}", message_id);
            }
            return false;
        }

        auto handler = it->second;
        boost::asio::post(
            business_pool_,
            [session, message_id, body = std::move(body), handler = std::move(handler)]() mutable {
                LOG_DEBUG("Dispatch message {} on business thread", message_id);
                handler(session, std::move(body));
            });
        return true;
    }

private:
    boost::asio::thread_pool& business_pool_;
    std::unordered_map<std::uint16_t, Handler> handlers_;
};

}  // namespace net
