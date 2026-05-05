#pragma once

#include "net/message_types.h"

#include <cstdint>
#include <cstring>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

namespace net::msg {

// ---------------------------------------------------------------------------
// Low-level binary I/O helpers
// ---------------------------------------------------------------------------
namespace detail {

inline void write_u16(std::string& out, std::uint16_t value) {
    out.push_back(static_cast<char>(value & 0xFF));
    out.push_back(static_cast<char>((value >> 8) & 0xFF));
}

inline void write_u32(std::string& out, std::uint32_t value) {
    out.push_back(static_cast<char>(value & 0xFF));
    out.push_back(static_cast<char>((value >> 8) & 0xFF));
    out.push_back(static_cast<char>((value >> 16) & 0xFF));
    out.push_back(static_cast<char>((value >> 24) & 0xFF));
}

inline void write_u64(std::string& out, std::uint64_t value) {
    write_u32(out, static_cast<std::uint32_t>(value & 0xFFFFFFFFULL));
    write_u32(out, static_cast<std::uint32_t>((value >> 32) & 0xFFFFFFFFULL));
}

inline void write_bool(std::string& out, bool value) {
    out.push_back(value ? '\x01' : '\x00');
}

inline void write_str(std::string& out, std::string_view value) {
    const auto len = static_cast<std::uint16_t>(value.size());
    write_u16(out, len);
    out.append(value);
}

template <typename T>
bool read_exact(std::string_view& data, T* dst, std::size_t size) {
    if (data.size() < size) return false;
    std::memcpy(dst, data.data(), size);
    data.remove_prefix(size);
    return true;
}

inline std::optional<std::uint16_t> read_u16(std::string_view& data) {
    std::uint16_t result = 0;
    std::uint8_t buf[2];
    if (!read_exact(data, buf, 2)) return std::nullopt;
    result = buf[0] | (static_cast<std::uint16_t>(buf[1]) << 8);
    return result;
}

inline std::optional<std::uint32_t> read_u32(std::string_view& data) {
    std::uint32_t result = 0;
    std::uint8_t buf[4];
    if (!read_exact(data, buf, 4)) return std::nullopt;
    result = buf[0] | (static_cast<std::uint32_t>(buf[1]) << 8) |
             (static_cast<std::uint32_t>(buf[2]) << 16) | (static_cast<std::uint32_t>(buf[3]) << 24);
    return result;
}

inline std::optional<std::uint64_t> read_u64(std::string_view& data) {
    const auto lo = read_u32(data);
    if (!lo) return std::nullopt;
    const auto hi = read_u32(data);
    if (!hi) return std::nullopt;
    return (static_cast<std::uint64_t>(*hi) << 32) | *lo;
}

inline std::optional<bool> read_bool(std::string_view& data) {
    if (data.empty()) return std::nullopt;
    const bool result = (data[0] != '\x00');
    data.remove_prefix(1);
    return result;
}

inline std::optional<std::string> read_str(std::string_view& data) {
    const auto len = read_u16(data);
    if (!len) return std::nullopt;
    if (data.size() < *len) return std::nullopt;
    std::string result(data.data(), *len);
    data.remove_prefix(*len);
    return result;
}

template <typename T>
std::optional<std::vector<T>> read_vec(std::string_view& data,
                                        std::optional<T>(*reader)(std::string_view&)) {
    const auto count = read_u16(data);
    if (!count) return std::nullopt;
    std::vector<T> result;
    result.reserve(*count);
    for (std::uint16_t i = 0; i < *count; ++i) {
        auto item = reader(data);
        if (!item) return std::nullopt;
        result.push_back(std::move(*item));
    }
    return result;
}

}  // namespace detail

// ---------------------------------------------------------------------------
// Serialize (message → binary string)
// ---------------------------------------------------------------------------

inline std::string serialize(const EchoRequest& msg) {
    std::string out;
    detail::write_str(out, msg.payload);
    return out;
}

inline std::string serialize(const EchoResponse& msg) {
    std::string out;
    detail::write_str(out, msg.payload);
    return out;
}

inline std::string serialize(const ErrorDetail& msg) {
    std::string out;
    detail::write_u32(out, static_cast<std::uint32_t>(msg.error_code));
    detail::write_str(out, msg.message);
    return out;
}

inline std::string serialize(const LoginRequest& msg) {
    std::string out;
    detail::write_str(out, msg.user_id);
    detail::write_str(out, msg.token);
    detail::write_str(out, msg.display_name);
    return out;
}

inline std::string serialize(const LoginResponse& msg) {
    std::string out;
    detail::write_bool(out, msg.ok);
    detail::write_str(out, msg.user_id);
    detail::write_str(out, msg.display_name);
    return out;
}

inline std::string serialize(const RoomCreateRequest& msg) {
    std::string out;
    detail::write_str(out, msg.room_id);
    return out;
}

inline std::string serialize(const RoomCreateResponse& msg) {
    std::string out;
    detail::write_str(out, msg.room_id);
    detail::write_str(out, msg.owner_id);
    return out;
}

inline std::string serialize(const RoomJoinRequest& msg) {
    return serialize(RoomCreateRequest{msg.room_id});
}

inline std::string serialize(const RoomJoinResponse& msg) {
    std::string out;
    detail::write_str(out, msg.room_id);
    return out;
}

inline std::string serialize(const RoomReadyRequest& msg) {
    std::string out;
    detail::write_bool(out, msg.ready);
    return out;
}

inline std::string serialize(const RoomReadyResponse& msg) {
    std::string out;
    detail::write_bool(out, msg.ready);
    detail::write_str(out, msg.user_id);
    return out;
}

inline std::string serialize(const RoomStatePush& msg) {
    std::string out;
    detail::write_str(out, msg.room_id);
    detail::write_str(out, msg.owner_id);
    detail::write_u16(out, static_cast<std::uint16_t>(msg.member_ids.size()));
    for (const auto& m : msg.member_ids) detail::write_str(out, m);
    detail::write_u16(out, static_cast<std::uint16_t>(msg.ready_ids.size()));
    for (const auto& r : msg.ready_ids) detail::write_str(out, r);
    return out;
}

inline std::string serialize(const BattleStartRequest& msg) {
    std::string out;
    detail::write_str(out, msg.room_id);
    return out;
}

inline std::string serialize(const BattleStartResponse& msg) {
    std::string out;
    detail::write_str(out, msg.battle_id);
    detail::write_str(out, msg.room_id);
    return out;
}

inline std::string serialize(const BattleInputRequest& msg) {
    std::string out;
    detail::write_str(out, msg.input_data);
    return out;
}

inline std::string serialize(const BattleInputResponse& msg) {
    std::string out;
    detail::write_u64(out, msg.input_seq);
    return out;
}

inline std::string serialize(const BattleInputPush& msg) {
    std::string out;
    detail::write_str(out, msg.user_id);
    detail::write_u64(out, msg.input_seq);
    detail::write_str(out, msg.input_data);
    return out;
}

inline std::string serialize(const BattleStatePush& msg) {
    std::string out;
    detail::write_str(out, msg.battle_id);
    detail::write_str(out, msg.room_id);
    detail::write_str(out, msg.state);
    return out;
}

// ---------------------------------------------------------------------------
// Deserialize (binary string → message)
// ---------------------------------------------------------------------------

inline std::optional<EchoRequest> deserialize_echo_request(std::string_view data) {
    EchoRequest msg;
    auto payload = detail::read_str(data);
    if (!payload) return std::nullopt;
    msg.payload = std::move(*payload);
    return msg;
}

inline std::optional<EchoResponse> deserialize_echo_response(std::string_view data) {
    EchoResponse msg;
    auto payload = detail::read_str(data);
    if (!payload) return std::nullopt;
    msg.payload = std::move(*payload);
    return msg;
}

inline std::optional<LoginRequest> deserialize_login_request(std::string_view data) {
    LoginRequest msg;
    auto uid = detail::read_str(data);
    auto tok = detail::read_str(data);
    auto dname = detail::read_str(data);
    if (!uid || !tok || !dname) return std::nullopt;
    msg.user_id = std::move(*uid);
    msg.token = std::move(*tok);
    msg.display_name = std::move(*dname);
    return msg;
}

inline std::optional<LoginResponse> deserialize_login_response(std::string_view data) {
    LoginResponse msg;
    auto ok = detail::read_bool(data);
    auto uid = detail::read_str(data);
    auto dname = detail::read_str(data);
    if (!ok || !uid || !dname) return std::nullopt;
    msg.ok = *ok;
    msg.user_id = std::move(*uid);
    msg.display_name = std::move(*dname);
    return msg;
}

inline std::optional<RoomCreateRequest> deserialize_room_create_request(std::string_view data) {
    auto rid = detail::read_str(data);
    if (!rid) return std::nullopt;
    return RoomCreateRequest{std::move(*rid)};
}

inline std::optional<RoomCreateResponse> deserialize_room_create_response(std::string_view data) {
    auto rid = detail::read_str(data);
    auto oid = detail::read_str(data);
    if (!rid || !oid) return std::nullopt;
    return RoomCreateResponse{std::move(*rid), std::move(*oid)};
}

inline std::optional<RoomJoinRequest> deserialize_room_join_request(std::string_view data) {
    auto rid = detail::read_str(data);
    if (!rid) return std::nullopt;
    return RoomJoinRequest{std::move(*rid)};
}

inline std::optional<RoomJoinResponse> deserialize_room_join_response(std::string_view data) {
    auto rid = detail::read_str(data);
    if (!rid) return std::nullopt;
    return RoomJoinResponse{std::move(*rid)};
}

inline std::optional<RoomReadyRequest> deserialize_room_ready_request(std::string_view data) {
    auto ready = detail::read_bool(data);
    if (!ready) return std::nullopt;
    return RoomReadyRequest{*ready};
}

inline std::optional<RoomReadyResponse> deserialize_room_ready_response(std::string_view data) {
    auto ready = detail::read_bool(data);
    auto uid = detail::read_str(data);
    if (!ready || !uid) return std::nullopt;
    return RoomReadyResponse{std::move(*uid), *ready};
}

inline std::optional<RoomStatePush> deserialize_room_state_push(std::string_view data) {
    RoomStatePush msg;
    auto rid = detail::read_str(data);
    auto oid = detail::read_str(data);
    auto members = detail::read_vec<std::string>(data, detail::read_str);
    auto readies = detail::read_vec<std::string>(data, detail::read_str);
    if (!rid || !oid || !members || !readies) return std::nullopt;
    msg.room_id = std::move(*rid);
    msg.owner_id = std::move(*oid);
    msg.member_ids = std::move(*members);
    msg.ready_ids = std::move(*readies);
    return msg;
}

inline std::optional<BattleStartRequest> deserialize_battle_start_request(std::string_view data) {
    auto rid = detail::read_str(data);
    if (!rid) return std::nullopt;
    return BattleStartRequest{std::move(*rid)};
}

inline std::optional<BattleStartResponse> deserialize_battle_start_response(std::string_view data) {
    auto bid = detail::read_str(data);
    auto rid = detail::read_str(data);
    if (!bid || !rid) return std::nullopt;
    return BattleStartResponse{std::move(*bid), std::move(*rid)};
}

inline std::optional<BattleInputRequest> deserialize_battle_input_request(std::string_view data) {
    auto input = detail::read_str(data);
    if (!input) return std::nullopt;
    return BattleInputRequest{std::move(*input)};
}

inline std::optional<BattleInputResponse> deserialize_battle_input_response(std::string_view data) {
    auto seq = detail::read_u64(data);
    if (!seq) return std::nullopt;
    return BattleInputResponse{*seq};
}

inline std::optional<BattleInputPush> deserialize_battle_input_push(std::string_view data) {
    auto uid = detail::read_str(data);
    auto seq = detail::read_u64(data);
    auto input = detail::read_str(data);
    if (!uid || !seq || !input) return std::nullopt;
    return BattleInputPush{std::move(*uid), *seq, std::move(*input)};
}

inline std::optional<BattleStatePush> deserialize_battle_state_push(std::string_view data) {
    auto bid = detail::read_str(data);
    auto rid = detail::read_str(data);
    auto state = detail::read_str(data);
    if (!bid || !rid || !state) return std::nullopt;
    return BattleStatePush{std::move(*bid), std::move(*rid), std::move(*state)};
}

}  // namespace net::msg
