#include "game/login/token_validator.h"

namespace game::login {

TokenValidationResult DevTokenValidator::validate(const std::string& user_id,
                                                  const std::string& token,
                                                  const std::optional<std::string>& display_name) const {
    if (user_id.empty() || token.empty()) {
        return {};
    }

    if (token != "token:" + user_id && token != "dev") {
        return {};
    }

    return TokenValidationResult{
        .ok = true,
        .user_id = user_id,
        .display_name = display_name.value_or(user_id),
    };
}

}  // namespace game::login
