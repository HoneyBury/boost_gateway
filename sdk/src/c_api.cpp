// SDK v4.1.0: C API wrapper around SdkClient for cross-language binding.
#include "boost_gateway/sdk/c_api.h"
#include "boost_gateway/sdk/client.h"
#include "boost_gateway/sdk/version.h"
#include <chrono>
#include <cstring>
#include <exception>
#include <new>
#include <string>

using namespace boost_gateway::sdk;

struct gsdk_client_t {
    SdkClient client;
    void* push_ud = nullptr;
    void* dc_ud = nullptr;
    gsdk_push_callback_t push_cb = nullptr;
    gsdk_disconnect_callback_t dc_cb = nullptr;
};

static void copy_str(char* dst, size_t n, const std::string& src) {
    if (dst == nullptr || n == 0) {
        return;
    }
    auto len = src.size() < n-1 ? src.size() : n-1;
    std::memcpy(dst, src.c_str(), len); dst[len] = 0;
}

static void copy_cstr(char* dst, size_t n, const char* src) {
    copy_str(dst, n, src ? std::string(src) : std::string());
}

static std::string safe_string(const char* value) {
    return value ? std::string(value) : std::string();
}

static gsdk_login_result_t login_error(const char* message) {
    gsdk_login_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_room_result_t room_error(const char* message) {
    gsdk_room_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_battle_start_result_t battle_start_error(const char* message) {
    gsdk_battle_start_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_battle_input_result_t battle_input_error(const char* message) {
    gsdk_battle_input_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_match_result_t match_error(const char* message) {
    gsdk_match_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_leaderboard_submit_result_t leaderboard_submit_error(const char* message) {
    gsdk_leaderboard_submit_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_leaderboard_query_result_t leaderboard_query_error(const char* message) {
    gsdk_leaderboard_query_result_t out{};
    out.error_code = -1;
    copy_cstr(out.error_message, sizeof(out.error_message), message);
    return out;
}

static gsdk_echo_result_t echo_error(const char* message) {
    gsdk_echo_result_t out{};
    copy_cstr(out.body, sizeof(out.body), message);
    return out;
}

extern "C" {

const char* gsdk_version() { return BOOST_GATEWAY_SDK_VERSION; }

gsdk_client_t* gsdk_create() {
    try {
        return new gsdk_client_t;
    } catch (const std::exception&) {
        return nullptr;
    }
}

void gsdk_destroy(gsdk_client_t* c) { delete c; }

int gsdk_connect(gsdk_client_t* c, const char* host, uint16_t port, int32_t ms) {
    if (c == nullptr || host == nullptr || ms < 0) {
        return 0;
    }
    try {
        return c->client.connect(host, port, std::chrono::milliseconds(ms)) ? 1 : 0;
    } catch (const std::exception&) {
        return 0;
    }
}
void gsdk_disconnect(gsdk_client_t* c) {
    if (c == nullptr) {
        return;
    }
    try {
        c->client.disconnect();
    } catch (const std::exception&) {
    }
}
int gsdk_is_connected(const gsdk_client_t* c) {
    if (c == nullptr) {
        return 0;
    }
    try {
        return c->client.is_connected() ? 1 : 0;
    } catch (const std::exception&) {
        return 0;
    }
}

void gsdk_on_push(gsdk_client_t* c, gsdk_push_callback_t cb, void* ud) {
    if (c == nullptr) {
        return;
    }
    c->push_cb = cb; c->push_ud = ud;
    c->client.on_push([c](const PushMessage& m) { if(c->push_cb) c->push_cb(m.message_id, m.body.c_str(), c->push_ud); });
}
void gsdk_on_disconnect(gsdk_client_t* c, gsdk_disconnect_callback_t cb, void* ud) {
    if (c == nullptr) {
        return;
    }
    c->dc_cb = cb; c->dc_ud = ud;
    c->client.on_disconnect([c]() { if(c->dc_cb) c->dc_cb(c->dc_ud); });
}

void gsdk_start_heartbeat(gsdk_client_t* c, int32_t interval_seconds) {
    if (c == nullptr || interval_seconds <= 0) {
        return;
    }
    try {
        c->client.start_heartbeat(std::chrono::seconds(interval_seconds));
    } catch (const std::exception&) {
    }
}

void gsdk_stop_heartbeat(gsdk_client_t* c) {
    if (c == nullptr) {
        return;
    }
    try {
        c->client.stop_heartbeat();
    } catch (const std::exception&) {
    }
}

gsdk_login_result_t gsdk_login(gsdk_client_t* c, const char* uid, const char* tok, int32_t ms) {
    if (c == nullptr || uid == nullptr || tok == nullptr || ms < 0) {
        return login_error("invalid_argument");
    }
    LoginResult r;
    try {
        r = c->client.login(uid, tok, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return login_error(ex.what());
    }
    gsdk_login_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.user_id, 64, r.user_id); copy_str(out.display_name, 64, r.display_name); copy_str(out.error_message, 256, r.error_message);
    return out;
}

gsdk_room_result_t gsdk_create_room(gsdk_client_t* c, const char* rid, int32_t ms) {
    if (c == nullptr || rid == nullptr || ms < 0) {
        return room_error("invalid_argument");
    }
    RoomResult r;
    try {
        r = c->client.create_room(rid, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return room_error(ex.what());
    }
    gsdk_room_result_t out{}; out.ok = r.ok; out.error_code = r.error_code; out.member_count = r.member_count;
    copy_str(out.room_id, 64, r.room_id); copy_str(out.error_message, 256, r.error_message);
    return out;
}
gsdk_room_result_t gsdk_join_room(gsdk_client_t* c, const char* rid, int32_t ms) {
    if (c == nullptr || rid == nullptr || ms < 0) {
        return room_error("invalid_argument");
    }
    RoomResult r;
    try {
        r = c->client.join_room(rid, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return room_error(ex.what());
    }
    gsdk_room_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.room_id, 64, r.room_id); copy_str(out.error_message, 256, r.error_message);
    return out;
}
gsdk_room_result_t gsdk_leave_room(gsdk_client_t* c, const char* rid, int32_t ms) {
    if (c == nullptr || rid == nullptr || ms < 0) {
        return room_error("invalid_argument");
    }
    RoomResult r;
    try {
        r = c->client.leave_room(rid, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return room_error(ex.what());
    }
    gsdk_room_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.room_id, 64, r.room_id); copy_str(out.error_message, 256, r.error_message);
    return out;
}
gsdk_room_result_t gsdk_set_ready(gsdk_client_t* c, int ready, int32_t ms) {
    if (c == nullptr || ms < 0) {
        return room_error("invalid_argument");
    }
    RoomResult r;
    try {
        r = c->client.set_ready(ready != 0, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return room_error(ex.what());
    }
    gsdk_room_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.error_message, 256, r.error_message);
    return out;
}

gsdk_battle_start_result_t gsdk_start_battle(gsdk_client_t* c, const char* rid, int32_t ms) {
    if (c == nullptr || rid == nullptr || ms < 0) {
        return battle_start_error("invalid_argument");
    }
    BattleStartResult r;
    try {
        r = c->client.start_battle(rid, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return battle_start_error(ex.what());
    }
    gsdk_battle_start_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.battle_id, 64, r.battle_id); copy_str(out.error_message, 256, r.error_message);
    return out;
}
gsdk_battle_input_result_t gsdk_send_battle_input(gsdk_client_t* c, const char* data, int32_t ms) {
    if (c == nullptr || data == nullptr || ms < 0) {
        return battle_input_error("invalid_argument");
    }
    BattleInputResult r;
    try {
        r = c->client.send_battle_input(data, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return battle_input_error(ex.what());
    }
    gsdk_battle_input_result_t out{}; out.ok = r.ok; out.error_code = r.error_code; out.input_seq = r.input_seq;
    copy_str(out.error_message, 256, r.error_message);
    return out;
}

gsdk_match_result_t gsdk_match_join(gsdk_client_t* c, const char* user_id, int64_t mmr, const char* mode, int32_t ms) {
    if (c == nullptr || user_id == nullptr || mode == nullptr || ms < 0) {
        return match_error("invalid_argument");
    }
    MatchResult r;
    try {
        r = c->client.match_join(user_id, mmr, mode, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return match_error(ex.what());
    }
    gsdk_match_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.response_body, sizeof(out.response_body), r.response_body);
    copy_str(out.error_message, sizeof(out.error_message), r.error_message);
    return out;
}

gsdk_match_result_t gsdk_match_leave(gsdk_client_t* c, const char* user_id, const char* mode, int32_t ms) {
    if (c == nullptr || user_id == nullptr || mode == nullptr || ms < 0) {
        return match_error("invalid_argument");
    }
    MatchResult r;
    try {
        r = c->client.match_leave(user_id, mode, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return match_error(ex.what());
    }
    gsdk_match_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.response_body, sizeof(out.response_body), r.response_body);
    copy_str(out.error_message, sizeof(out.error_message), r.error_message);
    return out;
}

gsdk_match_result_t gsdk_match_status(gsdk_client_t* c, const char* user_id, const char* mode, int32_t ms) {
    if (c == nullptr || user_id == nullptr || mode == nullptr || ms < 0) {
        return match_error("invalid_argument");
    }
    MatchResult r;
    try {
        r = c->client.match_status(user_id, mode, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return match_error(ex.what());
    }
    gsdk_match_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.response_body, sizeof(out.response_body), r.response_body);
    copy_str(out.error_message, sizeof(out.error_message), r.error_message);
    return out;
}

gsdk_leaderboard_submit_result_t gsdk_leaderboard_submit(gsdk_client_t* c, const char* user_id, const char* display_name, int64_t score, int32_t ms) {
    if (c == nullptr || user_id == nullptr || display_name == nullptr || ms < 0) {
        return leaderboard_submit_error("invalid_argument");
    }
    LeaderboardSubmitResult r;
    try {
        r = c->client.leaderboard_submit(user_id, display_name, score, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return leaderboard_submit_error(ex.what());
    }
    gsdk_leaderboard_submit_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.response_body, sizeof(out.response_body), r.response_body);
    copy_str(out.error_message, sizeof(out.error_message), r.error_message);
    return out;
}

gsdk_leaderboard_query_result_t gsdk_leaderboard_top(gsdk_client_t* c, uint32_t k, int32_t ms) {
    if (c == nullptr || ms < 0) {
        return leaderboard_query_error("invalid_argument");
    }
    LeaderboardQueryResult r;
    try {
        r = c->client.leaderboard_top(k, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return leaderboard_query_error(ex.what());
    }
    gsdk_leaderboard_query_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.response_body, sizeof(out.response_body), r.response_body);
    copy_str(out.error_message, sizeof(out.error_message), r.error_message);
    return out;
}

gsdk_leaderboard_query_result_t gsdk_leaderboard_rank(gsdk_client_t* c, const char* user_id, int32_t ms) {
    if (c == nullptr || user_id == nullptr || ms < 0) {
        return leaderboard_query_error("invalid_argument");
    }
    LeaderboardQueryResult r;
    try {
        r = c->client.leaderboard_rank(user_id, std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return leaderboard_query_error(ex.what());
    }
    gsdk_leaderboard_query_result_t out{}; out.ok = r.ok; out.error_code = r.error_code;
    copy_str(out.response_body, sizeof(out.response_body), r.response_body);
    copy_str(out.error_message, sizeof(out.error_message), r.error_message);
    return out;
}

gsdk_echo_result_t gsdk_echo(gsdk_client_t* c, const char* body, int32_t ms) {
    if (c == nullptr || body == nullptr || ms < 0) {
        return echo_error("invalid_argument");
    }
    EchoResult r;
    try {
        r = c->client.echo(safe_string(body), std::chrono::milliseconds(ms));
    } catch (const std::exception& ex) {
        return echo_error(ex.what());
    }
    gsdk_echo_result_t out{}; out.ok = r.ok;
    copy_str(out.body, 4096, r.echo_body);
    return out;
}

} // extern "C"
