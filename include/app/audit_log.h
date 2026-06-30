#pragma once

#include <chrono>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>

namespace app::audit {

inline std::string& audit_log_path() {
    static std::string path = "logs/audit.log";
    return path;
}

inline std::ofstream& audit_file() {
    static std::ofstream file(audit_log_path(), std::ios::app);
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
    localtime_r(&time, &tm_buf);
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%04d-%02d-%02dT%02d:%02d:%02d",
                  tm_buf.tm_year + 1900, tm_buf.tm_mon + 1, tm_buf.tm_mday,
                  tm_buf.tm_hour, tm_buf.tm_min, tm_buf.tm_sec);
    return buf;
}

// v3.4.0: Audit log rotation support
inline void set_audit_log_path(const std::string& path) {
    std::scoped_lock lock(audit_mutex());
    audit_log_path() = path;
    auto& file = audit_file();
    file.close();
    file.open(path, std::ios::app);
}

inline void rotate_audit_log() {
    std::scoped_lock lock(audit_mutex());
    auto& file = audit_file();
    file.close();
    auto old_path = audit_log_path();
    auto ts = now_iso();
    for (auto& c : ts) {
        if (c == ':') c = '-';
    }
    auto new_path = old_path + "." + ts;
    std::filesystem::rename(old_path, new_path);
    file.open(old_path, std::ios::app);
}

inline bool should_rotate(std::size_t max_size_bytes = 50 * 1024 * 1024) {
    std::error_code ec;
    auto size = std::filesystem::file_size(audit_log_path(), ec);
    return !ec && size >= max_size_bytes;
}

inline void log(const std::string& event_type, const std::string& details) {
    const auto entry = "{\"ts\":\"" + now_iso() + "\",\"event\":\"" + event_type +
                       "\",\"details\":\"" + details + "\"}\n";
    std::scoped_lock lock(audit_mutex());
    {
        std::error_code ec;
        auto size = std::filesystem::file_size(audit_log_path(), ec);
        if (!ec && size >= 50ULL * 1024 * 1024) {
            auto& file = audit_file();
            file.close();
            auto old_path = audit_log_path();
            auto ts = now_iso();
            for (auto& c : ts) {
                if (c == ':') c = '-';
            }
            auto new_path = old_path + "." + ts;
            std::filesystem::rename(old_path, new_path);
            file.open(old_path, std::ios::app);
        }
    }
    auto& file = audit_file();
    if (file.is_open()) {
        file << entry;
        file.flush();
    }
}

}  // namespace app::audit

#define AUDIT_LOG(event, details) ::app::audit::log(event, details)
