#include <gtest/gtest.h>

#include <atomic>
#include <string>
#include <thread>
#include <vector>

#include "v2/memory/object_pool.h"

using v2::memory::ObjectPool;

// Test type — larger than a pointer
struct Widget {
    int x = 0;
    int y = 0;
    std::string name;
};

TEST(V2ObjectPoolTest, AcquireReturnsDefaultConstructed) {
    ObjectPool<Widget, 8> pool;
    Widget* w = pool.acquire();
    ASSERT_NE(w, nullptr);
    EXPECT_EQ(w->x, 0);
    EXPECT_EQ(w->y, 0);
    EXPECT_TRUE(w->name.empty());
    pool.release(w);
}

TEST(V2ObjectPoolTest, AcquireReusesReleasedObjects) {
    ObjectPool<Widget, 4> pool;
    Widget* a = pool.acquire();
    a->x = 42;
    pool.release(a);

    Widget* b = pool.acquire();
    // Same pointer reused, but placement-new reconstructs the object
    EXPECT_EQ(b, a);
    EXPECT_EQ(b->x, 0);  // default-constructed
    EXPECT_TRUE(b->name.empty());
    pool.release(b);
}

TEST(V2ObjectPoolTest, PrefillPrePopulatesPool) {
    ObjectPool<std::uint64_t, 8> pool;
    EXPECT_EQ(pool.available(), 0U);

    pool.prefill(16);
    EXPECT_GE(pool.available(), 16U);
}

TEST(V2ObjectPoolTest, TotalAllocatedGrowsInBlocks) {
    ObjectPool<std::uint64_t, 8> pool;
    EXPECT_EQ(pool.total_allocated(), 0U);

    (void)pool.acquire();  // First acquire triggers block allocation
    EXPECT_EQ(pool.total_allocated(), 8U);  // BlockSize=8

    (void)pool.acquire();
    EXPECT_EQ(pool.total_allocated(), 8U);  // Still same block
}

TEST(V2ObjectPoolTest, ReleaseNullIsNoOp) {
    ObjectPool<std::uint64_t, 8> pool;
    pool.release(nullptr);  // Should not crash
    EXPECT_EQ(pool.available(), 0U);
}

TEST(V2ObjectPoolTest, MultipleAcquireReleaseCycles) {
    ObjectPool<std::uint64_t, 4> pool;

    // Acquire 10, release 10, repeat
    for (int cycle = 0; cycle < 5; ++cycle) {
        std::vector<std::uint64_t*> ptrs;
        for (int i = 0; i < 10; ++i) {
            ptrs.push_back(pool.acquire());
            *ptrs.back() = static_cast<std::uint64_t>(i);
        }
        for (auto* p : ptrs) {
            pool.release(p);
        }
    }
    // Total allocated should stay small (just 1-2 blocks)
    EXPECT_LE(pool.total_allocated(), 12U);  // 3 blocks max with BlockSize=4
    SUCCEED();
}

TEST(V2ObjectPoolTest, ThreadSafetyBasic) {
    ObjectPool<std::uint64_t, 16> pool;
    pool.prefill(32);

    std::atomic<int> errors{0};
    auto worker = [&]() {
        for (std::uint64_t i = 0; i < 100; ++i) {
            std::uint64_t* p = pool.acquire();
            if (p == nullptr) {
                errors.fetch_add(1);
                continue;
            }
            *p = i;
            pool.release(p);
        }
    };

    std::thread t1(worker);
    std::thread t2(worker);
    std::thread t3(worker);
    t1.join();
    t2.join();
    t3.join();

    EXPECT_EQ(errors.load(), 0);
}
