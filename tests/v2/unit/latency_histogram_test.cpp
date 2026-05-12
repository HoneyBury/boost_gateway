#include "v2/benchmark/latency_histogram.h"

#include <gtest/gtest.h>

namespace v2::benchmark {
namespace {

TEST(LatencyHistogramTest, EmptySnapshotIsZero) {
    LatencyHistogram h;
    const auto snap = h.snapshot();
    EXPECT_EQ(snap.total_count, 0);
    EXPECT_EQ(snap.min_ms, 0.0);
    EXPECT_EQ(snap.max_ms, 0.0);
    EXPECT_EQ(snap.p50_ms, 0.0);
    EXPECT_EQ(snap.p90_ms, 0.0);
    EXPECT_EQ(snap.p99_ms, 0.0);
}

TEST(LatencyHistogramTest, SingleRecord) {
    LatencyHistogram h;
    h.record_ms(3.0);
    const auto snap = h.snapshot();
    EXPECT_EQ(snap.total_count, 1);
    EXPECT_DOUBLE_EQ(snap.min_ms, 3.0);
    EXPECT_DOUBLE_EQ(snap.max_ms, 3.0);
}

TEST(LatencyHistogramTest, RecordUsConvertsToMs) {
    LatencyHistogram h;
    h.record_us(2500);  // 2.5 ms
    const auto snap = h.snapshot();
    EXPECT_EQ(snap.total_count, 1);
    EXPECT_NEAR(snap.min_ms, 2.5, 0.01);
}

TEST(LatencyHistogramTest, BucketingDistributesCorrectly) {
    LatencyHistogram h;
    // Record values that should land in different buckets
    h.record_ms(0.5);   // bucket 0: <= 1ms
    h.record_ms(1.5);   // bucket 1: <= 2ms
    h.record_ms(4.0);   // bucket 2: <= 5ms
    h.record_ms(8.0);   // bucket 3: <= 10ms
    h.record_ms(80.0);  // bucket 6: <= 100ms

    const auto snap = h.snapshot();
    EXPECT_EQ(snap.total_count, 5);
    EXPECT_EQ(snap.bucket_counts[0], 1);  // 0.5ms
    EXPECT_EQ(snap.bucket_counts[1], 1);  // 1.5ms
    EXPECT_EQ(snap.bucket_counts[2], 1);  // 4.0ms
    EXPECT_EQ(snap.bucket_counts[3], 1);  // 8.0ms
    EXPECT_EQ(snap.bucket_counts[6], 1);  // 80.0ms
}

TEST(LatencyHistogramTest, PercentileComputation) {
    LatencyHistogram h;
    // 49 at 3ms (bucket 2, <=5ms), 30 at 8ms (bucket 3, <=10ms),
    // 15 at 80ms (bucket 6, <=100ms), 6 at 800ms (bucket 9, <=1000ms)
    for (int i = 0; i < 49; ++i) h.record_ms(3.0);
    for (int i = 0; i < 30; ++i) h.record_ms(8.0);
    for (int i = 0; i < 15; ++i) h.record_ms(80.0);
    for (int i = 0; i < 6; ++i)  h.record_ms(800.0);

    const auto snap = h.snapshot();
    EXPECT_EQ(snap.total_count, 100);
    EXPECT_DOUBLE_EQ(snap.p50_ms, 10.0);    // 50th: in bucket 3 → upper bound 10ms
    EXPECT_DOUBLE_EQ(snap.p90_ms, 100.0);   // 90th: in bucket 6 → upper bound 100ms
    EXPECT_DOUBLE_EQ(snap.p99_ms, 1000.0);  // 99th: in bucket 9 → upper bound 1000ms
}

TEST(LatencyHistogramTest, DrainResetsCounters) {
    LatencyHistogram h;
    h.record_ms(5.0);
    h.record_ms(10.0);

    const auto snap1 = h.drain();
    EXPECT_EQ(snap1.total_count, 2);

    const auto snap2 = h.snapshot();
    EXPECT_EQ(snap2.total_count, 0);
    EXPECT_EQ(h.total_count(), 0);
}

TEST(LatencyHistogramTest, NegativeLatencyIgnored) {
    LatencyHistogram h;
    h.record_ms(-1.0);
    EXPECT_EQ(h.total_count(), 0);
}

}  // namespace
}  // namespace v2::benchmark
