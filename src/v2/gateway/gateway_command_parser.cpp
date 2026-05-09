#include "v2/gateway/gateway_command_parser.h"

#include "v2/gateway/battle_protocol_codec.h"

#include <sstream>
#include <vector>

namespace v2::gateway {

namespace {

std::vector<std::string> split(std::string_view body, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream{std::string(body)};
    std::string item;
    while (std::getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

}  // namespace

std::optional<ParsedLoginCommandBody> parse_login_command_body(std::string_view body) {
    const auto parts = split(body, '|');
    if (parts.empty()) {
        return std::nullopt;
    }

    ParsedLoginCommandBody parsed;
    parsed.user_id = parts.front();
    if (parts.size() >= 2) {
        parsed.token = parts[1];
    }
    if (parts.size() >= 3 && !parts[2].empty()) {
        parsed.display_name = parts[2];
    }
    return parsed;
}

bool validate_login_command_body(const ParsedLoginCommandBody& body) noexcept {
    return !body.user_id.empty();
}

std::optional<std::string> parse_room_id_body(std::string_view body) {
    if (!validate_room_id_body(body)) {
        return std::nullopt;
    }
    return std::string(body);
}

bool validate_room_id_body(std::string_view body) noexcept {
    return !body.empty();
}

std::optional<bool> parse_room_ready_body(std::string_view body) noexcept {
    if (body == "true") {
        return true;
    }
    if (body == "false") {
        return false;
    }
    return std::nullopt;
}

std::optional<ParsedBattleStartCommandBody> parse_battle_start_command_body(std::string_view body) {
    ParsedBattleStartCommandBody parsed;
    if (!body.empty()) {
        if (!validate_room_id_body(body)) {
            return std::nullopt;
        }
        parsed.room_id = std::string(body);
    }
    return parsed;
}

std::optional<ParsedBattleInputCommandBody> parse_battle_input_command_body(std::string_view body) {
    ParsedBattleInputCommandBody parsed;
    if (body.empty()) {
        return std::nullopt;
    }

    if (const auto finish_reason = parse_battle_finish_request(std::string(body)); finish_reason.has_value()) {
        parsed.is_finish_request = true;
        parsed.finish_reason = *finish_reason;
        parsed.input_data = std::string(body);
        return parsed;
    }

    parsed.input_data = std::string(body);
    return parsed;
}

bool validate_battle_input_command_body(const ParsedBattleInputCommandBody& body) noexcept {
    if (body.is_finish_request) {
        return !body.input_data.empty();
    }
    return !body.input_data.empty();
}

}  // namespace v2::gateway
