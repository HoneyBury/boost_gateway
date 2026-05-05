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

    EXPECT_TRUE(dispatcher.register_handler(
        42,
        [promise](const std::shared_ptr<net::Session>&, std::string body) {
            promise->set_value(std::move(body));
        }));

    EXPECT_TRUE(dispatcher.dispatch(std::shared_ptr<net::Session>{}, 42, "business_payload"));

    pool.join();
    EXPECT_EQ(future.get(), "business_payload");
}

TEST(MessageDispatcherTest, RejectsDuplicateHandlerRegistration) {
    app::logging::init("project_tests");

    boost::asio::thread_pool pool(1);
    net::MessageDispatcher dispatcher(pool);

    EXPECT_TRUE(dispatcher.register_handler(7, [](const std::shared_ptr<net::Session>&, std::string) {}));
    EXPECT_FALSE(dispatcher.register_handler(7, [](const std::shared_ptr<net::Session>&, std::string) {}));
}
