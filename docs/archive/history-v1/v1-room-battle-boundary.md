# v1.x 房间态与战斗态边界（v1.1.8 / T09 + T06②）

## 文档定位

本文对应 **`v1.1.8`**：收紧 **Room** 与 **Battle** 的职责边界（`development-optimization.md` §「第四步」与 §11 **T06** / **T09**）。**不改变**对外字符串协议（仍以 `../history-v1/v1-string-protocol.md` 为准）。

---

## 1. 简明状态关系表

| 维度 | RoomManager | BattleManager (`active_battles_`) |
|---|---|---|
| **存什么** | 房间是否存在、`Session` 成员、`ready`、`member_user_id` 缓存 | 每场战斗的 `player_ids`（`user_id`）、输入历史等 |
| **是否在战斗中（派生视图）** | `battle_started` 经由 `set_battle_active_query` ← **仅以 BattleManager 为准** | `battle_started(room_id)` |
| **房空** | `rooms_` 中摘除房间 | **`clear_battle_if_room_empty`**（见 `v1-cross-domain-flows.md` §B） |
| **战斗结束 (`end_battle`)** | 不改变房间 membership | 移除该 `room_id` 的战斗条目 |

---

## 2. `transfer_session()` 契约（T09）

- **用途**：仅此支撑 **同一 `user_id` 顶号**：旧 `Session` 断开前，把其在 **RoomManager** 内的席位迁到新 `Session`。**不是**通用「换绑玩家身份」API。
- **战斗中**：**允许**。战斗校验与输入路由以 **`user_id`** 为主键（`BattleManager::player_ids` / `submit_input(user_id)`），与新 `Session` 句柄解耦；顶号后新连接重新登录，`member_user_id` **随 `RoomMember` 一并迁移**（v1.1.8）。
- **禁止**：在未登录、未建立 `LoginContext` 的路径上滥用「仅迁 Session」来等价于换人——主链不负责这种语义。

---

## 3. 房间广播与开战列表：`member_user_id`

自 **v1.1.8**：

- `RoomMember::member_user_id` 由 **`RoomService`** 在 **create/join 成功**且已认证后写入（与当前 `LoginContext.user_id` 对齐）。
- `RoomService::build_room_state_body`、`BattleService` 开战前收集 **`player_ids`**：**优先**用 `member_user_id`，为空时再 **`login_context_of`**（兼容未经过 `RoomService` 写入的演示装配）。

---

## 4. `end_battle` / 回放 / 观战

| 能力 | v1.x 成熟度（详见矩阵） |
|---|---|
| `end_battle` | 仅结束战斗上下文；不改变房间是否在 |
| 回放生产链 | reserved |
| 观战协议 | reserved |

---

## 5. 与其它文档的关系

- **跨域编排**（顶号 kick、空房清理）：`../history-v1/v1-cross-domain-flows.md`
- **业务模块叙述**：`../history-v1/v1-business-fact-source.md`
- **逐项成熟度**：`../history-v1/v1-maturity-matrix.md`
