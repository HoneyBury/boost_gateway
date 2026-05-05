#include "app/logging.h"

#include <spdlog/async.h>
#include <spdlog/pattern_formatter.h>
#include <spdlog/sinks/basic_file_sink.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/spdlog.h>

#include <mutex>
#include <stdexcept>
#include <vector>

namespace app::logging {
namespace {

std::once_flag g_logging_once;
std::shared_ptr<spdlog::logger> g_logger;

spdlog::level::level_enum default_level() {
#if defined(NDEBUG)
    return spdlog::level::info;
#else
    return spdlog::level::trace;
#endif
}

}  // namespace

void init(std::string_view app_name, const std::filesystem::path& log_directory) {
    std::call_once(g_logging_once, [app_name, log_directory]() {
        std::filesystem::create_directories(log_directory);

        std::vector<spdlog::sink_ptr> sinks;
        auto console_sink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
        auto file_sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(
            (log_directory / std::string(app_name)).string() + ".log", true);

        sinks.push_back(console_sink);
        sinks.push_back(file_sink);

        g_logger = std::make_shared<spdlog::logger>(
            std::string(app_name), sinks.begin(), sinks.end());

        g_logger->set_level(default_level());
        g_logger->flush_on(spdlog::level::warn);
        g_logger->set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%n] [%^%l%$] [thread %t] %v");

        spdlog::set_default_logger(g_logger);
        spdlog::set_level(default_level());
        spdlog::flush_on(spdlog::level::warn);
    });
}

std::shared_ptr<spdlog::logger> get_logger() {
    if (!g_logger) {
        throw std::logic_error("logger is not initialized; call app::logging::init() first");
    }

    return g_logger;
}

}  // namespace app::logging
