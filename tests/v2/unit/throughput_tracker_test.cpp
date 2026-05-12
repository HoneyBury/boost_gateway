#include "v2/benchmark/throughput_tracker.h"

#include <gtest/gtest.h>

#include <thread>

namespace v2::benchmark {
namespace {

TEST(ThroughputTrackerTest, InitialStateIsZero) {
    ThroughputTracker t(2, 4);
    const auto snap = t.snapshot();
    EXPECT_EQ(snap.total_count, 0);
    EXPECT_EQ(snap.rate_per_second, 0.0);
    EXPECT_EQ(snap.window_seconds, 2);
}

TEST(ThroughputTrackerTest, RecordIncrementsCount) {
    ThroughputTracker t(2, 4);
    t.record();
    t.record(5);

    EXPECT_EQ(t.total_count(), 6);
    const auto snap = t.snapshot();
    EXPECT_EQ(snap.total_count, 6);
}

TEST(ThroughputTrackerTest, RateComputation) {
    ThroughputTracker t(2, 4);  // 2-second window

    // Record 100 messages — should show ~50/s rate
    for (int i = 0; i < 100; ++i) {
        t.record();
    }

    const auto snap = t.snapshot();
    EXPECT_EQ(snap.total_count, 100);
    EXPECT_GT(snap.rate_per_second, 0.0);
}

TEST(ThroughputTrackerTest, ResetClearsAll) {
    ThroughputTracker t(2, 4);
    t.record(10);
    t.reset();

    EXPECT_EQ(t.total_count(), 0);
    const auto snap = t.snapshot();
    EXPECT_EQ(snap.total_count, 0);
    EXPECT_EQ(snap.rate_per_second, 0.0);
}

TEST(ThroughputTrackerTest, WindowSlidingReducesRate) {
    ThroughputTracker t(1, 10);  // 1-second window, 10 buckets = 100ms each

    // Record and wait for the window to slide
    t.record(50);
    const auto snap1 = t.snapshot();
    EXPECT_GT(snap1.rate_per_second, 0.0);

    // Wait long enough for buckets to expire
    std::this_thread::sleep_for(std::chrono::milliseconds(1200));

    const auto snap2 = t.snapshot();
    EXPECT_LT(snap2.rate_per_second, snap1.rate_per_second);
}

TEST(ThroughputTrackerTest, LargeBurstDoesNotCrash) {
    ThroughputTracker t(5, 10);
    for (int i = 0; i < 10000; ++i) {
        t.record(100);
    }
    const auto snap = t.snapshot();
    EXPECT_EQ(snap.total_count, 10000 * 100);
    EXPECT_GT(snap.rate_per_second, 0.0);
}

}  // namespace
}  // namespace v2::benchmark
