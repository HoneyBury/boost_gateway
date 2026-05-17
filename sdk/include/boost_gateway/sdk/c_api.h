#pragma once
// SDK v4.1.0: Stable C API for cross-language binding.
// All functions use opaque handle and plain C types.
// ABI-stable across compiler versions.

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#ifdef _WIN32
  #ifdef BOOST_GATEWAY_SDK_EXPORTS
    #define GSDK_API __declspec(dllexport)
  #else
    #define GSDK_API __declspec(dllimport)
  #endif
#else
  #define GSDK_API __attribute__((visibility("default")))
#endif

typedef struct gsdk_client_t gsdk_client_t;

typedef void (*gsdk_push_callback_t)(uint16_t message_id, const char* body, void* user_data);
typedef void (*gsdk_disconnect_callback_t)(void* user_data);

// ── Lifecycle ────────────────────────────────────────────────────────

GSDK_API const char* gsdk_version(void);
GSDK_API gsdk_client_t* gsdk_create(void);
GSDK_API void gsdk_destroy(gsdk_client_t* client);

// ── Connection ───────────────────────────────────────────────────────

GSDK_API int gsdk_connect(gsdk_client_t* client, const char* host, uint16_t port, int32_t timeout_ms);
GSDK_API void gsdk_disconnect(gsdk_client_t* client);
GSDK_API int gsdk_is_connected(const gsdk_client_t* client);

// ── Callbacks ────────────────────────────────────────────────────────

GSDK_API void gsdk_on_push(gsdk_client_t* client, gsdk_push_callback_t cb, void* user_data);
GSDK_API void gsdk_on_disconnect(gsdk_client_t* client, gsdk_disconnect_callback_t cb, void* user_data);
GSDK_API void gsdk_start_heartbeat(gsdk_client_t* client, int32_t interval_seconds);
GSDK_API void gsdk_stop_heartbeat(gsdk_client_t* client);

// ── Auth ─────────────────────────────────────────────────────────────

typedef struct { int ok; int32_t error_code; char user_id[64]; char display_name[64]; char error_message[256]; } gsdk_login_result_t;
GSDK_API gsdk_login_result_t gsdk_login(gsdk_client_t* client, const char* user_id, const char* token, int32_t timeout_ms);

// ── Room ─────────────────────────────────────────────────────────────

typedef struct { int ok; int32_t error_code; char room_id[64]; int member_count; char error_message[256]; } gsdk_room_result_t;
GSDK_API gsdk_room_result_t gsdk_create_room(gsdk_client_t* client, const char* room_id, int32_t timeout_ms);
GSDK_API gsdk_room_result_t gsdk_join_room(gsdk_client_t* client, const char* room_id, int32_t timeout_ms);
GSDK_API gsdk_room_result_t gsdk_leave_room(gsdk_client_t* client, const char* room_id, int32_t timeout_ms);
GSDK_API gsdk_room_result_t gsdk_set_ready(gsdk_client_t* client, int ready, int32_t timeout_ms);

// ── Battle ───────────────────────────────────────────────────────────

typedef struct { int ok; int32_t error_code; char battle_id[64]; char error_message[256]; } gsdk_battle_start_result_t;
GSDK_API gsdk_battle_start_result_t gsdk_start_battle(gsdk_client_t* client, const char* room_id, int32_t timeout_ms);

typedef struct { int ok; int32_t error_code; uint64_t input_seq; char error_message[256]; } gsdk_battle_input_result_t;
GSDK_API gsdk_battle_input_result_t gsdk_send_battle_input(gsdk_client_t* client, const char* input_data, int32_t timeout_ms);

// ── Matchmaking ─────────────────────────────────────────────────────

typedef struct { int ok; int32_t error_code; char response_body[4096]; char error_message[256]; } gsdk_match_result_t;
GSDK_API gsdk_match_result_t gsdk_match_join(gsdk_client_t* client, const char* user_id, int64_t mmr, const char* mode, int32_t timeout_ms);
GSDK_API gsdk_match_result_t gsdk_match_leave(gsdk_client_t* client, const char* user_id, const char* mode, int32_t timeout_ms);
GSDK_API gsdk_match_result_t gsdk_match_status(gsdk_client_t* client, const char* user_id, const char* mode, int32_t timeout_ms);

// ── Leaderboard ─────────────────────────────────────────────────────

typedef struct { int ok; int32_t error_code; char response_body[4096]; char error_message[256]; } gsdk_leaderboard_submit_result_t;
typedef struct { int ok; int32_t error_code; char response_body[4096]; char error_message[256]; } gsdk_leaderboard_query_result_t;
GSDK_API gsdk_leaderboard_submit_result_t gsdk_leaderboard_submit(gsdk_client_t* client, const char* user_id, const char* display_name, int64_t score, int32_t timeout_ms);
GSDK_API gsdk_leaderboard_query_result_t gsdk_leaderboard_top(gsdk_client_t* client, uint32_t k, int32_t timeout_ms);
GSDK_API gsdk_leaderboard_query_result_t gsdk_leaderboard_rank(gsdk_client_t* client, const char* user_id, int32_t timeout_ms);

// ── Echo ─────────────────────────────────────────────────────────────

typedef struct { int ok; char body[4096]; } gsdk_echo_result_t;
GSDK_API gsdk_echo_result_t gsdk_echo(gsdk_client_t* client, const char* body, int32_t timeout_ms);

#ifdef __cplusplus
}
#endif
