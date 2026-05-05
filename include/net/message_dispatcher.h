#pragma once

#include "app/logging.h"
#include "net/session.h"

#include <boost/asio/post.hpp>
#include <boost/asio/thread_pool.hpp>

#include <cstdint>
#include <string>
#include <unordered_map>

namespace net {

class MessageDispatcher {
public:
    using Handler = std::function<void(const std::shared_ptr<Session>&, std::string)>;

    explicit MessageDispatcher(boost::asio::thread_pool& business_pool)
        : business_pool_(business_pool) {}

    void register_handler(std::uint16_t message_id, Handler handler) {
        handlers_[message_id] = std::move(handler);
    }

    void dispatch(const std::shared_ptr<Session>& session,
                  std::uint16_t message_id,
                  std::string body) const {
        const auto it = handlers_.find(message_id);
        if (it == handlers_.end()) {
            LOG_WARN("No handler for message id {} from {}",
                     message_id,
                     session->remote_endpoint());
            return;
        }

        auto handler = it->second;
        boost::asio::post(business_pool_,
                          [session, message_id, body = std::move(body), handler = std::move(handler)]() mutable {
                              LOG_DEBUG("Dispatch message {} on business thread", message_id);
                              handler(session, std::move(body));
                          });
    }

private:
    boost::asio::thread_pool& business_pool_;
    std::unordered_map<std::uint16_t, Handler> handlers_;
};

}  // namespace net
