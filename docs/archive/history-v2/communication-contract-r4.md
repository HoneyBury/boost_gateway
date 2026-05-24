# R4 Communication Contract Formalization & Fault Injection

> Date: `2026-05-23`
> Scope: typed envelope, BackendEnvelope compatibility layer, proto/gRPC migration roadmap, error code propagation, fault injection matrix

## 1. Backend Service Handler Contracts

### 1.1 Login Service

| Operation | Backend message_type | Request payload fields | Response payload fields | Client error code | Typed Kind (v3) |
|-----------|---------------------|----------------------|-----------------------|------------------|-----------------|
| Authenticate | `login_request` | `{user_id, token, display_name}` | `{status, user_id, display_name, role, is_duplicate}` | `kInvalidToken` (1003), `kLoginBackendUnavailable` (2008) | `kLoginRequest` / `kLoginResponse` |

Source: `runtime.cpp` lines 273-371, `login_service.cpp` lines 54-116, `matchmaking_service.cpp` lines 339-347 (handler registration pattern), `backend_server.cpp` lines 174-202 (handler dispatch).

The login flow uses Gateway TCP `kLoginRequest` (2001). When a `GatewayServiceBridge` is configured, the Runtime delegates to the login backend via `bridge_->route(kLogin, "login_request", body)`. The backend returns `{"status":"ok"}` on success or error payload with `"reason"` on failure.

**Error code mapping (Runtime -> Client):**

| Backend result | Client `ErrorCode` | Numeric |
|---|---|---|
| Routing failure or timeout | `kLoginBackendUnavailable` | 2008 |
| Backend rejects (`result.error == kRejected`) | `kInvalidToken` | 1003 |
| Backend responds with non-ok status | `kInvalidToken` | 1003 |

---

### 1.2 Room Service

| Operation | Backend message_type | Request payload fields | Response payload fields | Client error code | Typed Kind (v3) |
|-----------|---------------------|----------------------|-----------------------|------------------|-----------------|
| Create room | `room_create` | `{user_id, room_id}` | `{status, room_id, member_count}` | `kInvalidRoomId` (2001), `kRoomBackendUnavailable` (2009) | `kRoomCreateRequest` / `kRoomCreateResponse` |
| Join room | `room_join` | `{user_id, room_id}` | `{status, room_id, member_count}` | `kInvalidRoomId` (2001), `kRoomBackendUnavailable` (2009) | `kRoomJoinRequest` / `kRoomJoinResponse` |
| Leave room | `room_leave` | `{user_id, room_id}` | `{status}` | `kNotInRoom` (2005), `kRoomBackendUnavailable` (2009) | `kRoomLeaveRequest` / `kRoomLeaveResponse` |
| Set ready | `room_ready` | `{user_id, room_id, ready}` | `{status}` | `kAuthRequired` (1001), `kRoomBackendUnavailable` (2009) | `kRoomReadyRequest` / `kRoomReadyResponse` |
| Start battle (cascade) | `room_start_battle` | `{user_id, room_id}` | `{status, forward:{target, message_type, payload}}` | `kBattleNotStarted` (3003), `kRoomBackendUnavailable` (2009) | `kRoomStartBattleRequest` / `kRoomStartBattleResponse` |

**Pipeline:** The `room_start_battle` handler on the room backend returns a `forward` object in its response, instructing the gateway to cascade to the battle backend:

```json
{
  "status": "ok",
  "forward": {
    "target": "battle",
    "message_type": "battle_create",
    "payload": { "battle_id": "...", "room_id": "...", "player_ids": [...] }
  }
}
```

**Error code mapping (Runtime -> Client):**

| Failure scenario | Client `ErrorCode` | Numeric |
|---|---|---|
| Backend rejected (kRejected) | `kInvalidRoomId` / `kAuthRequired` / `kBattleNotStarted` | 2001 / 1001 / 3003 |
| Backend unavailable | `kRoomBackendUnavailable` | 2009 |
| Battle cascade failure | `kBattleNotStarted` | 3003 |

---

### 1.3 Battle Service

| Operation | Backend message_type | Request payload fields | Response payload fields | Client error code | Typed Kind (v3) |
|-----------|---------------------|----------------------|-----------------------|------------------|-----------------|
| Submit input | `battle_input` | `{user_id, battle_id, input_data, score, submitted_frame}` | `{status, input_seq}` | `kBattleBackendUnavailable` (3010), `kAuthRequired` (1001) | `kBattleInputRequest` / `kBattleInputResponse` |
| Finish battle | `battle_finish` | `{user_id, battle_id, reason}` | `{status, push_to_sessions:[...]}` | `kBattleNotStarted` (3003), `kBattleBackendUnavailable` (3010) | `kBattleFinishRequest` / `kBattleFinishResponse` |

**Note:** `battle_create` is a system-to-system operation triggered by the `room_start_battle` cascade, not directly exposed to clients. The battle backend defines its own `battle_create` handler.

Frame/battle state pushes flow from backend to gateway via the `push_to_sessions` array in battle responses. Push kinds: `"battle_started"`, `"frame_advanced"`, `"battle_finished"`.

**Error code mapping (Runtime -> Client):**

| Failure scenario | Client `ErrorCode` | Numeric |
|---|---|---|
| Backend unavailable | `kBattleBackendUnavailable` | 3010 |
| Backend rejects input | `kAuthRequired` | 1001 |
| No active battle for room | `kBattleNotStarted` | 3003 |

---

### 1.4 Matchmaking Service

| Operation | Backend message_type | Request payload fields | Response payload fields | Client error code | Typed Kind (v3) |
|-----------|---------------------|----------------------|-----------------------|------------------|-----------------|
| Join queue | `match_join` | `{user_id, mmr, mode}` | `{status, queued, mode}` | `kSessionNotFound` (9002) | `kMatchJoinRequest` / `kMatchJoinResponse` |
| Leave queue | `match_leave` | `{user_id, mode}` | `{status, left}` | `kSessionNotFound` (9002) | `kMatchLeaveRequest` / `kMatchLeaveResponse` |
| Query status | `match_status` | `{user_id, mode}` | `{status, matched, match_id, mode, avg_mmr, queue_size}` | `kSessionNotFound` (9002) | `kMatchStatusRequest` / `kMatchStatusResponse` |

**Special behavior:** The matchmaking service uses Raft consensus (`v3::cluster::RaftNode`) for leader election. Only the Raft leader performs matchmaking. Non-leader nodes return error with a `leader_hint` JSON:

```json
{"status": "error", "reason": "not_raft_leader:{\"leader_id\":\"...\", \"leader_host\":\"...\", \"leader_port\":...}"}
```

**Internal Raft RPC handlers (not client-facing):**

| Operation | Backend message_type | Payload |
|-----------|---------------------|---------|
| Raft request vote | `raft_request_vote` | Raft `RequestVoteArgs` JSON |
| Raft append entries | `raft_append_entries` | Raft `AppendEntriesArgs` JSON |

---

### 1.5 Leaderboard Service

| Operation | Backend message_type | Request payload fields | Response payload fields | Client error code | Typed Kind (v3) |
|-----------|---------------------|----------------------|-----------------------|------------------|-----------------|
| Submit score | `leaderboard_submit` | `{user_id, display_name, score, idempotency_key}` | `{status, user_id, rank, idempotent}` | `kSessionNotFound` (9002) | `kLeaderboardSubmitRequest` / `kLeaderboardSubmitResponse` |
| Get top K | `leaderboard_top` | `{k}` | `{status, entries:[{rank, user_id, display_name, score}]}` | `kSessionNotFound` (9002) | `kLeaderboardTopRequest` / `kLeaderboardTopResponse` |
| Lookup rank | `leaderboard_rank` | `{user_id}` | `{status, user_id, rank, score}` | `kSessionNotFound` (9002) | `kLeaderboardRankRequest` / `kLeaderboardRankResponse` |

**Special behavior:**
- When Redis is unavailable, the leaderboard service falls back to in-memory storage.
- Score submission supports idempotency via an optional `idempotency_key` field. Duplicate keys are silently accepted (returning existing rank) rather than re-applied.
- Like matchmaking, the leaderboard service uses Raft and returns `not_raft_leader` error with a leader hint when the target node is not the leader.

---

## 2. Envelope Wire Format

### 2.1 v2 BackendEnvelope (legacy JSON)

Defined in `include/v2/service/backend_envelope.h`. Serialized to JSON:

```json
{
  "correlation_id": 12345,
  "source_service": "gateway",
  "target_service": "login",
  "kind": "request",
  "timeout_ms": 5000,
  "error_code": 0,
  "payload": "{\"user_id\":\"alice\",\"token\":\"...\",\"display_name\":\"Alice\"}",
  "message_type": "login_request",
  "trace_id": 987654,
  "span_id": 123456
}
```

Key fields:
- `message_type` (string): The handler name on the backend, e.g. `"login_request"`, `"room_create"`, `"match_join"`, `"leaderboard_submit"`
- `kind` (enum): `"request"`, `"response"`, `"push"`, `"error"`
- `payload` (string): Escaped JSON string containing the operation-specific payload

### 2.2 v3 TypedEnvelope (typed envelope helper)

Defined in `include/v3/proto/envelope_codec.h`. Uses a nested JSON structure:

```json
{
  "correlation_id": 12345,
  "source_service": "gateway",
  "target_service": "login",
  "timeout_ms": 5000,
  "error_code": 0,
  "trace_id": 987654,
  "span_id": 123456,
  "payload": {
    "login": {
      "login_request": {
        "user_id": "alice",
        "token": "...",
        "display_name": "Alice"
      }
    }
  }
}
```

The payload is a 2-level nested object: `{domain: {message_kind: payload_fields}}`. The adapter layer (`envelope_adapter.cpp`) converts between v2 and v3 formats via `to_typed_envelope()` and `to_backend_envelope()`.

### 2.3 Message Type Mappings (v2 message_type to v3 Typed Kind)

Source: `envelope_adapter.cpp` `kMessageTypeMappings` array (25 entries):

| v2 message_type | v3 EnvelopeMessageKind |
|---|---|
| `login_request` | `kLoginRequest` |
| `login_response` | `kLoginResponse` |
| `room_create` | `kRoomCreateRequest` |
| `room_create_response` | `kRoomCreateResponse` |
| `room_join` | `kRoomJoinRequest` |
| `room_join_response` | `kRoomJoinResponse` |
| `room_ready` | `kRoomReadyRequest` |
| `room_ready_response` | `kRoomReadyResponse` |
| `battle_input` | `kBattleInputRequest` |
| `battle_input_response` | `kBattleInputResponse` |
| `match_join` | `kMatchJoinRequest` |
| `match_join_response` | `kMatchJoinResponse` |
| `match_leave` | `kMatchLeaveRequest` |
| `match_leave_response` | `kMatchLeaveResponse` |
| `match_status` | `kMatchStatusRequest` |
| `match_status_response` | `kMatchStatusResponse` |
| `leaderboard_submit` | `kLeaderboardSubmitRequest` |
| `leaderboard_submit_response` | `kLeaderboardSubmitResponse` |
| `leaderboard_top` | `kLeaderboardTopRequest` |
| `leaderboard_top_response` | `kLeaderboardTopResponse` |
| `leaderboard_rank` | `kLeaderboardRankRequest` |
| `leaderboard_rank_response` | `kLeaderboardRankResponse` |
| `match_found_push` | `kMatchFoundPush` |
| `match_to_room` | `kMatchToRoomRequest` |
| `match_to_room_response` | `kMatchToRoomResponse` |

---

## 3. Proto/gRPC Migration Roadmap

### Phase 1 (Current, v3.0-v3.4): Raw JSON payload + typed envelope kind

**Status:** ACTIVE (default production path)

- BackendEnvelope JSON is the primary inter-service transport
- `v3::proto` typed envelope helper provides typed `EnvelopeMessageKind` enum with structured but still-JSON encoding
- Adapter layer (`envelope_adapter.cpp`) converts between v2 BackendEnvelope and v3 TypedEnvelope
- `decode_handler_payload()` detects encoding: returns `kTypedEnvelope` or `kLegacyRawJson`
- Legacy raw JSON path has a deprecation notice via `legacy_raw_json_deprecation_notice()`
- All 5 backend services use `decode_handler_payload()` and `wrap_typed_response_if_needed()` for backward compatibility
- `.proto` schema files exist but are used for contract validation only (not wire serialization)

**Trigger:** N/A — this is the current baseline

**Risk:** None

**Rollback:** N/A

### Phase 2 (v3.5): Proto-generated envelope for new handlers

**Status:** PLANNED

- New message types MUST be added to `.proto` files and generated via `generate_v3_proto_cpp` CMake target
- Use `ServiceEnvelope` from `common.proto` as the wire format for new handlers
- Existing handlers continue using JSON envelope; new handlers use proto
- `check_v3_proto_schema` CI gate ensures proto changes do not drift from implementation
- Dual encoding paths coexist in `decode_handler_payload()`

**Trigger Conditions:**
- A new backend service or message type is introduced
- Cross-language client (.NET, Python, etc.) requires contract-first IDL

**Risk:**
- Developers must maintain both JSON and proto encoding paths
- Testing matrix doubles: need to test both encoding paths per handler

**Rollback:**
- Phase 2 is opt-in per handler. If proto path has issues, set `message_type` to a JSON-only handler name and the legacy path handles it.

### Phase 3 (v3.6): Legacy raw JSON deprecated (still accepted)

**Status:** PLANNED

- All handler `message_type` values default to typed envelope kind
- Legacy raw JSON encoding still accepted but produces a loud deprecation warning each time
- `decode_handler_payload()` returns `kLegacyRawJson` but logs at WARN level
- Schema validator (`runtime.cpp` `schema_validator_`) validates typed envelope payloads against `.proto` schemas before dispatch
- All integration tests run with typed envelopes as the primary encoding

**Trigger Conditions:**
- Phase 2 has been stable for at least one release cycle (no JSON-envelope-only bug fixes)
- All 5 backend services can be configured to accept only typed envelopes without regression

**Risk:**
- Clients or tooling that bypass the envelope adapter and construct raw JSON payloads directly will break
- Third-party integrations using custom JSON payloads need migration

**Rollback:**
- Set a feature flag (e.g., `V2_ALLOW_LEGACY_JSON=true` environment variable) to restore legacy JSON acceptance without warning
- If a critical bug is found in typed envelope parsing, fall back to `kLegacyRawJson` globally

### Phase 4 (v4.0): Raw JSON removed, proto-only

**Status:** FUTURE

- BackendEnvelope JSON format is removed
- All inter-service communication uses generated protobuf wire format via `ServiceEnvelope` (with `oneof` per domain)
- `v2/service/envelope_adapter.cpp` and all BackendEnvelope code are removed
- `decode_handler_payload()` and `wrap_typed_response_if_needed()` removed
- gRPC transport available as an alternative to raw TCP+protobuf, controlled by build flag `BOOST_BUILD_GRPC=ON`

**Trigger Conditions:**
- Phase 3 has been stable for at least two release cycles
- Performance benchmarks show proto wire format meets or exceeds JSON envelope P99 latency
- SDK and all client libraries have migrated away from raw JSON construction
- gRPC transport PoC is complete with full-flow evidence for all 5 services
- TLS/mTLS, load balancing, health check, deadline, retry, and backpressure policies documented

**Risk:**
- Major breaking change: all inter-service communication must migrate simultaneously
- Tooling (logging, debugging, curl-based manual testing) loses human-readable JSON
- gRPC dependency increases build complexity and binary size

**Rollback:**
- A JSON-to-proto bridging proxy can be deployed at each backend's listening port to translate between old JSON clients and new proto backends
- Retain the `decode_handler_payload()` code path but gate behind a compile-time flag for emergency use

### Migration Decision Log

| Decision point | Document | Status |
|---|---|---|
| Keep generated gRPC as experimental, not default | `../process/v3-proto-grpc-adr.md` | Accepted |
| gRPC PoC implemented | `docs/grpc-poc-summary.md` | Completed (Stage E) |
| Proto schema validation CI gate | `scripts/check_v3_proto_schema.py` | Available |
| gRPC build flag | `BOOST_BUILD_GRPC=ON` | Available |
| Performance gRPC vs TCP | `docs/grpc-poc-summary.md` | TBD (no real I/O data yet) |

---

## 4. Error Code Propagation

### 4.1 Error Code Definitions

**Two error code systems coexist:**

| Scope | Type | Prefix | Source | Example |
|---|---|---|---|---|
| Client-facing | `net::protocol::ErrorCode` (enum class) | 4-digit positive integers | `include/net/protocol.h` | `kAuthRequired = 1001` |
| Backend-internal | `v2::service::ServiceErrorCode` (enum class) | Negative integers (-1xxx range) | `include/v2/service/error_codes.h` | `kInvalidRequest = -1004` |
| Client-facing (mapped) | Via `to_client_error()` | Negative integers (-2xxx range) | `error_codes.cpp` | `kTimeout = -2001` |

**Backend (ServiceErrorCode) definitions:**

| Symbol | Value | Category |
|---|---|---|
| `kOk` | 0 | Success |
| `kTimeout` | -1001 | Gateway routing |
| `kUnavailable` | -1002 | Gateway routing |
| `kRejected` | -1003 | Gateway routing |
| `kInvalidRequest` | -1004 | Gateway routing |
| `kCircuitOpen` | -1007 | Gateway routing |
| `kInternalError` | -1005 | General |
| `kNotImplemented` | -1006 | General |
| `kUserAlreadyExists` | -1100 | Identity |
| `kIllegalUsername` | -1101 | Identity |
| `kWeakCredential` | -1102 | Identity |
| `kAccountDisabled` | -1103 | Identity |
| `kStorageUnavailable` | -1104 | Identity |
| `kRoomNotFound` | -1200 | Room/Lobby |
| `kRoomFull` | -1201 | Room/Lobby |
| `kRoomInInstance` | -1202 | Room/Lobby |
| `kRoomClosed` | -1203 | Room/Lobby |
| `kNotRoomOwner` | -1204 | Room/Lobby |
| `kNotRoomMember` | -1205 | Room/Lobby |

**Client-facing (net::protocol::ErrorCode) definitions:**

| Symbol | Value | Category |
|---|---|---|
| `kOk` | 0 | Success |
| `kAuthRequired` | 1001 | Auth |
| `kInvalidUserId` | 1002 | Auth |
| `kInvalidToken` | 1003 | Auth |
| `kDuplicateLogin` | 1004 | Auth |
| `kTokenExpired` | 1005 | Auth |
| `kLoginBackendUnavailable` | 2008 | Backend |
| `kInvalidRoomId` | 2001 | Room |
| `kRoomAlreadyExists` | 2002 | Room |
| `kRoomNotFound` | 2003 | Room |
| `kRoomInBattle` | 2004 | Room |
| `kNotInRoom` | 2005 | Room |
| `kNotRoomOwner` | 2006 | Room |
| `kNotAllReady` | 2007 | Room |
| `kRoomBackendUnavailable` | 2009 | Room |
| `kNotEnoughPlayers` | 3001 | Battle |
| `kBattleAlreadyStarted` | 3002 | Battle |
| `kBattleNotStarted` | 3003 | Battle |
| `kPlayerNotInBattle` | 3004 | Battle |
| `kBattleBackendUnavailable` | 3010 | Battle |
| `kRateLimited` | 9001 | Gateway |
| `kSessionNotFound` | 9002 | Gateway |

### 4.2 Gateway -> Backend Routing Failure Propagation

This table shows the complete error path from `GatewayServiceBridge::route()` through to the client response:

| Routing failure | `route()` result.error | Client `ErrorCode` | Client body |
|---|---|---|---|
| Circuit breaker OPEN | `kCircuitOpen` (-1007) | `kLogin/BackendUnavailable` or generic | Circuit-breaker-derived |
| Backend connection failed | `kUnavailable` (-1002) | `k*BackendUnavailable` | `"backend_error"` |
| Backend request timeout | `kTimeout` (-1001) | `k*BackendUnavailable` | `"backend_error"` |
| Backend responded `kind == kError` | `error_code` from envelope (e.g. -1003) | Mapped per `error_code`: `kRejected` -> specific codes | `reason` from backend |
| Backend responded non-ok status | N/A (success=false) | `kInvalidToken` / `kInvalidRoomId` etc. | `reason` from JSON payload |

### 4.3 Session Kick/Close Error Reasons

| Kick reason | Client `ErrorCode` | Push message_id | body |
|---|---|---|---|
| Duplicate login | `kDuplicateLogin` (1004) | `kSessionKickedPush` (1003) | `"session_kicked:duplicate_login"` |
| With room transfer | `kDuplicateLogin` (1004) | `kSessionKickedPush` (1003) | `"session_kicked:duplicate_login:room_transferred"` |

---

## 5. Fault Injection Matrix

### 5.1 Existing Fault Injection Capabilities

**Code-artifact inventory:**

| Component | Location | Capability |
|---|---|---|
| `ChaosSimulator` | `tests/chaos/chaos_test_framework.h` | Rule-based fault injection: MessageDrop, MessageDelay, MessageCorrupt, NetworkPartition, ProcessKill |
| `NetworkPartitionSimulator` | `include/v2/test/fault_injector.h` / `src/v2/test/fault_injector.cpp` | TCP proxy that drops bytes after threshold or at random rate |
| `LatencyInjector` | `include/v2/test/fault_injector.h` | Fixed or random delay wrapper for callbacks |
| `FailureInjector` | `include/v2/test/fault_injector.h` | Probabilistic failure injection (0.0-1.0) |
| `CircuitBreaker` | `include/v2/service/circuit_breaker.h` / `src/v2/service/circuit_breaker.cpp` | Closed -> Open -> HalfOpen state machine with configurable threshold/timeout |

**Chaos test inventory:**

| Test | File | What it covers |
|---|---|---|
| `ChaosGatewayTest.NetworkPartitionDuringLogin` | `tests/chaos/gateway_chaos_test.cpp` | 2s partition during login, verify system not crash |
| `ChaosGatewayTest.BackendDisconnectAndRecover` | `tests/chaos/gateway_chaos_test.cpp` | Simulate 3s backend outage, verify recovery |
| `ChaosGatewayTest.RandomMessageDelayAndTimeout` | `tests/chaos/gateway_chaos_test.cpp` | 50% probability 500-2000ms delay on gateway, verify timeout handling |
| `ChaosGatewayTest.RandomMessageDropAndRetransmission` | `tests/chaos/gateway_chaos_test.cpp` | 5% message drop rate, verify system survives |
| `ChaosStabilityTest.LongRunningWithRandomFaults` | `tests/chaos/stability_chaos_test.cpp` | 60s run with random faults (drop/delay/corrupt), verify >50% success rate |

### 5.2 Fault Injection Matrix

| Fault scenario | Injection method | Expected behavior | Verification method | Current status |
|---|---|---|---|---|
| Backend process crash (login/room/battle/match/leaderboard) | Kill backend process OR `ProcessKill` chaos rule | Gateway detects connection loss within `connect_timeout` (default 500ms); `ensure_connection()` returns null; `route()` returns `kUnavailable`; circuit breaker tracks failure; gateway does not crash | `ChaosGatewayTest.BackendDisconnectAndRecover` | **COVERED** (gateway_chaos_test, circuit breaker integration) |
| Backend recovery after crash | Restart backend process OR `recover_all()` in chaos | `ensure_connection()` creates new `BackendConnection` on next `route()` call; circuit breaker transitions HalfOpen -> Closed after `half_open_max_requests` successful probes; gateway resumes routing | `ChaosGatewayTest.BackendDisconnectAndRecover` | **COVERED** (gateway_chaos_test verifies recovery path) |
| Connection timeout | Inject network delay via `LatencyInjector` or `MessageDelay` chaos rule (500-2000ms) | `BackendConnection::send_request()` times out after `timeout` duration (default from BackendConfig); `route()` returns `kTimeout`; pending request cleaned up without leaking | `ChaosGatewayTest.RandomMessageDelayAndTimeout` | **COVERED** (chaos test verifies timeout handling; no leak detection yet) |
| Circuit breaker OPEN | Consecutive failures exceeding threshold (default 3) | `CircuitBreaker::allow_request()` returns false; `route()` returns `kCircuitOpen` immediately without attempting connection; metrics record `degraded` | `tests/v2/unit/circuit_breaker_test.cpp` | **COVERED** (unit test covers state machine, threshold, timeout) |
| Circuit breaker HalfOpen recovery | After `timeout` (default 30s) in OPEN state | `CircuitBreaker::allow_request()` transitions to HalfOpen, allows one probe; on success -> Closed; on failure -> back to Open with reset timer | Circuit breaker unit test | **COVERED** (HSM transitions: Closed-Open-HalfOpen-Closed) |
| Raft leader switch (matchmaking) | Stop current Raft leader node | Raft cluster elects new leader; only leader performs matchmaking; non-leader returns `not_raft_leader` error with leader hint; committed log entries not lost | `tests/v2/unit/raft_test.cpp` | **COVERED** (raft unit test exists) |
| Raft follower catch-up (leaderboard) | Restart follower node after downtime | Follower receives AppendEntries RPCs from leader; replays committed log entries via `apply_raft_entry()`; catches up to match leader state | `tests/v2/unit/raft_test.cpp` | **COVERED** (raft unit test covers log replication) |
| Redis unavailable (leaderboard) | Shut down Redis server (default localhost:6379) | `try_auto_connect_redis()` fails gracefully; leaderboard falls back to in-memory `SortedSet`; all operations (submit/top/rank) continue with degraded semantics; no data loss for in-flight scores | `tests/v2/unit/leaderboard_test.cpp` | **COVERED** (leaderboard unit test covers in-memory fallback; manual verification through `BOOST_REDIS_HOST` env) |
| Network partition | `NetworkPartitionSimulator` TCP proxy with `drop_after_bytes` or `drop_rate` | Active connections stall; gateway `send_request()` times out; `ensure_connection()` may close stale connections; on partition recovery, new connections are established | `ChaosGatewayTest.NetworkPartitionDuringLogin` | **COVERED** (basic partition scenario tested; no cross-service partition test) |
| Message corruption | `ChaosSimulator` with `MessageCorrupt` rule | Corrupted messages fail frame decoding in `read_frame()`; connection is closed; gateway reconnects on next request; backend is unaffected | `ChaosStabilityTest.LongRunningWithRandomFaults` | **COVERED** (stability test includes corruption, but no explicit corrupt-payload assertion) |
| Gateway graceful shutdown | Stop gateway process (SIGTERM) | All backend connections closed via `GatewayServiceBridge::shutdown()`; pending requests get error response; in-flight battle input offload workers drain queue | Manual / `multi_process_test.h` framework | **PARTIAL** (shutdown sequence verified structurally; no explicit chaos test for SIGTERM) |
| Saturation / rate limit | Flood with requests exceeding `kMaxMessagesPerWindow` | `GatewayService::check_rate_limit()` blocks excess requests; client receives `kRateLimited` (9001); only gateway ingress is rate-limited, backend is not overwhelmed | Manual via perf test | **PARTIAL** (rate limiter logic present in gateway_service.cpp; no chaos test for rate-limit overflow) |
| Battle input offload worker stall | Inject delay into `enqueue_battle_route_task` workers | Workers are dedicated threads; if all workers are busy, tasks queue in `battle_route_tasks_` deque; gateway continues accepting inputs; no worker crash or memory grow unbounded | Manual inspection | **NOT COVERED** (no chaos test for worker thread starvation) |
| Mixed encoding (legacy JSON + typed envelope) | Send one handler with BackendEnvelope JSON, another with v3 TypedEnvelope | `decode_handler_payload()` auto-detects encoding; each handler processes its encoding correctly; adapter round-trips preserve all fields | `tests/v2/integration/service_bus_integrity_test.cpp` | **COVERED** (integration test covers round-trip for typed/proto envelopes) |
| Service registry heartbeat failure | Make backend unreachable after registry registration | `ensure_connection()` marks instance unhealthy in registry via `registry_->mark_unhealthy()`; cluster router removes instance from discovery until heartbeat resumes | `tests/v2/integration/backend_health_test.cpp` | **COVERED** (integration test covers health check / heartbeat path) |

### 5.3 Coverage Summary

| Layer | Coverage status | Gaps |
|---|---|---|
| Circuit breaker (per-backend) | Full unit test coverage | No chaos test that combines circuit breaker with real backend timeout |
| Chaos test infrastructure | Framework in place (`ChaosSimulator`, `NetworkPartitionSimulator`) | Tests only cover gateway-local faults; no cross-service fault injection (e.g., inject failure between matchmaking and room) |
| Raft consensus faults | Unit test coverage for leader election and log replication | No chaos test for Raft during active matchmaking/leaderboard requests |
| Redis degradation | Unit test coverage for in-memory fallback | No chaos test that toggles Redis availability mid-operation |
| Network partition | Basic chaos test (single client) | No partition test with multiple concurrent clients, no cross-region partition |
| Long-running stability | 60s random fault injection test | Only covers echo requests, not full flow (login -> room -> battle -> leaderboard) |
| Battle input offload | Coverage missing | No test for worker thread starvation or overflow |

---

## 6. Performance Baseline (Communication Layer)

Source: `scripts/collect_v2_arch_baseline.py`. Last collected: PASS.

| Metric | Samples | P99 | Alarm threshold |
|---|---|---|---|
| `backend_envelope_json_roundtrip` | 10000 | 130.7 us | <= 1000 us |
| `typed_envelope_json_roundtrip` | 10000 | 164.2 us | <= 1000 us |
| `backend_typed_adapter_roundtrip` | 10000 | 19.0 us | <= 1000 us |

All three communication contract metrics pass the P99 alarm threshold. The adapter layer (v2<->v3 conversion) is the cheapest operation at ~19 us P99 because it is a simple struct-to-struct conversion with no serialization.

---

## 7. Key Files Reference

| File | Role |
|---|---|
| `include/v2/service/backend_envelope.h` | v2 BackendEnvelope struct and serialization |
| `src/v2/service/backend_envelope.cpp` | v2 JSON encode/decode, correlation ID generation |
| `include/v3/proto/envelope_codec.h` | v3 TypedEnvelope, EnvelopeMessageKind enum, typed encode/decode |
| `include/v2/service/envelope_adapter.h` | Adapter between v2 BackendEnvelope and v3 TypedEnvelope |
| `src/v2/service/envelope_adapter.cpp` | Message type mapping table, `decode_handler_payload()`, `wrap_typed_response_if_needed()` |
| `include/v2/service/error_codes.h` | Backend-internal ServiceErrorCode enum |
| `src/v2/service/error_codes.cpp` | `to_client_error()` mapping and string conversion |
| `include/net/protocol.h` | Client-facing `ErrorCode` enum and protocol message ID constants |
| `include/v2/service/circuit_breaker.h` | Circuit breaker state machine |
| `src/v2/service/circuit_breaker.cpp` | Circuit breaker implementation |
| `include/v2/test/fault_injector.h` | `LatencyInjector`, `FailureInjector`, `NetworkPartitionSimulator` |
| `src/v2/test/fault_injector.cpp` | Network partition simulator (TCP proxy relay) |
| `tests/chaos/chaos_test_framework.h` | `ChaosSimulator` (rule-based fault injection framework) |
| `tests/chaos/gateway_chaos_test.cpp` | Chaos tests: partition, disconnect, delay, drop |
| `tests/chaos/stability_chaos_test.cpp` | 60s stability test with random faults |
| `proto/v3/common.proto` | `ServiceEnvelope` with oneof per domain (canonical proto contract) |
| `proto/v3/*.proto` | Per-service type definitions (login, room, battle, match, leaderboard, gateway) |
| `../process/v3-proto-grpc-adr.md` | ADR for proto/gRPC migration: current decision to keep experimental |
| `docs/grpc-poc-summary.md` | gRPC PoC summary: architecture, components, limitations |

Note: At the time of this writing, the gateway->backend bridge (`GatewayServiceBridge::route()`) uses `message_type` string-based routing (e.g., `"login_request"`, `"room_create"`). The `message_type` value corresponds to the handler key registered on the backend `BackendServer::HandlerMap`. The v3 TypedEnvelope is used as an optional wrapper within the handler (via `decode_handler_payload()`) but is not yet the default wire format for the bridge `route()` calls.
