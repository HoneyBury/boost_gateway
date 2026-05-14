#pragma once

#include <cctype>
#include <cstdlib>
#include <optional>
#include <string>

namespace v2::config {

inline std::optional<std::string> get_env(const std::string& name) {
    const char* val = std::getenv(name.c_str());
    if (!val) return std::nullopt;
    return std::string(val);
}

inline std::string flag_to_env_name(const std::string& flag_name) {
    std::string result = "BOOST_";
    for (char c : flag_name) {
        result += static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
    }
    return result;
}

}  // namespace v2::config
