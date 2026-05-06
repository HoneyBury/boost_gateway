# v1.x 跨域编排流程（单一入口说明）

## 文档定位

本文档配合 **`v1.1.7`**（`development-optimization.md` §11 **T07 / T08**），把散落在多处的「登录顶号」「空房清理」编成**可查的统一流程说明**，并与**代码单一策略入口**对应。

冻结的字符串协议仍以 **`docs/v1-string-protocol.md`** 为准；本文不重复每条 body，只描述**调用顺序与职责边界**。

---

## A. 重复登录恢复链（T07）

### 语义

同一 `user_id` 在新连接上登录成功 → 旧连接被顶替 →（若旧连接在房间内）**房间席位迁移到新会话** → 旧连接收到 **顶号 kick push** → 新连接收到 **登录成功响应** 与可选 **`session_resumed` push**。

### 单一事实与代码入口

| 步骤 | 责任模块 | 实现位置 |
|---|---|---|
| 认证与 `user_index` 顶号 | `SessionManager` | `SessionManager::authenticate` |
| 房间 `SessionPtr` 从旧换新 | `RoomManager` | `RoomManager::transfer_session` |
| 顶号时何时迁移房间、如何拼 `login_ok` / `session_resumed` | `LoginService` + **辅助** | `src/game/login/login_service.cpp` 调用 **`game/login/login_recovery.{h,cpp}`**：`transfer_room_for_duplicate_login`、`build_login_room_notify_paths` |
| 发送 push / 停旧连接 | `LoginService` | `login_service.cpp`（行为与 v1.1.6 前一致，仅提取重复逻辑） |

### 不在此链内扩展的点

- **不在** `GatewayServer` 里做顶号房间迁移（保持网络层与业务顶号解耦）。
- **`session_resumed` 的 `battle=0/1`** 仍来自 `RoomSnapshot::battle_started` 派生视图（`BattleManager` 为 SSOT，见 `v1.1.4`）。

---

## B. 空房战斗清理链（T08）

### 语义

当某 `room_id` 在 **`RoomManager` 中已无任何成员**（主动离队或断线 `remove_session` 导致房间被拆除）时，应 **移除 `BattleManager` 中该房间的战斗上下文**，避免「孤儿战斗态」。

### 单一策略入口

**`game::room::clear_battle_if_room_empty`**

定义于：

- `include/game/room/room_battle_lifecycle.h`
- `src/game/room/room_battle_lifecycle.cpp`

### 调用方（须在房间成员关系更新之后）

| 场景 | 调用位置 |
|---|---|
| 客户端主动离队 | `RoomService`：`leave_room` 成功且已更新 `RoomManager` 后 |
| TCP 会话关闭 | `GatewayServer`：`room_manager.remove_session` **之后**，对离开的会话曾所在的 `room_id` 调用 |

**禁止**：在其它路径手写 `battle_manager.remove_room(room_id)` 的「member_count == 0」分支而与上述策略并行（应保持单一入口，便于审计与 **T09** 之后的边界迭代）。

---

## C. 与后续版本的关系

- **`v1.1.8` / T09 / T06②**（房战边界、`transfer_session`、`member_user_id`）：**`docs/v1-room-battle-boundary.md`**
- **`v1.1.9` / T10** — 治理入口分层：**`docs/v1-governance-layers.md`** §1–§5
- **`v1.1.10`** — 治理成熟度冻结（文档与示例用语）：同文 **§6**
- **`v1.1.11` / T11** — **`docs/v1-admin-audit-rules.md`**（admin **调用前提** + **`admin_invoke`** 最小审计）；**运行时 ACL**：仍 `reserved`
- **`v1.1.12` / T12** — **`docs/v1-config-maturity.md`**（`GatewayAppConfig` 启动/热更新/预留；`ConfigWatcher` 现状）
- **`v1.1.13` / T13** — **`docs/v1-runtime-lifecycle.md`**（启动 / reload / shutdown 清单；showcase **`io.stop()`**）
- **开战编排链**：仍主要在 `BattleService` + `RoomService`/`RoomManager`；若将来提取独立助手，再追加专节并指向实现文件。
