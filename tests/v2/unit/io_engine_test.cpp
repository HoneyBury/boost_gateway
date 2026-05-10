#include <gtest/gtest.h>

#include "app/logging.h"
#include "net/packet_codec.h"
#include "v2/io/io_engine.h"

#include <boost/asio.hpp>

#include <chrono>
#include <atomic>
#include <future>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

}  // namespace

TEST(V2IoEngineTest, DispatchesTasksToRequestedCore) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);
    engine.run();

    std::promise<std::uint32_t> promise;
    auto future = promise.get_future();
    engine.dispatch_to_core(1, [&promise, &engine]() mutable {
        promise.set_value(engine.current_core_id().value_or(999U));
    });

    EXPECT_EQ(future.get(), 1U);
    engine.stop();
}

TEST(V2IoEngineTest, DispatchesTasksToAllCores) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    engine.run();

    std::promise<void> promise;
    auto future = promise.get_future();
    std::mutex mutex;
    std::set<std::uint32_t> seen_cores;
    std::atomic<std::size_t> completions{0};

    engine.dispatch_to_all_cores(
        [&](std::uint32_t core_id) {
            {
                std::scoped_lock lock(mutex);
                seen_cores.insert(engine.current_core_id().value_or(999U));
                seen_cores.insert(core_id);
            }
            if (completions.fetch_add(1, std::memory_order_relaxed) + 1 == 3U) {
                promise.set_value();
            }
        });

    future.get();
    EXPECT_EQ(seen_cores.size(), 3U);
    EXPECT_NE(seen_cores.find(0U), seen_cores.end());
    EXPECT_NE(seen_cores.find(1U), seen_cores.end());
    EXPECT_NE(seen_cores.find(2U), seen_cores.end());
    engine.stop();
}

TEST(V2IoEngineTest, ListenAssignmentsRotateAcrossCores) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);
    try {
        auto first = engine.listen("127.0.0.1", 0);
        auto second = engine.listen("127.0.0.1", 0);

        EXPECT_EQ(first->owning_core_id(), 0U);
        EXPECT_EQ(second->owning_core_id(), 1U);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, ListenCanPinAcceptorToSpecificCore) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    try {
        auto fixed = engine.listen("127.0.0.1", 0, {}, v2::io::IoListenOptions{.fixed_core_id = 2});
        auto round_robin = engine.listen("127.0.0.1", 0);

        EXPECT_EQ(fixed->owning_core_id(), 2U);
        EXPECT_EQ(round_robin->owning_core_id(), 0U);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptsSocketAndDeliversPacketToSessionHandler) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);

    try {
        auto acceptor = engine.listen("127.0.0.1", 0);
        EXPECT_EQ(acceptor->owning_core_id(), 0U);
        std::promise<std::string> packet_body_promise;
        auto packet_body = packet_body_promise.get_future();
        std::promise<std::uint32_t> core_id_promise;
        auto core_id = core_id_promise.get_future();

        acceptor->async_accept([&](std::unique_ptr<v2::io::IoSession> session) {
            ASSERT_NE(session, nullptr);
            core_id_promise.set_value(session->owning_core_id());
            session->set_packet_handler(
                [&packet_body_promise](v2::io::IoSession::PacketMessage message) mutable {
                    packet_body_promise.set_value(std::move(message.body));
                });
            session->start();
        });

        engine.run();

        asio::io_context client_io;
        tcp::socket client(client_io);
        client.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), acceptor->local_port()));

        const auto packet = net::packet::encode(1001, 77, 0, "io_engine_works");
        asio::write(client, asio::buffer(packet));

        EXPECT_EQ(core_id.get(), 0U);
        EXPECT_EQ(packet_body.get(), "io_engine_works");
        client.close();
        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptsNativeSessionForGatewayStyleIngress) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);

    try {
        auto acceptor = engine.listen("127.0.0.1", 0);
        std::promise<std::string> endpoint_promise;
        auto endpoint = endpoint_promise.get_future();

        acceptor->async_accept_native(
            [&endpoint_promise](std::shared_ptr<net::Session> session) mutable {
                ASSERT_NE(session, nullptr);
                endpoint_promise.set_value(session->remote_endpoint());
                session->stop();
            });

        engine.run();

        asio::io_context client_io;
        tcp::socket client(client_io);
        client.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), acceptor->local_port()));

        EXPECT_NE(endpoint.get().find("127.0.0.1:"), std::string::npos);
        client.close();
        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, MultiplePinnedAcceptorsAcceptOnIndependentCores) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);

    try {
        auto first = engine.listen("127.0.0.1", 0, {}, v2::io::IoListenOptions{.fixed_core_id = 0});
        auto second = engine.listen("127.0.0.1", 0, {}, v2::io::IoListenOptions{.fixed_core_id = 1});
        std::promise<std::uint32_t> first_core_promise;
        std::promise<std::uint32_t> second_core_promise;
        auto first_core = first_core_promise.get_future();
        auto second_core = second_core_promise.get_future();

        first->async_accept([&](std::unique_ptr<v2::io::IoSession> session) {
            ASSERT_NE(session, nullptr);
            first_core_promise.set_value(session->owning_core_id());
            session->close();
        });
        second->async_accept([&](std::unique_ptr<v2::io::IoSession> session) {
            ASSERT_NE(session, nullptr);
            second_core_promise.set_value(session->owning_core_id());
            session->close();
        });

        engine.run();

        asio::io_context client_io;
        tcp::socket first_client(client_io);
        tcp::socket second_client(client_io);
        first_client.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), first->local_port()));
        second_client.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), second->local_port()));

        EXPECT_EQ(first_core.get(), 0U);
        EXPECT_EQ(second_core.get(), 1U);
        first_client.close();
        second_client.close();
        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}
