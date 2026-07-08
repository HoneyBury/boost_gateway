#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>
#include <thread>

#include "v2/config/config_watcher.h"

class V2ConfigWatcherTest : public ::testing::Test {
protected:
    void SetUp() override {
        static std::mt19937 rng{std::random_device{}()};
        auto suffix = std::to_string(std::uniform_int_distribution<int>(1, 999999)(rng));
        temp_dir_ = std::filesystem::temp_directory_path() / ("v2_config_watcher_test_" + suffix);
        std::filesystem::create_directories(temp_dir_);
        config_path_ = temp_dir_ / "test.json";
    }

    void TearDown() override {
        std::filesystem::remove_all(temp_dir_);
    }

    void write_json(const std::string& content) {
        std::ofstream file(config_path_);
        file << content;
        file.close();
    }

    void touch_file() {
        write_json("{\"value\": 0}");
    }

    std::filesystem::path temp_dir_;
    std::filesystem::path config_path_;
};

// ─── Callback fires on file change ──────────────────────────────────

TEST_F(V2ConfigWatcherTest, CallbackFiresOnFileChange) {
    touch_file();
    std::atomic<int> call_count{0};

    {
        v2::config::ConfigWatcher watcher(config_path_,
                                           [&]() { ++call_count; });
        watcher.start(std::chrono::milliseconds(50));

        // Wait for initial poll to capture baseline
        std::this_thread::sleep_for(std::chrono::milliseconds(100));

        // Ensure 1s+ has elapsed since initial file creation (APFS 1s timestamp granularity)
        std::this_thread::sleep_for(std::chrono::seconds(1));

        // Modify the file
        write_json("{\"value\": 1}");

        // Wait for watcher to detect the change
        for (int i = 0; i < 50 && call_count.load() < 1; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
    }

    EXPECT_GE(call_count.load(), 1);
}

// ─── No callback fires when file is unchanged ───────────────────────

TEST_F(V2ConfigWatcherTest, NoCallbackWhenFileUnchanged) {
    touch_file();
    std::atomic<int> call_count{0};

    {
        v2::config::ConfigWatcher watcher(config_path_,
                                           [&]() { ++call_count; });
        watcher.start(std::chrono::milliseconds(50));

        // Let a couple of poll cycles pass
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }

    EXPECT_EQ(call_count.load(), 0);
}

// ─── Stop prevents further callbacks ────────────────────────────────

TEST_F(V2ConfigWatcherTest, StopPreventsFurtherCallbacks) {
    touch_file();
    std::atomic<int> call_count{0};

    v2::config::ConfigWatcher watcher(config_path_,
                                       [&]() { ++call_count; });
    watcher.start(std::chrono::milliseconds(50));
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    watcher.stop();
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    write_json("{\"value\": 2}");
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    EXPECT_EQ(call_count.load(), 0);
}

// ─── Destroying watcher stops it cleanly ────────────────────────────

TEST_F(V2ConfigWatcherTest, DestroyStopsCleanly) {
    touch_file();
    {
        v2::config::ConfigWatcher watcher(config_path_,
                                           []() {});
        watcher.start();
    }
    // No crash, no hang — destruction stopped the thread
    SUCCEED();
}

// ─── Missing file on start doesn't crash ────────────────────────────

TEST_F(V2ConfigWatcherTest, MissingFileDoesNotCrash) {
    // config_path_ is not created — no touch_file()
    v2::config::ConfigWatcher watcher(config_path_, []() {});
    watcher.start(std::chrono::milliseconds(50));
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    // Should not crash — callback silently skips when file can't be read
    SUCCEED();
}

// ─── Stop without start is safe no-op ─────────────────────────────────

TEST_F(V2ConfigWatcherTest, StopWithoutStartIsSafeNoOp) {
    touch_file();
    v2::config::ConfigWatcher watcher(config_path_, []() {});
    watcher.stop();  // Should not crash or hang
    SUCCEED();
}

// ─── Double start is safe ─────────────────────────────────────────────

TEST_F(V2ConfigWatcherTest, DoubleStartIsSafe) {
    touch_file();
    std::atomic<int> call_count{0};
    v2::config::ConfigWatcher watcher(config_path_,
                                       [&]() { ++call_count; });
    watcher.start(std::chrono::milliseconds(50));
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    watcher.start(std::chrono::milliseconds(50));  // Second start
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    write_json("{\"value\": 3}");
    for (int i = 0; i < 50 && call_count.load() < 1; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    EXPECT_GE(call_count.load(), 1);
}

// ─── Rapid file changes within single poll interval ───────────────────

TEST_F(V2ConfigWatcherTest, RapidFileChangesDetected) {
    touch_file();
    std::atomic<int> call_count{0};

    v2::config::ConfigWatcher watcher(config_path_,
                                       [&]() { ++call_count; });
    // Use longer poll interval so we can fit multiple writes in one cycle
    watcher.start(std::chrono::milliseconds(800));

    // First write
    write_json("{\"value\": 10}");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    // Second write before poll fires
    write_json("{\"value\": 20}");
    std::this_thread::sleep_for(std::chrono::milliseconds(900));

    // At least one change should be detected (last_write_time updated)
    EXPECT_GE(call_count.load(), 1);
}
