#include "app/crash_handler.h"

#include "app/logging.h"

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>

#include <unistd.h>

namespace app::crash {
namespace {

std::filesystem::path crash_dir() {
    return std::filesystem::path("runtime") / "crashes";
}

std::string timestamp_string() {
    const auto now = std::chrono::system_clock::now();
    const auto time = std::chrono::system_clock::to_time_t(now);
    std::tm tm_buf{};
    localtime_r(&time, &tm_buf);
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%04d%02d%02d_%02d%02d%02d",
                  tm_buf.tm_year + 1900, tm_buf.tm_mon + 1, tm_buf.tm_mday,
                  tm_buf.tm_hour, tm_buf.tm_min, tm_buf.tm_sec);
    return buf;
}

constexpr std::size_t kMaxCrashReports = 10;

void rotate_crash_reports() {
    try {
        const auto dir = crash_dir();
        if (!std::filesystem::exists(dir)) return;

        std::vector<std::filesystem::path> reports;
        for (const auto& entry : std::filesystem::directory_iterator(dir)) {
            if (entry.path().extension() == ".txt") {
                reports.push_back(entry.path());
            }
        }
        std::sort(reports.begin(), reports.end());
        while (reports.size() >= kMaxCrashReports) {
            std::filesystem::remove(reports.front());
            reports.erase(reports.begin());
        }
    } catch (...) {}
}

void write_crash_report(const std::string& signal_name) {
    try {
        rotate_crash_reports();
        std::filesystem::create_directories(crash_dir());
        const auto path = crash_dir() / ("crash_" + timestamp_string() + ".txt");
        std::ofstream output(path);
        if (output.is_open()) {
            output << "crash_signal: " << signal_name << "\n";
            output << "timestamp: " << timestamp_string() << "\n";
            output << "os: POSIX\n";
            char hostname[256]{};
            gethostname(hostname, sizeof(hostname));
            output << "hostname: " << hostname << "\n";
        }
    } catch (...) {
        // Last resort — nothing we can do
    }
}

void on_signal(int sig) {
    const auto* name = "UNKNOWN";
    switch (sig) {
    case SIGSEGV: name = "SIGSEGV"; break;
    case SIGABRT: name = "SIGABRT"; break;
    case SIGFPE:  name = "SIGFPE"; break;
    case SIGILL:  name = "SIGILL"; break;
    default: break;
    }

    LOG_CRITICAL("Fatal crash: {}", name);
    write_crash_report(name);
    std::_Exit(EXIT_FAILURE);
}

}  // namespace

void install_crash_handler() {
    std::signal(SIGSEGV, on_signal);
    std::signal(SIGABRT, on_signal);
    std::signal(SIGFPE, on_signal);
    std::signal(SIGILL, on_signal);
    LOG_INFO("Crash handler installed");
}

}  // namespace app::crash
