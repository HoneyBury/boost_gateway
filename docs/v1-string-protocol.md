# v1.x 业务字符串协议冻结（T02 后半）

## 1. 文档定位

本文档是 **`v1.1.6`** 对 **login / room / battle** 三条业务线**当前主链**上真实使用的**字符串 body 协议**的冻结表，与 `net::protocol::ErrorCode`、`include/net/protocol.h` 消息号一致。

- **主契约**：客户端 ↔ 网关之间，**以本文档 + `docs/runtime-playbook.md` §2 包格式为准**。
- **非契约**：`include/net/message_types.h`（`net::msg`）与 `message_serializer` 为**草案**，主链未使用；已知与字符串 body 分叉处见本文档 §5。

与 `docs/v1-maturity-matrix.md` 冲突时，**以矩阵中的成熟度（stable / experimental）解释“是否可依赖”**；与**消息号、body 形态、错误码数值**冲突时，**以本文档与 `include/net/protocol.h` 为准**，并应回写修正矩阵或 playbook 中的笔误。

---

## 2. 错误响应约定

业务失败时，服务端通常回复 **`kErrorResponse (9001)`**，`error_code` 为 `net::protocol::ErrorCode`，`body` 默认等于 `net::protocol::to_string(error_code)`（`PushService::send_error` 在 body 为空时填充）。

登录校验失败路径中，`kInvalidUserId` 有一处直接 `session->send`（与上述约定一致：`body == "invalid_user_id"`）。

---

## 3. `ErrorCode` 表（冻结）

| 枚举 | 值 | `to_string()` / 默认 body |
|---|---|---|
| `kOk` | 0 | `ok` |
| `kAuthRequired` | 1001 | `auth_required` |
| `kInvalidUserId` | 1002 | `invalid_user_id` |
| `kInvalidToken` | 1003 | `invalid_token` |
| `kDuplicateLogin` | 1004 | `duplicate_login` |
| `kTokenExpired` | 1005 | `token_expired` |
| `kInvalidRoomId` | 2001 | `invalid_room_id` |
| `kRoomAlreadyExists` | 2002 | `room_already_exists` |
| `kRoomNotFound` | 2003 | `room_not_found` |
| `kRoomInBattle` | 2004 | `room_in_battle` |
| `kNotInRoom` | 2005 | `not_in_room` |
| `kNotRoomOwner` | 2006 | `not_room_owner` |
| `kNotAllReady` | 2007 | `not_all_ready` |
| `kNotEnoughPlayers` | 3001 | `not_enough_players` |
| `kBattleAlreadyStarted` | 3002 | `battle_already_started` |
| `kBattleNotStarted` | 3003 | `battle_not_started` |
| **`kPlayerNotInBattle`** | **3004** | **`player_not_in_battle`** |
| `kRateLimited` | 9001 | `rate_limited` |
| `kSessionNotFound` | 9002 | `session_not_found` |

> **v1.1.6 语义修正**：`SubmitInputResult::kPlayerNotInBattle` 映射为 **`kPlayerNotInBattle (3004)`**，不再使用 `kAuthRequired`。

---

## 4. 登录（`kLoginRequest` 2001 / `kLoginResponse` 2002）

### 4.1 请求 body

```text
user_id|token
user_id|token|display_name
```

解析：`login_service.cpp` — `parse_login_request`。`user_id` 为空或无第一段 → `kInvalidUserId`。

### 4.2 成功响应 body

```text
login_ok:{user_id}:{display_name}
login_ok:{user_id}:{display_name}:room={room_id}
```

第二行：登录后若会话已在房间内，附带 `:room=`（来自 `RoomManager::room_snapshot_of`）。

### 4.3 Push：`kSessionKickedPush` (1003)

顶号断开旧会话时：

```text
session_kicked:duplicate_login
session_kicked:duplicate_login:room_transferred
```

### 4.4 Push：`kSessionResumedPush` (1004)

新会话登录且已在房间：

```text
session_resumed:{room_id}:battle=0
session_resumed:{room_id}:battle=1
```

`battle` 为 `RoomSnapshot::battle_started` 的派生视图（**事实源为 `BattleManager`**，见 `v1.1.4` / `v1-business-fact-source.md`）。

### 4.5 失败

| 条件 | `ErrorCode` |
|---|---|
| 非法 user / 解析失败 | `kInvalidUserId` |
| 校验失败（过期） | `kTokenExpired` |
| 校验失败（其它） | `kInvalidToken` |

---

## 5. `net::msg` 分叉说明（非主契约）

仅列与当前字符串主链差异最大的项；完整类型见 `include/net/message_types.h`。

| 草案类型 | 与主链字符串差异摘要 |
|---|---|
| `LoginResponse` | 无 `:room=`、无 `session_resumed` push 等价物 |
| `RoomJoinResponse` | 无主链的 `:{player_count}` |
| `SessionResumedPush` | 草案仅 `room_id`，无主链的 `battle=0/1` |
| `BattleStartResponse` | 草案含 `battle_id`，主链为 `battle_started:{room_id}:{player_count}` |
| `BattleStatePush` | 草案字段与主链 `battle_state:started:...` 字符串不一致 |
| `RoomStatePush` | 草案为结构化 member 列表；主链为单字符串 `room_state:...`（见 §6.4） |

---

## 6. 房间

### 6.1 `kRoomCreateRequest` (3001) / `kRoomCreateResponse` (3002)

- 请求 body：房间 id 字符串（由 `RoomManager::create_room` 校验）。
- 成功：`room_created:{room_id}`。
- 失败：`kAuthRequired` / `kInvalidRoomId` / `kRoomAlreadyExists` / `kSessionNotFound`（见 `room_service.cpp`）。

### 6.2 `kRoomJoinRequest` (3003) / `kRoomJoinResponse` (3004)

- 请求 body：房间 id。
- 成功：`room_joined:{room_id}:{player_count}`。
- 失败：含 `kRoomInBattle`、`kRoomNotFound` 等。

### 6.3 `kRoomLeaveRequest` (3005) / `kRoomLeaveResponse` (3006)

- 请求 body：**当前实现未解析 body**，以会话当前房间为准。
- 成功：`room_left:{room_id}`。
- 若离队后人数为 0：经 **`clear_battle_if_room_empty`**（`v1.1.7` / T08）摘除 `BattleManager` 中对应战斗上下文。

### 6.4 `kRoomReadyRequest` (3007) / `kRoomReadyResponse` (3008)

- 解析：`split_once`，`ready = (action=="1"|"true") || (value=="1"|"true")`。故 body 可为 `true`、`1`、`ready|true` 等（较宽松）。
- 成功：`room_ready:on` 或 `room_ready:off`。

### 6.5 Push：`kRoomStatePush` (3009)

```text
room_state:{room_id}:owner={owner_user_id}:battle={0|1}:members={user_id}:{0|1};...
```

`owner` / `members` 中的 `user_id`：自 **`v1.1.8`** 起，`RoomService` 在 create/join 成功后将当前 `LoginContext.user_id` 写入 **`RoomMember.member_user_id`**，`build_room_state_body` **优先使用该缓存**；仅在缓存为空时回退 **`SessionManager::login_context_of`**（未走 `RoomService` 写入的装配兜底 `unknown`）。`battle` 仍为派生字段（`set_battle_active_query` 未绑定时恒为 0）。

> 房/战状态机与 `transfer_session` 契约见 **`docs/v1-room-battle-boundary.md`**。

---

## 7. 战斗

### 7.1 `kBattleStartRequest` (4001) / `kBattleStartResponse` (4002)

- 前置：`kAuthRequired`、`kNotInRoom`、`kNotRoomOwner`、全员 `ready` 检查失败 → `kNotAllReady`。
- **`player_ids`** 来源：与 **`v1.1.8`** 的 `RoomMember.member_user_id` 对齐——**非空则优先**，否则回退 `login_context_of`。
- 成功：`battle_started:{room_id}:{player_count}`。
- 失败：`kNotEnoughPlayers`、`kBattleAlreadyStarted`、`kNotInRoom`（`BattleManager` 侧）。

### 7.2 Push：`kBattleStatePush` (4006)

开战广播示例：

```text
battle_state:started:{room_id}:{player_count}
```

### 7.3 `kBattleInputRequest` (4003) / `kBattleInputResponse` (4004)

- 前置：会话须在房间且具备 `login_context`；否则 `kNotInRoom`（非「未登录」专用码，因房间类 handler 未统一先验 auth）。
- 成功：`battle_input_accepted:{room_id}:{sequence}`。
- `BattleManager::submit_input`：
  - 无战斗 → `kBattleNotStarted`；
  - `user_id` 不在该场战斗参与者列表 → **`kPlayerNotInBattle`（v1.1.6）**。

### 7.4 Push：`kBattleInputPush` (4005)

```text
battle_input:{room_id}:{user_id}:{sequence}:{payload}
```

---

## 8. 版本与任务

- **对应维护任务**：`development-optimization.md` §11 **T02**（`v1.1.1` 明确字符串为主契约；**`v1.1.6` 冻结表 + 错误码语义**）。
- **前一版相关**：`v1.1.5` `docs/v1-business-fact-source.md`（职责边界）。
- **下一维护版**：`v1.1.17`（T16：冻结存储后端与 audit / replay **格式支持级别**）。**横切规范**：`docs/v1-cross-cutting-lifecycle-binding.md`（**v1.1.16** / T15）；**横切事实**：`docs/v1-cross-cutting-capabilities.md`（**v1.1.15** / T14）。**装配清单与受控语义**：`docs/v1-runtime-lifecycle.md`（**v1.1.13–v1.1.14** / T13）。**配置字段成熟度**：`docs/v1-config-maturity.md`（**v1.1.12**）。**二进制 admin**：`docs/v1-admin-audit-rules.md`（**v1.1.11**）。**成熟度冻结**：`docs/v1-governance-layers.md` **§6**（**v1.1.10**）。
