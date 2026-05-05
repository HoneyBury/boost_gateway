#pragma once

#include <chrono>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>

namespace app::audit {

inline std::ofstream& audit_file() {
    static std::ofstream file("logs/audit.log", std::ios::app);
    static std::once_flag init_flag;
    std::call_once(init_flag, [] {
        std::filesystem::create_directories("logs");
    });
    return file;
}

inline std::mutex& audit_mutex() {
    static std::mutex mtx;
    return mtx;
}

inline std::string now_iso() {
    const auto now = std::chrono::system_clock::now();
    const auto time = std::chrono::system_clock::to_time_t(now);
    std::tm tm_buf{};
#ifdef _WIN32
    localtime_s(&tm_buf, &time);
#else
    localtime_r(&time, &tm_buf);
#endif
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d",
                  tm_buf.tm_year + 1900, tm_buf.tm_mon + 1, tm_buf.tm_mday,
                  tm_buf.tm_hour, tm_buf.tm_min, tm_buf.tm_sec);
    return buf;
}

inline void log(const std::string& event_type, const std::string& details) {
    const auto entry = "{\"ts\":\"" + now_iso() + "\",\"event\":\"" + event_type +
                       "\",\"details\":\"" + details + "\"}\n";
    std::scoped_lock lock(audit_mutex());
    auto& file = audit_file();
    if (file.is_open()) {
        file << entry;
        file.flush();
    }
}

}  // namespace app::audit

#define AUDIT_LOG(event, details) ::app::audit::log(event, details)
