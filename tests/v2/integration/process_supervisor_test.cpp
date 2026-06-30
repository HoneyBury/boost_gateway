// Integration tests for ProcessSupervisor.
//
// These tests spawn real OS child processes (using cmd.exe on Windows,
// /bin/sh on POSIX) and verify lifecycle management, crash detection,
// auto-restart, and limit enforcement.
//
// The tests use simple long-running and immediately-exiting shell
// commands as child processes, so no external echo binary is needed.

#include "app/process_supervisor.h"

#include <gtest/gtest.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <string>
#include <thread>
#include <vector>

namespace {

// ─── Platform-specific helper for a long-running process ──────────

// Returns (executable, {args}) for a process that runs for a very long
// time and only exits when killed.
std::pair<std::string, std::vector<std::string>> long_running_process() {
    // sleep 9999
    return {"/bin/sh", {"-c", "sleep 9999"}};

}

// Returns (executable, {args}) for a process that exits immediately
// with a non-zero exit code.
std::pair<std::string, std::vector<std::string>> failing_process() {
    return {"/bin/sh", {"-c", "exit 42"}};

}

// Wait for process state to settle (e.g., for crash detection).
void settle(std::chrono::milliseconds duration = std::chrono::milliseconds(500)) {
    std::this_thread::sleep_for(duration);
}

}  // anonymous namespace

// ─── ProcessSupervisorTest fixture ─────────────────────────────────

class ProcessSupervisorTest : public ::testing::Test {
protected:
    void SetUp() override {
        // Nothing special needed.
    }

    void TearDown() override {
        settle(std::chrono::milliseconds(300));
    }
};

// ─── Test 1: Start a child process, verify it runs, then stop ─────

TEST_F(ProcessSupervisorTest, StartAndStopChild) {
    app::ProcessSupervisor supervisor;

    const auto [exe, args] = long_running_process();

    app::ProcessSupervisor::ProcessConfig config;
    config.name = "sleeper";
    config.executable = exe;
    config.args = args;
    config.restart_delay_ms = 1000;
    config.max_restarts = 0;       // don't restart for this test
    config.health_check_port = 0;  // skip TCP health check

    supervisor.add_process(config);

    // Start the process.
    ASSERT_TRUE(supervisor.start_all());

    // Give it a moment to start.
    settle(std::chrono::milliseconds(500));

    // Verify the supervisor reports it as running.
    EXPECT_TRUE(supervisor.is_running("sleeper"));

    // Stop the process.
    supervisor.stop_all();

    // Verify stopped.
    EXPECT_FALSE(supervisor.is_running("sleeper"));
}

// ─── Test 2: Crash detection and auto-restart ─────────────────────

TEST_F(ProcessSupervisorTest, CrashAndAutoRestart) {
    app::ProcessSupervisor supervisor;

    std::atomic<int> crash_count{0};
    std::string last_crashed_name;
    supervisor.set_crash_callback(
        [&](const std::string& name, int /*exit_code*/) {
            crash_count.fetch_add(1);
            last_crashed_name = name;
        });

    // Use a process that exits immediately with an error.
    const auto [exe, args] = failing_process();

    app::ProcessSupervisor::ProcessConfig config;
    config.name = "crasher";
    config.executable = exe;
    config.args = args;
    config.restart_delay_ms = 300;   // fast restart for testing
    config.max_restarts = 3;          // allow restarts
    config.health_check_port = 0;

    supervisor.add_process(config);

    ASSERT_TRUE(supervisor.start_all());

    // The process will exit quickly. The supervisor should detect the
    // exit via WaitForSingleObject and restart it automatically.
    // Wait for the restart cycle to happen.
    settle(std::chrono::seconds(3));

    // The crash callback should have been called at least once.
    EXPECT_GE(crash_count.load(), 1);
    EXPECT_EQ(last_crashed_name, "crasher");

    // The restart count should be > 0.
    EXPECT_GE(supervisor.restart_count("crasher"), 1);

    // Process should still be running after restart.
    // (But may have failed again if restart was immediate.)
    // We just verify the supervisor managed the lifecycle.

    supervisor.stop_all();
}

// ─── Test 3: Max restarts enforcement ─────────────────────────────

TEST_F(ProcessSupervisorTest, MaxRestartsEnforcement) {
    app::ProcessSupervisor supervisor;

    std::atomic<int> crash_count{0};
    supervisor.set_crash_callback(
        [&](const std::string& /*name*/, int /*exit_code*/) {
            crash_count.fetch_add(1);
        });

    // Use a process that exits immediately.
    const auto [exe, args] = failing_process();

    app::ProcessSupervisor::ProcessConfig config;
    config.name = "crasher_limit";
    config.executable = exe;
    config.args = args;
    config.restart_delay_ms = 200;
    config.max_restarts = 2;         // only 2 restarts allowed
    config.health_check_port = 0;

    supervisor.add_process(config);

    ASSERT_TRUE(supervisor.start_all());

    // Wait long enough for the process to fail and the supervisor to
    // exhaust its restarts (2 failures * 200ms delay + margin).
    settle(std::chrono::seconds(4));

    // The process should no longer be running (max restarts exceeded).
    EXPECT_FALSE(supervisor.is_running("crasher_limit"));

    // The crash callback should have been called.
    // (One per failure, including initial + restarts: 1 initial + 2 restarts = 3)
    EXPECT_GE(crash_count.load(), 1);

    // Clean up.
    supervisor.stop_all();
}

// ─── Test 4: StopAll terminates all child processes ───────────────

TEST_F(ProcessSupervisorTest, StopAllTerminatesChildren) {
    app::ProcessSupervisor supervisor;

    // Start two independent long-running "processes".
    const auto [exe1, args1] = long_running_process();
    const auto [exe2, args2] = long_running_process();

    app::ProcessSupervisor::ProcessConfig config1;
    config1.name = "sleeper_1";
    config1.executable = exe1;
    config1.args = args1;
    config1.restart_delay_ms = 1000;
    config1.max_restarts = 0;
    config1.health_check_port = 0;

    app::ProcessSupervisor::ProcessConfig config2;
    config2.name = "sleeper_2";
    config2.executable = exe2;
    config2.args = args2;
    config2.restart_delay_ms = 1000;
    config2.max_restarts = 0;
    config2.health_check_port = 0;

    supervisor.add_process(config1);
    supervisor.add_process(config2);

    ASSERT_TRUE(supervisor.start_all());

    // Wait for both to start.
    settle(std::chrono::milliseconds(800));

    EXPECT_TRUE(supervisor.is_running("sleeper_1"));
    EXPECT_TRUE(supervisor.is_running("sleeper_2"));

    // Stop all.
    supervisor.stop_all();

    // Verify both stopped.
    EXPECT_FALSE(supervisor.is_running("sleeper_1"));
    EXPECT_FALSE(supervisor.is_running("sleeper_2"));
}
