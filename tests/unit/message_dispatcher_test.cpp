#include "app/logging.h"
#include "net/message_dispatcher.h"

#include <boost/asio/thread_pool.hpp>

#include <future>
#include <memory>

#include <gtest/gtest.h>

TEST(MessageDispatcherTest, DispatchesRegisteredHandlerOnBusinessPool) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);

    auto promise = std::make_shared<std::promise<std::string>>();
    auto future = promise->get_future();

    EXPECT_TRUE(dispatcher.register_handler(42, [promise](const net::DispatchContext& context) {
        promise->set_value(context.body);
    }));

    EXPECT_TRUE(dispatcher.dispatch(std::shared_ptr<net::Session>{}, 42, 1, 0, "business_payload"));

    pool.join();
    EXPECT_EQ(future.get(), "business_payload");
}

TEST(MessageDispatcherTest, RejectsDuplicateHandlerRegistration) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);

    EXPECT_TRUE(dispatcher.register_handler(7, [](const net::DispatchContext&) {}));
    EXPECT_FALSE(dispatcher.register_handler(7, [](const net::DispatchContext&) {}));
}

TEST(MessageDispatcherTest, MiddlewareCanBlockMessageBeforeHandlerRuns) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);

    auto promise = std::make_shared<std::promise<bool>>();
    auto future = promise->get_future();

    dispatcher.register_middleware("block_all", [promise](const net::DispatchContext&) {
        promise->set_value(true);
        return false;
    });

    EXPECT_TRUE(dispatcher.register_handler(99, [](const net::DispatchContext&) {
        FAIL() << "Handler should not run when middleware blocks the message.";
    }));

    EXPECT_TRUE(dispatcher.dispatch(std::shared_ptr<net::Session>{}, 99, 2, 0, "blocked_payload"));

    pool.join();
    EXPECT_TRUE(future.get());
    EXPECT_EQ(dispatcher.middleware_count(), 1U);
}
