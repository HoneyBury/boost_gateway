#pragma once

#include <optional>
#include <string>

namespace game::login {

struct TokenValidationResult {
    bool ok = false;
    std::string user_id;
    std::string display_name;
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

}  // namespace game::login
