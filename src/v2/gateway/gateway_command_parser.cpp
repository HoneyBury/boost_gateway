#include "v2/gateway/gateway_command_parser.h"

#include "v2/gateway/battle_protocol_codec.h"

#include <charconv>
#include <cstdlib>
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

std::optional<std::int64_t> parse_i64(std::string_view raw) noexcept {
    std::int64_t value = 0;
    const auto* begin = raw.data();
    const auto* end = raw.data() + raw.size();
    const auto [ptr, ec] = std::from_chars(begin, end, value);
    if (ec != std::errc{} || ptr != end) {
        return std::nullopt;
    }
    return value;
}

std::optional<std::size_t> parse_size(std::string_view raw) noexcept {
    std::size_t value = 0;
    const auto* begin = raw.data();
    const auto* end = raw.data() + raw.size();
    const auto [ptr, ec] = std::from_chars(begin, end, value);
    if (ec != std::errc{} || ptr != end) {
        return std::nullopt;
    }
    return value;
}

}  // namespace

std::optional<ParsedLoginCommandBody> parse_login_command_body(std::string_view body) {
    const auto parts = split(body, '|');
    if (parts.empty()) {
        return std::nullopt;
    }

    ParsedLoginCommandBody parsed{};
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
    if (body.empty()) {
        return ParsedBattleStartCommandBody{};
    }
    if (!validate_room_id_body(body)) {
        return std::nullopt;
    }
    return ParsedBattleStartCommandBody{.room_id = std::string(body)};
}

bool validate_battle_start_command_body(const ParsedBattleStartCommandBody& body) noexcept {
    return !body.room_id.has_value() || !body.room_id->empty();
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

    if (body.starts_with("score=") || body.starts_with("frame=")) {
        const auto colon_pos = body.find(':');
        if (colon_pos != std::string_view::npos) {
            const auto metadata = body.substr(0, colon_pos);
            for (const auto field : split(metadata, ',')) {
                if (field.starts_with("score=")) {
                    parsed.score = std::strtoll(field.substr(6).c_str(), nullptr, 10);
                } else if (field.starts_with("frame=")) {
                    parsed.submitted_frame = static_cast<std::uint32_t>(
                        std::strtoul(field.substr(6).c_str(), nullptr, 10));
                }
            }
            parsed.input_data = std::string(body.substr(colon_pos + 1));
        } else {
            parsed.input_data = std::string(body);
        }
    } else {
        parsed.input_data = std::string(body);
    }
    return parsed;
}

bool validate_battle_input_command_body(const ParsedBattleInputCommandBody& body) noexcept {
    if (body.is_finish_request) {
        return !body.input_data.empty();
    }
    return !body.input_data.empty();
}

std::optional<ParsedMatchCommandBody> parse_match_command_body(std::string_view body) {
    const auto parts = split(body, '|');
    if (parts.empty() || parts.front().empty()) {
        return std::nullopt;
    }

    ParsedMatchCommandBody parsed;
    parsed.user_id = parts[0];
    if (parts.size() >= 2 && !parts[1].empty()) {
        const auto mmr = parse_i64(parts[1]);
        if (!mmr.has_value()) {
            return std::nullopt;
        }
        parsed.mmr = *mmr;
    }
    if (parts.size() >= 3 && !parts[2].empty()) {
        parsed.mode = parts[2];
    }
    return parsed;
}

bool validate_match_command_body(const ParsedMatchCommandBody& body) noexcept {
    return !body.user_id.empty() &&
           (body.mode == "1v1" || body.mode == "2v2" || body.mode == "4v4");
}

std::optional<ParsedLeaderboardSubmitCommandBody>
parse_leaderboard_submit_command_body(std::string_view body) {
    const auto parts = split(body, '|');
    if (parts.size() < 3 || parts[0].empty()) {
        return std::nullopt;
    }

    const auto score = parse_i64(parts[2]);
    if (!score.has_value()) {
        return std::nullopt;
    }

    return ParsedLeaderboardSubmitCommandBody{
        .user_id = parts[0],
        .display_name = parts[1],
        .score = *score,
    };
}

bool validate_leaderboard_submit_command_body(
    const ParsedLeaderboardSubmitCommandBody& body) noexcept {
    return !body.user_id.empty();
}

std::optional<std::size_t> parse_leaderboard_top_command_body(std::string_view body) {
    if (body.empty()) {
        return std::size_t{10};
    }
    return parse_size(body);
}

std::optional<std::string> parse_leaderboard_rank_command_body(std::string_view body) {
    if (body.empty()) {
        return std::nullopt;
    }
    return std::string(body);
}

}  // namespace v2::gateway
