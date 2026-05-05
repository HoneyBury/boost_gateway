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
    std::uint8_t flags = 0;
    std::uint64_t trace_id = 0;
    std::string body;
};

class MessageDispatcher {
public:
    using Handler = std::function<void(const DispatchContext&)>;
    using Middleware = std::function<bool(const DispatchContext&)>;

    explicit MessageDispatcher(boost::asio::thread_pool& business_pool)
        : business_pool_(business_pool) {}

    // Register a dedicated thread pool for a message ID range [min, max].
    // Handlers in this range are dispatched to the dedicated pool instead of the default.
    void set_thread_pool(std::uint16_t min_id, std::uint16_t max_id, boost::asio::thread_pool& pool) {
        std::unique_lock lock(mutex_);
        pool_ranges_.push_back({min_id, max_id, &pool});
    }

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
                  std::string body,
                  std::uint64_t trace_id = 0,
                  std::uint8_t flags = 0) const {
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
            .flags = flags,
            .trace_id = trace_id,
            .body = std::move(body),
        };

        // Route to dedicated pool if message falls in a registered range
        auto* target_pool = &business_pool_;
        {
            std::shared_lock lock(mutex_);
            for (const auto& range : pool_ranges_) {
                if (context.message_id >= range.min_id && context.message_id <= range.max_id) {
                    target_pool = range.pool;
                    break;
                }
            }
        }

        boost::asio::post(*target_pool, [context = std::move(context),
                                           handler = std::move(handler),
                                           middlewares = std::move(middlewares)]() mutable {
            for (const auto& middleware_entry : middlewares) {
                if (!middleware_entry.middleware(context)) {
                    LOG_DEBUG("Message {} blocked by middleware {} [trace={}]",
                              context.message_id,
                              middleware_entry.name,
                              context.trace_id);
                    return;
                }
            }

            LOG_DEBUG("Dispatch message {} on business thread [trace={}]",
                      context.message_id,
                      context.trace_id);
            handler(context);
        });
        return true;
    }

private:
    struct MiddlewareEntry {
        std::string name;
        Middleware middleware;
    };

    struct PoolRange {
        std::uint16_t min_id;
        std::uint16_t max_id;
        boost::asio::thread_pool* pool;
    };

    boost::asio::thread_pool& business_pool_;
    mutable std::shared_mutex mutex_;
    std::unordered_map<std::uint16_t, Handler> handlers_;
    std::vector<MiddlewareEntry> middlewares_;
    std::vector<PoolRange> pool_ranges_;
};

}  // namespace net
