#pragma once

#include <atomic>
#include <filesystem>
#include <spdlog/spdlog.h>
#include <memory>
#include <string>
#include <string_view>

namespace app::logging {

void init(std::string_view app_name,
          const std::filesystem::path& log_directory = "logs");

std::shared_ptr<spdlog::logger> get_logger();

}  // namespace app::logging

#define LOG_TRACE(...) SPDLOG_LOGGER_TRACE(::app::logging::get_logger(), __VA_ARGS__)
#define LOG_DEBUG(...) SPDLOG_LOGGER_DEBUG(::app::logging::get_logger(), __VA_ARGS__)
#define LOG_INFO(...) SPDLOG_LOGGER_INFO(::app::logging::get_logger(), __VA_ARGS__)
#define LOG_WARN(...) SPDLOG_LOGGER_WARN(::app::logging::get_logger(), __VA_ARGS__)
#define LOG_ERROR(...) SPDLOG_LOGGER_ERROR(::app::logging::get_logger(), __VA_ARGS__)
#define LOG_CRITICAL(...) SPDLOG_LOGGER_CRITICAL(::app::logging::get_logger(), __VA_ARGS__)

// Sampled logging: only logs 1 out of every N calls, for high-frequency paths
#define LOG_INFO_SAMPLED(N, ...)                                   \
    do {                                                           \
        static std::atomic<std::size_t> _sample_counter{0};        \
        if (_sample_counter.fetch_add(1, std::memory_order_relaxed) % (N) == 0) { \
            SPDLOG_LOGGER_INFO(::app::logging::get_logger(), __VA_ARGS__); \
        }                                                          \
    } while (0)

#define LOG_DEBUG_SAMPLED(N, ...)                                  \
    do {                                                           \
        static std::atomic<std::size_t> _sample_counter{0};        \
        if (_sample_counter.fetch_add(1, std::memory_order_relaxed) % (N) == 0) { \
            SPDLOG_LOGGER_DEBUG(::app::logging::get_logger(), __VA_ARGS__); \
        }                                                          \
    } while (0)
