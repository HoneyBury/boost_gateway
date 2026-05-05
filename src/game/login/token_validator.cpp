#include "game/login/token_validator.h"

#include <nlohmann/json.hpp>

#include <fstream>

namespace game::login {
namespace {

using json = nlohmann::json;

}  // namespace

TokenValidationResult DevTokenValidator::validate(const std::string& user_id,
                                                  const std::string& token,
                                                  const std::optional<std::string>& display_name) const {
    if (user_id.empty() || token.empty()) {
        return {};
    }

    const auto now = std::chrono::system_clock::now();

    // "dev" token always passes but with short TTL
    if (token == "dev") {
        return TokenValidationResult{
            .ok = true, .expired = false,
            .user_id = user_id,
            .display_name = display_name.value_or(user_id),
            .expires_at = now + std::chrono::hours(1),
        };
    }

    if (token != "token:" + user_id) {
        return {};
    }

    return TokenValidationResult{
        .ok = true, .expired = false,
        .user_id = user_id,
        .display_name = display_name.value_or(user_id),
        .expires_at = now + kDefaultTokenTtl,
    };
}

JsonFileTokenValidator::JsonFileTokenValidator(UserMap users) : users_(std::move(users)) {}

std::optional<JsonFileTokenValidator> JsonFileTokenValidator::load_from_file(const std::filesystem::path& path) {
    std::ifstream input(path);
    if (!input.is_open()) {
        return std::nullopt;
    }

    json document;
    input >> document;
    if (!document.is_object() || !document.contains("users") || !document["users"].is_array()) {
        return std::nullopt;
    }

    UserMap users;
    for (const auto& entry : document["users"]) {
        if (!entry.is_object()) {
            continue;
        }

        const auto user_id = entry.value("user_id", "");
        const auto token = entry.value("token", "");
        if (user_id.empty() || token.empty()) {
            continue;
        }

        users.emplace(user_id,
                      UserRecord{
                          .token = token,
                          .display_name = entry.value("display_name", user_id),
                      });
    }

    if (users.empty()) {
        return std::nullopt;
    }

    return JsonFileTokenValidator(std::move(users));
}

TokenValidationResult JsonFileTokenValidator::validate(const std::string& user_id,
                                                       const std::string& token,
                                                       const std::optional<std::string>& display_name) const {
    const auto it = users_.find(user_id);
    if (it == users_.end() || token.empty() || token != it->second.token) {
        return {};
    }

    return TokenValidationResult{
        .ok = true, .expired = false,
        .user_id = user_id,
        .display_name = display_name.value_or(it->second.display_name),
        .expires_at = std::chrono::system_clock::now() + kDefaultTokenTtl,
    };
}

std::size_t JsonFileTokenValidator::user_count() const {
    return users_.size();
}

}  // namespace game::login
