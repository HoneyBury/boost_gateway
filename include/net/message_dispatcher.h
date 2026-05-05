#pragma once

#include "app/logging.h"
#include "net/session.h"

#include <boost/asio/post.hpp>
#include <boost/asio/thread_pool.hpp>

#include <cstdint>
#include <functional>
#include <mutex>
#include <shared_mutex>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>
#include <vector>

namespace net {

struct DispatchContext {
    std::shared_ptr<Session> session;
    std::uint16_t message_id = 0;
    std::uint32_t request_id = 0;
    std::int32_t error_code = 0;
    std::string body;
};

class MessageDispatcher {
public:
    using Handler = std::function<void(const DispatchContext&)>;
    using Middleware = std::function<bool(const DispatchContext&)>;

    explicit MessageDispatcher(boost::asio::thread_pool& business_pool)
        : business_pool_(business_pool) {}

    bool register_handler(std::uint16_t message_id, Handler handler) {
        std::unique_lock lock(mutex_);
        return handlers_.emplace(message_id, std::move(handler)).second;
    }

    bool unregister_handler(std::uint16_t message_id) {
        std::unique_lock lock(mutex_);
        return handlers_.erase(message_id) > 0;
    }

    [[nodiscard]] bool has_handler(std::uint16_t message_id) const {
        std::shared_lock lock(mutex_);
        return handlers_.find(message_id) != handlers_.end();
    }

    [[nodiscard]] std::size_t handler_count() const {
        std::shared_lock lock(mutex_);
        return handlers_.size();
    }

    void register_middleware(std::string name, Middleware middleware) {
        std::unique_lock lock(mutex_);
        middlewares_.push_back({std::move(name), std::move(middleware)});
    }

    [[nodiscard]] std::size_t middleware_count() const {
        std::shared_lock lock(mutex_);
        return middlewares_.size();
    }

    bool dispatch(const std::shared_ptr<Session>& session,
                  std::uint16_t message_id,
                  std::uint32_t request_id,
                  std::int32_t error_code,
                  std::string body) const {
        Handler handler;
        std::vector<MiddlewareEntry> middlewares;
        {
            std::shared_lock lock(mutex_);
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

            handler = it->second;
            middlewares = middlewares_;
        }

        DispatchContext context{
            .session = session,
            .message_id = message_id,
            .request_id = request_id,
            .error_code = error_code,
            .body = std::move(body),
        };

        boost::asio::post(business_pool_, [context = std::move(context),
                                           handler = std::move(handler),
                                           middlewares = std::move(middlewares)]() mutable {
            for (const auto& middleware_entry : middlewares) {
                if (!middleware_entry.middleware(context)) {
                    LOG_DEBUG("Message {} blocked by middleware {}",
                              context.message_id,
                              middleware_entry.name);
                    return;
                }
            }

            LOG_DEBUG("Dispatch message {} on business thread", context.message_id);
            handler(context);
        });
        return true;
    }

private:
    struct MiddlewareEntry {
        std::string name;
        Middleware middleware;
    };

    boost::asio::thread_pool& business_pool_;
    mutable std::shared_mutex mutex_;
    std::unordered_map<std::uint16_t, Handler> handlers_;
    std::vector<MiddlewareEntry> middlewares_;
};

}  // namespace net
