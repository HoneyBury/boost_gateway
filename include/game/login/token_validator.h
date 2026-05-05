#pragma once

#include <chrono>
#include <cstdint>
#include <filesystem>
#include <optional>
#include <string>
#include <unordered_map>

namespace game::login {

constexpr std::chrono::seconds kDefaultTokenTtl{86400};  // 24h

struct TokenValidationResult {
    bool ok = false;
    bool expired = false;
    std::string user_id;
    std::string display_name;
    std::chrono::system_clock::time_point expires_at;
};

class TokenValidator {
public:
    virtual ~TokenValidator() = default;
    [[nodiscard]] virtual TokenValidationResult validate(const std::string& user_id,
                                                         const std::string& token,
                                                         const std::optional<std::string>& display_name) const = 0;
};

class DevTokenValidator : public TokenValidator {
public:
    [[nodiscard]] TokenValidationResult validate(const std::string& user_id,
                                                 const std::string& token,
                                                 const std::optional<std::string>& display_name) const override;
};

class JsonFileTokenValidator : public TokenValidator {
public:
    struct UserRecord {
        std::string token;
        std::string display_name;
    };

    using UserMap = std::unordered_map<std::string, UserRecord>;

    explicit JsonFileTokenValidator(UserMap users);

    [[nodiscard]] static std::optional<JsonFileTokenValidator> load_from_file(const std::filesystem::path& path);

    [[nodiscard]] TokenValidationResult validate(const std::string& user_id,
                                                 const std::string& token,
                                                 const std::optional<std::string>& display_name) const override;

    [[nodiscard]] std::size_t user_count() const;

private:
    UserMap users_;
};

}  // namespace game::login
