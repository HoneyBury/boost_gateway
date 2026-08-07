#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "v2/ecs/parallel_system_executor.h"
#include "v2/ecs/world.h"

namespace v2::ecs {
namespace {

// ─── Test system helpers ───────────────────────────────────────────────

// A system that records when it ran and optionally sleeps.
class TestSystem : public System {
public:
    explicit TestSystem(std::string id,
                         std::chrono::milliseconds sleep_duration = {})
        : id_(std::move(id))
        , sleep_duration_(sleep_duration) {}

    void run(World& world, const FrameContext& ctx) override {
        (void)world;
        (void)ctx;
        ran_ = true;
        {
            std::lock_guard lock(run_order_mutex_);
            run_order_.push_back(id_);
        }
        if (sleep_duration_.count() > 0) {
            std::this_thread::sleep_for(sleep_duration_);
        }
    }

    [[nodiscard]] bool has_run() const noexcept { return ran_; }
    [[nodiscard]] const std::string& id() const noexcept { return id_; }

    // Shared run-order list across systems.
    static std::vector<std::string> run_order_;
    static std::mutex run_order_mutex_;
    static void reset_run_order() { run_order_.clear(); }

private:
    std::string id_;
    std::chrono::milliseconds sleep_duration_;
    bool ran_ = false;
};

class ThrowingSystem final : public System {
public:
    explicit ThrowingSystem(bool standard_exception)
        : standard_exception_(standard_exception) {}

    void run(World&, const FrameContext&) override {
        if (standard_exception_) {
            throw std::runtime_error("injected system failure");
        }
        throw 42;
    }

private:
    bool standard_exception_;
};

class NestedParallelSystem final : public System {
public:
    void run(World& world, const FrameContext& ctx) override {
        ParallelSystemExecutor nested;
        nested.add_system(
            std::make_unique<TestSystem>("nested_a"),
            SystemMetadata{.name = "nested_a"});
        nested.add_system(
            std::make_unique<TestSystem>("nested_b"),
            SystemMetadata{.name = "nested_b"});
        executed_ = nested.execute_all(world, ctx);
    }

    [[nodiscard]] std::size_t executed() const noexcept { return executed_; }

private:
    std::size_t executed_ = 0;
};

std::vector<std::string> TestSystem::run_order_;
std::mutex TestSystem::run_order_mutex_;

// ─── Test 1: Two independent systems run in parallel ───────────────────
// Verifies total wall-clock time is less than sequential execution.

TEST(ParallelSystemExecutorTest, IndependentSystemsRunInParallel) {
    TestSystem::reset_run_order();

    ParallelSystemExecutor executor;

    executor.add_system(
        std::make_unique<TestSystem>("slow_a", std::chrono::milliseconds(200)),
        SystemMetadata{.name = "slow_a"});

    executor.add_system(
        std::make_unique<TestSystem>("slow_b", std::chrono::milliseconds(200)),
        SystemMetadata{.name = "slow_b"});

    SimpleWorld world;
    FrameContext ctx;

    auto start = std::chrono::steady_clock::now();
    auto count = executor.execute_all(world, ctx);
    auto elapsed = std::chrono::steady_clock::now() - start;

    EXPECT_EQ(count, 2U);

    // Two 200ms systems running in parallel should take < 300ms total.
    // (Sequential would take ~400ms.)
    auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(elapsed).count();
    EXPECT_LT(elapsed_ms, 300) << "Parallel execution of two 200ms systems "
                                  "should complete in under 300ms";
}

// ─── Test 2: Systems with dependencies execute in correct order ────────

TEST(ParallelSystemExecutorTest, DependentSystemsRunInOrder) {
    TestSystem::reset_run_order();

    ParallelSystemExecutor executor;

    // System "b" depends on "a", system "c" depends on "b".
    executor.add_system(
        std::make_unique<TestSystem>("a"),
        SystemMetadata{.name = "a"});

    executor.add_system(
        std::make_unique<TestSystem>("b"),
        SystemMetadata{.name = "b", .dependencies = {"a"}});

    executor.add_system(
        std::make_unique<TestSystem>("c"),
        SystemMetadata{.name = "c", .dependencies = {"b"}});

    SimpleWorld world;
    FrameContext ctx;

    executor.execute_all(world, ctx);

    // Verify execution order: a, then b, then c.
    ASSERT_EQ(TestSystem::run_order_.size(), 3U);
    EXPECT_EQ(TestSystem::run_order_[0], "a");
    EXPECT_EQ(TestSystem::run_order_[1], "b");
    EXPECT_EQ(TestSystem::run_order_[2], "c");
}

// ─── Test 3: Empty system list does not crash ──────────────────────────

TEST(ParallelSystemExecutorTest, EmptyListDoesNotCrash) {
    ParallelSystemExecutor executor;

    SimpleWorld world;
    FrameContext ctx;

    EXPECT_NO_THROW({
        auto count = executor.execute_all(world, ctx);
        EXPECT_EQ(count, 0U);
    });
}

// ─── Test 4: Mixed dependencies and independent systems ────────────────
// Systems a1, a2 are independent of each other but both precede b.

TEST(ParallelSystemExecutorTest, MixedDependencies) {
    TestSystem::reset_run_order();

    ParallelSystemExecutor executor;

    executor.add_system(
        std::make_unique<TestSystem>("a2"),
        SystemMetadata{.name = "a2"});

    executor.add_system(
        std::make_unique<TestSystem>("b"),
        SystemMetadata{.name = "b", .dependencies = {"a1", "a2"}});

    executor.add_system(
        std::make_unique<TestSystem>("a1"),
        SystemMetadata{.name = "a1"});

    SimpleWorld world;
    FrameContext ctx;

    executor.execute_all(world, ctx);

    // Both a1 and a2 must precede b.
    ASSERT_EQ(TestSystem::run_order_.size(), 3U);
    auto pos_a1 = std::distance(TestSystem::run_order_.begin(),
                                 std::find(TestSystem::run_order_.begin(),
                                           TestSystem::run_order_.end(), "a1"));
    auto pos_a2 = std::distance(TestSystem::run_order_.begin(),
                                 std::find(TestSystem::run_order_.begin(),
                                           TestSystem::run_order_.end(), "a2"));
    auto pos_b  = std::distance(TestSystem::run_order_.begin(),
                                 std::find(TestSystem::run_order_.begin(),
                                           TestSystem::run_order_.end(), "b"));

    EXPECT_LT(pos_a1, pos_b);
    EXPECT_LT(pos_a2, pos_b);
}

// ─── Test 5: Sequential executor compatibility ─────────────────────────

TEST(ParallelSystemExecutorTest, SequentialExecutorBaseline) {
    TestSystem::reset_run_order();

    SequentialSystemExecutor executor;
    executor.add_system(
        std::make_unique<TestSystem>("seq1"),
        SystemMetadata{.name = "seq1"});
    executor.add_system(
        std::make_unique<TestSystem>("seq2"),
        SystemMetadata{.name = "seq2"});

    SimpleWorld world;
    FrameContext ctx;

    auto count = executor.execute_all(world, ctx);
    EXPECT_EQ(count, 2U);

    ASSERT_EQ(TestSystem::run_order_.size(), 2U);
    EXPECT_EQ(TestSystem::run_order_[0], "seq1");
    EXPECT_EQ(TestSystem::run_order_[1], "seq2");
}

// ─── Test 6: Cycle detection does not crash ────────────────────────────

TEST(ParallelSystemExecutorTest, CycleDoesNotCrash) {
    TestSystem::reset_run_order();

    ParallelSystemExecutor executor;

    // a depends on b, b depends on a → cycle.
    executor.add_system(
        std::make_unique<TestSystem>("a"),
        SystemMetadata{.name = "a", .dependencies = {"b"}});

    executor.add_system(
        std::make_unique<TestSystem>("b"),
        SystemMetadata{.name = "b", .dependencies = {"a"}});

    SimpleWorld world;
    FrameContext ctx;

    EXPECT_NO_THROW({
        auto count = executor.execute_all(world, ctx);
        // Both systems should still execute (cycle fallback).
        EXPECT_EQ(count, 2U);
    });
}

TEST(ParallelSystemExecutorTest, DependencyOrderIsStableAcrossFrames) {
    ParallelSystemExecutor executor;
    executor.add_system(
        std::make_unique<TestSystem>("a"),
        SystemMetadata{.name = "a"});
    executor.add_system(
        std::make_unique<TestSystem>("b"),
        SystemMetadata{.name = "b", .dependencies = {"a"}});
    executor.add_system(
        std::make_unique<TestSystem>("c"),
        SystemMetadata{.name = "c", .dependencies = {"b"}});

    SimpleWorld world;
    FrameContext ctx;
    for (std::size_t frame = 0; frame < 100; ++frame) {
        TestSystem::reset_run_order();
        EXPECT_EQ(executor.execute_all(world, ctx), 3U);
        EXPECT_EQ(TestSystem::run_order_, (std::vector<std::string>{"a", "b", "c"}));
    }
}

TEST(ParallelSystemExecutorTest, PersistentPoolDoesNotLoseCompletionWakeups) {
    ParallelSystemExecutor executor;
    executor.add_system(
        std::make_unique<TestSystem>("first"),
        SystemMetadata{.name = "first"});
    executor.add_system(
        std::make_unique<TestSystem>("second"),
        SystemMetadata{.name = "second"});
    executor.rebuild_stages();

    SimpleWorld world;
    FrameContext ctx;
    for (std::size_t frame = 0; frame < 10'000; ++frame) {
        ASSERT_EQ(executor.execute_all(world, ctx), 2U);
    }
}

TEST(ParallelSystemExecutorTest, NestedExecutionFromWorkerCannotExhaustPool) {
    ParallelSystemExecutor executor;
    auto nested = std::make_unique<NestedParallelSystem>();
    auto* nested_ptr = nested.get();
    executor.add_system(std::make_unique<TestSystem>("caller"),
                        SystemMetadata{.name = "caller"});
    executor.add_system(std::move(nested), SystemMetadata{.name = "nested"});

    SimpleWorld world;
    FrameContext ctx;
    EXPECT_EQ(executor.execute_all(world, ctx), 2U);
    EXPECT_EQ(nested_ptr->executed(), 2U);
}

TEST(ParallelSystemExecutorTest, ExceptionsDoNotSkipFollowingStages) {
    ParallelSystemExecutor executor;
    executor.add_system(
        std::make_unique<ThrowingSystem>(true),
        SystemMetadata{.name = "standard_failure"});
    executor.add_system(
        std::make_unique<ThrowingSystem>(false),
        SystemMetadata{.name = "unknown_failure"});
    executor.add_system(
        std::make_unique<TestSystem>("after_failures"),
        SystemMetadata{
            .name = "after_failures",
            .dependencies = {"standard_failure", "unknown_failure"},
        });

    TestSystem::reset_run_order();
    SimpleWorld world;
    FrameContext ctx;
    EXPECT_NO_THROW(EXPECT_EQ(executor.execute_all(world, ctx), 3U));
    EXPECT_EQ(TestSystem::run_order_, (std::vector<std::string>{"after_failures"}));
}

TEST(ParallelSystemExecutorTest, CycleFallbackIsReusableAcrossFrames) {
    ParallelSystemExecutor executor;
    executor.add_system(
        std::make_unique<TestSystem>("a"),
        SystemMetadata{.name = "a", .dependencies = {"b"}});
    executor.add_system(
        std::make_unique<TestSystem>("b"),
        SystemMetadata{.name = "b", .dependencies = {"a"}});

    SimpleWorld world;
    FrameContext ctx;
    for (std::size_t frame = 0; frame < 20; ++frame) {
        TestSystem::reset_run_order();
        EXPECT_EQ(executor.execute_all(world, ctx), 2U);
        EXPECT_EQ(TestSystem::run_order_.size(), 2U);
        EXPECT_NE(std::find(TestSystem::run_order_.begin(), TestSystem::run_order_.end(), "a"),
                  TestSystem::run_order_.end());
        EXPECT_NE(std::find(TestSystem::run_order_.begin(), TestSystem::run_order_.end(), "b"),
                  TestSystem::run_order_.end());
    }
}

}  // namespace
}  // namespace v2::ecs
