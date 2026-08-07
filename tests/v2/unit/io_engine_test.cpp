#include <gtest/gtest.h>

#include "app/logging.h"
#include "net/packet_codec.h"
#include "v2/actor/actor.h"
#include "v2/actor/message.h"
#include "v2/io/io_engine.h"
#include "v2/io/mailbox.h"
#include "v2/runtime/actor_system.h"

#include <boost/asio.hpp>

#include <algorithm>
#include <atomic>
#include <barrier>
#include <chrono>
#include <future>
#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

namespace {

namespace asio = boost::asio;
using tcp = asio::ip::tcp;

struct BlockingMoveGate {
    std::atomic<bool> move_started{false};
    std::atomic<bool> allow_move{false};
};

struct BlockingMovePayload {
    BlockingMovePayload(std::shared_ptr<BlockingMoveGate> move_gate, int payload_value)
        : gate(std::move(move_gate)), value(payload_value) {}

    BlockingMovePayload(BlockingMovePayload&& other) noexcept
        : gate(std::move(other.gate)), value(other.value) {
        if (gate) {
            gate->move_started.store(true, std::memory_order_release);
            while (!gate->allow_move.load(std::memory_order_acquire)) {
                std::this_thread::yield();
            }
        }
    }

    BlockingMovePayload(const BlockingMovePayload&) = delete;
    BlockingMovePayload& operator=(const BlockingMovePayload&) = delete;
    BlockingMovePayload& operator=(BlockingMovePayload&&) = delete;

    std::shared_ptr<BlockingMoveGate> gate;
    int value = 0;
};

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

// ─── Accept Policy Tests ───────────────────────────────────────

TEST(V2IoEngineTest, AcceptPolicyRoundRobinDistributesAcrossCores) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    try {
        const auto opts = v2::io::IoListenOptions{
            .accept_policy = v2::io::AcceptPolicy::kRoundRobin,
        };
        auto a0 = engine.listen("127.0.0.1", 0, {}, opts);
        auto a1 = engine.listen("127.0.0.1", 0, {}, opts);
        auto a2 = engine.listen("127.0.0.1", 0, {}, opts);

        EXPECT_EQ(a0->owning_core_id(), 0U);
        EXPECT_EQ(a1->owning_core_id(), 1U);
        EXPECT_EQ(a2->owning_core_id(), 2U);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptPolicyLeastLoadedPicksIdleCore) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    try {
        // Register sessions on cores 0 and 1 — least loaded should pick core 2.
        engine.register_session(0);
        engine.register_session(0);
        engine.register_session(1);

        const auto opts = v2::io::IoListenOptions{
            .accept_policy = v2::io::AcceptPolicy::kLeastLoaded,
        };
        auto acceptor = engine.listen("127.0.0.1", 0, {}, opts);

        EXPECT_EQ(acceptor->owning_core_id(), 2U);
        EXPECT_EQ(acceptor->accept_policy(), v2::io::AcceptPolicy::kLeastLoaded);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptPolicyLeastLoadedBalancesAcrossCores) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    try {
        // Make core 1 the most loaded.
        engine.register_session(1);
        engine.register_session(1);
        engine.register_session(1);
        engine.register_session(0);  // core 0 has 1

        const auto opts = v2::io::IoListenOptions{
            .accept_policy = v2::io::AcceptPolicy::kLeastLoaded,
        };
        auto acceptor = engine.listen("127.0.0.1", 0, {}, opts);

        // core 2 has 0 sessions, core 0 has 1 → pick core 2.
        EXPECT_EQ(acceptor->owning_core_id(), 2U);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptPolicyFixedPinsToSpecifiedCore) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(4);
    try {
        const auto opts = v2::io::IoListenOptions{
            .fixed_core_id = 3,
            .accept_policy = v2::io::AcceptPolicy::kFixed,
        };
        auto acceptor = engine.listen("127.0.0.1", 0, {}, opts);

        EXPECT_EQ(acceptor->owning_core_id(), 3U);
        EXPECT_EQ(acceptor->accept_policy(), v2::io::AcceptPolicy::kFixed);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptPolicyFixedWithoutCoreIdFallsBackToRoundRobin) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);
    try {
        const auto opts = v2::io::IoListenOptions{
            .fixed_core_id = std::nullopt,
            .accept_policy = v2::io::AcceptPolicy::kFixed,
        };
        auto a0 = engine.listen("127.0.0.1", 0, {}, opts);
        auto a1 = engine.listen("127.0.0.1", 0, {}, opts);

        EXPECT_EQ(a0->owning_core_id(), 0U);
        EXPECT_EQ(a1->owning_core_id(), 1U);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, AcceptorExposesAcceptPolicy) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    try {
        auto rr = engine.listen("127.0.0.1", 0, {},
            v2::io::IoListenOptions{.accept_policy = v2::io::AcceptPolicy::kRoundRobin});
        auto ll = engine.listen("127.0.0.1", 0, {},
            v2::io::IoListenOptions{.accept_policy = v2::io::AcceptPolicy::kLeastLoaded});
        auto fixed = engine.listen("127.0.0.1", 0, {},
            v2::io::IoListenOptions{.fixed_core_id = 0, .accept_policy = v2::io::AcceptPolicy::kFixed});

        EXPECT_EQ(rr->accept_policy(), v2::io::AcceptPolicy::kRoundRobin);
        EXPECT_EQ(ll->accept_policy(), v2::io::AcceptPolicy::kLeastLoaded);
        EXPECT_EQ(fixed->accept_policy(), v2::io::AcceptPolicy::kFixed);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

// ─── Session Counting Tests ────────────────────────────────────

TEST(V2IoEngineTest, SessionRegistrationIncrementsAndDecrementsCount) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);

    EXPECT_EQ(engine.session_count(0), 0U);
    EXPECT_EQ(engine.session_count(1), 0U);

    engine.register_session(0);
    engine.register_session(0);
    engine.register_session(1);

    EXPECT_EQ(engine.session_count(0), 2U);
    EXPECT_EQ(engine.session_count(1), 1U);

    engine.unregister_session(0);
    EXPECT_EQ(engine.session_count(0), 1U);

    engine.unregister_session(0);
    EXPECT_EQ(engine.session_count(0), 0U);

    engine.unregister_session(1);
    EXPECT_EQ(engine.session_count(1), 0U);
}

TEST(V2IoEngineTest, UnregisterDoesNotUnderflow) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);

    EXPECT_EQ(engine.session_count(0), 0U);
    engine.unregister_session(0);  // Should not underflow or crash.
    EXPECT_EQ(engine.session_count(0), 0U);
}

TEST(V2IoEngineTest, SessionCountOutOfBoundsReturnsZero) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);

    EXPECT_EQ(engine.session_count(999), 0U);
}

TEST(V2IoEngineTest, IoSessionAutoRegistersSessionOnAccept) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);

    try {
        auto acceptor = engine.listen("127.0.0.1", 0);
        std::promise<void> done;
        auto done_future = done.get_future();

        acceptor->async_accept([&](std::unique_ptr<v2::io::IoSession> session) {
            ASSERT_NE(session, nullptr);
            // Session should already be registered by the constructor.
            EXPECT_EQ(engine.session_count(0), 1U);
            session->start();
            session->close();
            done.set_value();
        });

        engine.run();

        asio::io_context client_io;
        tcp::socket client(client_io);
        client.connect(tcp::endpoint(asio::ip::make_address("127.0.0.1"), acceptor->local_port()));
        client.close();

        done_future.wait();

        // Give the engine a moment to process the close/destruction.
        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

// ─── SPSC Queue Tests ──────────────────────────────────────────

TEST(V2SpscQueueTest, BasicEnqueueDequeue) {
    v2::io::SpscQueue<int> queue(4);

    EXPECT_TRUE(queue.empty());
    EXPECT_TRUE(queue.try_enqueue(42));
    EXPECT_FALSE(queue.empty());

    auto item = queue.try_dequeue();
    ASSERT_TRUE(item.has_value());
    EXPECT_EQ(*item, 42);
    EXPECT_TRUE(queue.empty());
}

TEST(V2SpscQueueTest, DrainEmptiesQueue) {
    v2::io::SpscQueue<int> queue(8);

    for (int i = 0; i < 5; ++i) {
        EXPECT_TRUE(queue.try_enqueue(i));
    }
    EXPECT_EQ(queue.size(), 5U);

    const auto items = queue.drain();
    EXPECT_EQ(items.size(), 5U);
    for (int i = 0; i < 5; ++i) {
        EXPECT_EQ(items[static_cast<std::size_t>(i)], i);
    }
    EXPECT_TRUE(queue.empty());
}

TEST(V2SpscQueueTest, CapacityBackpressure) {
    v2::io::SpscQueue<int> queue(4);  // capacity rounds up to 4

    // Fill the queue.
    for (int i = 0; i < 4; ++i) {
        EXPECT_TRUE(queue.try_enqueue(i));
    }
    // Next enqueue should fail.
    EXPECT_FALSE(queue.try_enqueue(99));
    EXPECT_EQ(queue.size(), 4U);

    // Dequeue one, then enqueue should succeed again.
    auto item = queue.try_dequeue();
    ASSERT_TRUE(item.has_value());
    EXPECT_EQ(*item, 0);

    EXPECT_TRUE(queue.try_enqueue(99));
}

TEST(V2SpscQueueTest, MoveOnlyPayloadPreserved) {
    v2::io::SpscQueue<std::unique_ptr<int>> queue(4);

    queue.try_enqueue(std::make_unique<int>(10));
    queue.try_enqueue(std::make_unique<int>(20));

    auto items = queue.drain();
    ASSERT_EQ(items.size(), 2U);
    EXPECT_EQ(*items[0], 10);
    EXPECT_EQ(*items[1], 20);
}

TEST(V2SpscQueueTest, StringPayloadPreserved) {
    v2::io::SpscQueue<std::string> queue(4);

    queue.try_enqueue("hello");
    queue.try_enqueue(std::string("world"));

    auto items = queue.drain();
    ASSERT_EQ(items.size(), 2U);
    EXPECT_EQ(items[0], "hello");
    EXPECT_EQ(items[1], "world");
}

TEST(V2SpscQueueTest, DequeueOnEmptyReturnsNullopt) {
    v2::io::SpscQueue<int> queue(4);

    EXPECT_FALSE(queue.try_dequeue().has_value());
}

TEST(V2SpscQueueTest, SingleProducerSingleConsumerThreadSafety) {
    v2::io::SpscQueue<int> queue(1024);

    constexpr int kItemCount = 10000;
    std::atomic<bool> producer_started{false};
    std::atomic<bool> consumer_started{false};

    std::thread producer([&]() {
        producer_started.store(true, std::memory_order_release);
        // Wait for consumer to be ready.
        while (!consumer_started.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
        for (int i = 0; i < kItemCount; ++i) {
            while (!queue.try_enqueue(i)) {
                std::this_thread::yield();
            }
        }
    });

    std::thread consumer([&]() {
        consumer_started.store(true, std::memory_order_release);
        while (!producer_started.load(std::memory_order_acquire)) {
            std::this_thread::yield();
        }
        int received = 0;
        int expected = 0;
        while (received < kItemCount) {
            auto item = queue.try_dequeue();
            if (item.has_value()) {
                EXPECT_EQ(*item, expected);
                ++expected;
                ++received;
            } else {
                std::this_thread::yield();
            }
        }
    });

    producer.join();
    consumer.join();
    EXPECT_TRUE(queue.empty());
}

// ─── MPSC Queue Tests ──────────────────────────────────────────

TEST(V2MpscQueueTest, MultiProducerFanInIsExactlyOnceAndPreservesProducerFifo) {
    constexpr std::size_t kProducers = 4;
    constexpr std::size_t kItemsPerProducer = 5000;
    constexpr std::size_t kTotalItems = kProducers * kItemsPerProducer;
    v2::io::MpscQueue<std::uint64_t> queue(1024);
    std::barrier start(static_cast<std::ptrdiff_t>(kProducers + 1));
    std::vector<std::thread> producers;
    producers.reserve(kProducers);

    for (std::size_t producer = 0; producer < kProducers; ++producer) {
        producers.emplace_back([&, producer]() {
            start.arrive_and_wait();
            for (std::size_t sequence = 0; sequence < kItemsPerProducer; ++sequence) {
                const auto value = (static_cast<std::uint64_t>(producer) << 32U) |
                                   static_cast<std::uint64_t>(sequence);
                while (!queue.try_enqueue(value)) {
                    std::this_thread::yield();
                }
            }
        });
    }

    std::vector<std::uint64_t> received;
    received.reserve(kTotalItems);
    start.arrive_and_wait();
    while (received.size() < kTotalItems) {
        if (auto item = queue.try_dequeue()) {
            received.push_back(*item);
        } else {
            std::this_thread::yield();
        }
    }
    for (auto& producer : producers) {
        producer.join();
    }

    std::vector<std::size_t> next_sequence(kProducers, 0);
    std::vector<bool> seen(kTotalItems, false);
    for (const auto value : received) {
        const auto producer = static_cast<std::size_t>(value >> 32U);
        const auto sequence = static_cast<std::size_t>(value & 0xffffffffULL);
        ASSERT_LT(producer, kProducers);
        ASSERT_LT(sequence, kItemsPerProducer);
        EXPECT_EQ(sequence, next_sequence[producer]++);
        const auto index = producer * kItemsPerProducer + sequence;
        EXPECT_FALSE(seen[index]);
        seen[index] = true;
    }
    EXPECT_TRUE(std::all_of(seen.begin(), seen.end(), [](bool value) { return value; }));
    EXPECT_TRUE(queue.empty());
}

TEST(V2MpscQueueTest, FullQueueDoesNotConsumeRejectedMoveOnlyItem) {
    v2::io::MpscQueue<std::unique_ptr<int>> queue(2);
    EXPECT_TRUE(queue.try_enqueue(std::make_unique<int>(1)));
    EXPECT_TRUE(queue.try_enqueue(std::make_unique<int>(2)));

    auto rejected = std::make_unique<int>(3);
    EXPECT_EQ(queue.try_enqueue_result(std::move(rejected)), v2::io::EnqueueResult::kFull);
    ASSERT_NE(rejected, nullptr);
    EXPECT_EQ(*rejected, 3);
    EXPECT_EQ(queue.size(), 2U);
}

TEST(V2MpscQueueTest, CloseRejectsNewItemsAndAllowsPendingDrain) {
    v2::io::MpscQueue<int> queue(4);
    EXPECT_TRUE(queue.try_enqueue(1));
    queue.close();

    int rejected = 2;
    EXPECT_EQ(queue.try_enqueue_result(std::move(rejected)), v2::io::EnqueueResult::kClosed);
    const auto drained = queue.drain();
    ASSERT_EQ(drained.size(), 1U);
    EXPECT_EQ(drained[0], 1);
    EXPECT_TRUE(queue.closed());
}

TEST(V2MpscQueueTest, CloseWaitsForReservedProducerBeforeReturning) {
    v2::io::MpscQueue<BlockingMovePayload> queue(4);
    auto gate = std::make_shared<BlockingMoveGate>();
    BlockingMovePayload payload(gate, 7);
    std::atomic<v2::io::EnqueueResult> enqueue_result{v2::io::EnqueueResult::kClosed};
    std::atomic<bool> close_returned{false};

    std::thread producer([&]() {
        enqueue_result.store(
            queue.try_enqueue_result(std::move(payload)),
            std::memory_order_release);
    });
    while (!gate->move_started.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }

    std::thread closer([&]() {
        queue.close();
        close_returned.store(true, std::memory_order_release);
    });
    while (!queue.closed()) {
        std::this_thread::yield();
    }

    EXPECT_FALSE(close_returned.load(std::memory_order_acquire));
    BlockingMovePayload late_payload(nullptr, 8);
    EXPECT_EQ(
        queue.try_enqueue_result(std::move(late_payload)),
        v2::io::EnqueueResult::kClosed);
    EXPECT_EQ(late_payload.value, 8);

    gate->allow_move.store(true, std::memory_order_release);
    producer.join();
    closer.join();

    EXPECT_EQ(
        enqueue_result.load(std::memory_order_acquire),
        v2::io::EnqueueResult::kSuccess);
    EXPECT_TRUE(close_returned.load(std::memory_order_acquire));
    auto drained = queue.drain();
    ASSERT_EQ(drained.size(), 1U);
    EXPECT_EQ(drained[0].value, 7);
}

// ─── Cross-Core Mailbox Tests ──────────────────────────────────

TEST(V2IoEngineTest, PostMailboxDeliversToCorrectCore) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);
    engine.run();

    v2::actor::Message msg;
    msg.header.kind = v2::actor::MessageKind::kUser;
    msg.header.trace_id = 42;
    msg.payload = std::string("cross-core-test");

    EXPECT_TRUE(engine.post_mailbox(1, std::move(msg)));
    // Messages posted to core 1 should not appear in core 0's mailbox.
    const auto core0_drained = engine.drain_mailbox(0);
    EXPECT_TRUE(core0_drained.empty());

    const auto core1_drained = engine.drain_mailbox(1);
    ASSERT_EQ(core1_drained.size(), 1U);
    EXPECT_EQ(core1_drained[0].header.trace_id, 42U);

    engine.stop();
}

TEST(V2IoEngineTest, DrainMailboxReturnsAllPending) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    engine.run();

    for (int i = 0; i < 5; ++i) {
        v2::actor::Message msg;
        msg.header.trace_id = static_cast<std::uint64_t>(i);
        msg.payload = std::string("msg-") + std::to_string(i);
        EXPECT_TRUE(engine.post_mailbox(0, std::move(msg)));
    }

    const auto drained = engine.drain_mailbox(0);
    ASSERT_EQ(drained.size(), 5U);
    for (std::size_t i = 0; i < 5; ++i) {
        EXPECT_EQ(drained[i].header.trace_id, static_cast<std::uint64_t>(i));
    }
    // Second drain should be empty.
    EXPECT_TRUE(engine.drain_mailbox(0).empty());

    engine.stop();
}

TEST(V2IoEngineTest, PostMailboxOutOfBoundsReturnsFalse) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    v2::actor::Message msg;
    msg.payload = std::string("nope");

    EXPECT_FALSE(engine.post_mailbox(999, std::move(msg)));
    ASSERT_TRUE(std::holds_alternative<std::string>(msg.payload));
    EXPECT_EQ(std::get<std::string>(msg.payload), "nope");
    EXPECT_EQ(engine.mailbox_metrics().rejected_invalid_core, 1U);
}

TEST(V2IoEngineTest, MailboxPreservesMessagePayload) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    engine.run();

    // Post a LoginRequestMsg via the variant payload.
    v2::actor::Message msg;
    msg.header.kind = v2::actor::MessageKind::kUser;
    msg.header.trace_id = 100;
    msg.header.request_id = 200;
    msg.payload = v2::player::LoginRequestMsg{
        .user_id = "player_one",
        .token = "secret",
        .display_name = "PlayerOne",
    };

    EXPECT_TRUE(engine.post_mailbox(0, std::move(msg)));

    const auto drained = engine.drain_mailbox(0);
    ASSERT_EQ(drained.size(), 1U);
    EXPECT_EQ(drained[0].header.trace_id, 100U);
    EXPECT_EQ(drained[0].header.request_id, 200U);

    ASSERT_TRUE(std::holds_alternative<v2::player::LoginRequestMsg>(drained[0].payload));
    const auto& login = std::get<v2::player::LoginRequestMsg>(drained[0].payload);
    EXPECT_EQ(login.user_id, "player_one");
    EXPECT_EQ(login.token, "secret");
    EXPECT_EQ(login.display_name, "PlayerOne");

    engine.stop();
}

TEST(V2IoEngineTest, PostMailboxFromDifferentThread) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);
    engine.run();

    std::promise<void> producer_done;
    auto producer_future = producer_done.get_future();

    std::thread producer([&engine, &producer_done]() {
        for (int i = 0; i < 100; ++i) {
            v2::actor::Message msg;
            msg.header.trace_id = static_cast<std::uint64_t>(i);
            msg.payload = std::string("thread-msg-") + std::to_string(i);
            while (!engine.post_mailbox(0, std::move(msg))) {
                std::this_thread::yield();
            }
        }
        producer_done.set_value();
    });

    producer_future.wait();

    // Drain on core 0's thread.
    std::promise<std::size_t> drained_count_promise;
    auto drained_count = drained_count_promise.get_future();
    engine.dispatch_to_core(0,
        [&engine, &drained_count_promise]() mutable {
            const auto drained = engine.drain_mailbox(0);
            drained_count_promise.set_value(drained.size());
        });

    EXPECT_EQ(drained_count.get(), 100U);

    producer.join();
    engine.stop();
}

TEST(V2IoEngineTest, MailboxMultiProducerFanInIsExactlyOnceAndProducerFifo) {
    app::logging::init("project_tests");

    constexpr std::size_t kProducers = 4;
    constexpr std::size_t kItemsPerProducer = 2000;
    constexpr std::size_t kTotalItems = kProducers * kItemsPerProducer;
    v2::io::AsioIoEngine engine(2);
    engine.run();

    std::barrier start(static_cast<std::ptrdiff_t>(kProducers + 1));
    std::promise<std::vector<std::uint64_t>> drained_promise;
    auto drained_future = drained_promise.get_future();
    engine.dispatch_to_core(1, [&]() {
        std::vector<std::uint64_t> received;
        received.reserve(kTotalItems);
        start.arrive_and_wait();
        while (received.size() < kTotalItems) {
            auto batch = engine.drain_mailbox(1);
            for (const auto& message : batch) {
                received.push_back(message.header.trace_id);
            }
            if (batch.empty()) {
                std::this_thread::yield();
            }
        }
        drained_promise.set_value(std::move(received));
    });

    std::vector<std::thread> producers;
    producers.reserve(kProducers);
    for (std::size_t producer = 0; producer < kProducers; ++producer) {
        producers.emplace_back([&, producer]() {
            start.arrive_and_wait();
            for (std::size_t sequence = 0; sequence < kItemsPerProducer; ++sequence) {
                v2::actor::Message message;
                message.header.trace_id = (static_cast<std::uint64_t>(producer) << 32U) |
                                          static_cast<std::uint64_t>(sequence);
                message.payload = std::string("fan-in");
                while (!engine.post_mailbox(1, std::move(message))) {
                    std::this_thread::yield();
                }
            }
        });
    }

    const auto received = drained_future.get();
    for (auto& producer : producers) {
        producer.join();
    }
    engine.stop();

    ASSERT_EQ(received.size(), kTotalItems);
    std::vector<std::size_t> next_sequence(kProducers, 0);
    std::vector<bool> seen(kTotalItems, false);
    for (const auto value : received) {
        const auto producer = static_cast<std::size_t>(value >> 32U);
        const auto sequence = static_cast<std::size_t>(value & 0xffffffffULL);
        ASSERT_LT(producer, kProducers);
        ASSERT_LT(sequence, kItemsPerProducer);
        EXPECT_EQ(sequence, next_sequence[producer]++);
        const auto index = producer * kItemsPerProducer + sequence;
        EXPECT_FALSE(seen[index]);
        seen[index] = true;
    }
    EXPECT_TRUE(std::all_of(seen.begin(), seen.end(), [](bool value) { return value; }));
    EXPECT_EQ(engine.mailbox_metrics().accepted, kTotalItems);
}

TEST(V2IoEngineTest, MailboxOverflowIsAccountedAndDoesNotConsumeMessage) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    for (std::size_t i = 0; i < 1024; ++i) {
        v2::actor::Message message;
        message.header.trace_id = i;
        EXPECT_TRUE(engine.post_mailbox(0, std::move(message)));
    }

    v2::actor::Message rejected;
    rejected.payload = std::string("retain-on-overflow");
    EXPECT_FALSE(engine.post_mailbox(0, std::move(rejected)));
    ASSERT_TRUE(std::holds_alternative<std::string>(rejected.payload));
    EXPECT_EQ(std::get<std::string>(rejected.payload), "retain-on-overflow");

    const auto metrics = engine.mailbox_metrics();
    EXPECT_EQ(metrics.accepted, 1024U);
    EXPECT_EQ(metrics.rejected_full, 1U);
    EXPECT_EQ(metrics.rejected_closed, 0U);
    EXPECT_EQ(engine.drain_mailbox(0).size(), 1024U);
}

TEST(V2IoEngineTest, MailboxStopClosesAndPreservesPreviouslyAcceptedMessages) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    engine.run();
    v2::actor::Message accepted;
    accepted.header.trace_id = 41;
    EXPECT_TRUE(engine.post_mailbox(0, std::move(accepted)));
    engine.stop();

    v2::actor::Message rejected;
    rejected.header.trace_id = 42;
    EXPECT_FALSE(engine.post_mailbox(0, std::move(rejected)));
    EXPECT_EQ(rejected.header.trace_id, 42U);

    const auto drained = engine.drain_mailbox(0);
    ASSERT_EQ(drained.size(), 1U);
    EXPECT_EQ(drained[0].header.trace_id, 41U);
    const auto metrics = engine.mailbox_metrics();
    EXPECT_EQ(metrics.accepted, 1U);
    EXPECT_EQ(metrics.rejected_closed, 1U);
}

// ─── SO_REUSEPORT Tests ─────────────────────────────────────────

TEST(V2IoEngineTest, MultiAcceptorCreatedWhenReusePortSet) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    try {
        const auto opts = v2::io::IoListenOptions{
            .reuse_port = true,
        };
        auto acceptor = engine.listen("127.0.0.1", 0, {}, opts);

        ASSERT_NE(acceptor, nullptr);
        EXPECT_TRUE(acceptor->is_multi_core());

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, MultiAcceptorHasPortOnAllCores) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(2);
    try {
        const auto opts = v2::io::IoListenOptions{
            .reuse_port = true,
        };
        auto acceptor = engine.listen("127.0.0.1", 0, {}, opts);

        ASSERT_NE(acceptor, nullptr);
        EXPECT_TRUE(acceptor->is_multi_core());
        EXPECT_NE(acceptor->local_port(), 0U);

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, SingleAcceptorWhenReusePortFalse) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(3);
    try {
        auto acceptor = engine.listen("127.0.0.1", 0);
        ASSERT_NE(acceptor, nullptr);
        EXPECT_FALSE(acceptor->is_multi_core());

        engine.stop();
    } catch (const std::exception& ex) {
        engine.stop();
        GTEST_SKIP() << "socket bind unavailable in this environment: " << ex.what();
    }
}

TEST(V2IoEngineTest, SetActorSystemIsAccessibleForMailboxDrain) {
    app::logging::init("project_tests");

    v2::io::AsioIoEngine engine(1);
    v2::runtime::ActorSystem actor_system;

    actor_system.set_io_engine(&engine);

    v2::actor::Message msg;
    msg.header.kind = v2::actor::MessageKind::kUser;
    msg.header.trace_id = 77;
    msg.payload = std::string("affinity-test");

    engine.run();
    engine.post_mailbox(0, std::move(msg));

    std::size_t drained = actor_system.drain_mailbox_and_dispatch(0);
    EXPECT_EQ(drained, 0U);  // No actors registered yet.

    engine.stop();
}

TEST(V2IoEngineTest, ActorAffinityStoredOnCreation) {
    app::logging::init("project_tests");

    v2::runtime::ActorSystem actor_system;
    v2::io::AsioIoEngine engine(2);
    actor_system.set_io_engine(&engine);

    // Create a minimal actor with core affinity.
    class TestActor final : public v2::actor::Actor {
    public:
        void on_start() override {}
        void on_message(v2::actor::Message&&) override {}
        void on_stop() override {}
    };

    auto ref = actor_system.create_actor(
        std::make_unique<TestActor>(), {}, std::optional<std::uint32_t>(1));

    ASSERT_TRUE(ref.is_valid());
    EXPECT_TRUE(ref.core_id().has_value());
    EXPECT_EQ(*ref.core_id(), 1U);

    actor_system.shutdown();
}
