# v1.x 业务线职责与事实源校准

## 文档定位

本文件对应维护路线 **`v1.1.5`**（参见 `development-optimization.md`「第一步：先统一业务事实源与模块定位」），在 **`docs/v1-maturity-matrix.md`** 的逐项表格之上，用文字说明 **login / room / battle** 三条业务线在 `v1.x` 中的**边界、事实源与派生视图**，避免「各 service 都能改状态、文档各写各的」继续扩散。

读者若只关心「某能力是否 stable」，仍以 **矩阵**为准；本文负责回答「为什么这么标 / 模块之间谁说了算」。

---

## 1. 四个验收问题（必须能明确回答）

### 1.1 登录成功是否等于「恢复完成」？

**否。**

- **`LoginService` 的事实源职责**：在 `SessionManager` 里建立 **authentication**（`LoginContext`：user_id / display_name）、处理顶号与 `kSessionKickedPush`，并发出 **`kLoginResponse`**。
- **`恢复态`（房间 / 战斗快照、恢复类 push）**：是**编排层**在登录成功**之后**追加的：例如 `room_snapshot_of` / `RoomSnapshot::battle_started`（经 `set_battle_active_query` 对齐 `BattleManager`）参与拼 `login_ok` 与 `session_resumed`。**一次登录响应里可以同时出现「鉴权成功」与「恢复线索」，但语义上仍是「先身份、后恢复」两件事。**
- 因此：**「login_ok」≠「所有恢复动作已跑完且与长期状态永久一致」**；顶号 + 房间迁移等路径下，恢复是**尽力同步的桥接**，详见矩阵中 `transfer_session`、房间快照补全身份等 **experimental** 项。

### 1.2 房间成员是按 `Session` 还是按稳定玩家身份建模？

**运行时主链以 `Session` / `Session*` 为_room 成员键；稳定身份（user_id）来自 `SessionManager` 登录态，在协议与广播里再补全。**

- `RoomManager` 的成员表键是 **`Session` 指针**，不是 `user_id` 主键——这是 **v1.x 工程现实**（与异步连接生命周期一致）。
- 房间状态广播、快照里若需要 **`user_id`/显示名**，路径是 **回查 `SessionManager::login_context_of(session)`**（矩阵已标为 **experimental / 不自洽**）：即「房间数据结构 + 会话登录态」组合的**派生视图**。
- **业务上**我们仍说「房间语义是玩家与房间的关系」：在 v1.x 里这层关系**通过「已认证 Session ↔ user_id」映射**落地，而不是独立 player 表。

### 1.3 战斗是独立实体还是房间附属状态？

**在 v1.x 主链中是「按 `room_id` 挂接的战斗上下文」，不是独立 match 实体。**

- **`BattleManager`** 以 `room_id` 为 key 管理 `active_battles_`，与 `RoomManager` 的「房间是否存在」**松耦合**（开局时由 `BattleService` 与房间资格校验共同约束）。
- **不搞成**「独立 battle_id 的生命周期服务」；观战、回放、长局帧同步等在矩阵中多为 **reserved / experimental**。
- **`battle_started` 唯一事实源**：自 **`v1.1.4`** 起 **`BattleManager::battle_started(room_id)`**（由 `active_battles_` 反映）；`RoomManager` **只通过 `set_battle_active_query` 查询派生**，不再双写。

### 1.4 `battle_started` 以哪一处状态为准？

**只以 `BattleManager` 为准。** 房间侧快照与 `kRoomInBattle` 均来自 `set_battle_active_query` → `BattleManager::battle_started`。详见 `v1-maturity-matrix.md` §3.3 与 **`v1.1.4`** 变更说明。

---

## 2. 模块职责一句话

| 模块 | v1.x 主职责 | 明确不做 / 非主链 |
|------|-------------|-------------------|
| **Login**（`LoginService` + 校验器） | 建认证、发登录响应、顶号与踢线协助 | 不负责长期玩家档案真源；不负责房间/战斗**长期**状态写入 |
| **Room**（`RoomService` + `RoomManager`） | 玩家与房间 membership、房主、准备、离开；`transfer_session` **桥接** | 不持有独立 `battle_started` 真源；不负责完整「独立战场」域模型 |
| **Battle**（`BattleService` + `BattleManager`） | 基于 `room_id` 的上下文、输入、帧推进、结算演示 | 非通用战斗引擎；观战/回放协议未主链闭环 |

---

## 3. 与路线图版本的关系

| 版本 | 关系 |
|------|------|
| `v1.1.4` | 已在代码层落实 **「battle_started 只信 BattleManager」** |
| **`v1.1.5`** | **本文档** + 矩阵/导航交叉引用，校准**叙述** |
| `v1.1.6+` | 协议表冻结、错误码对齐、跨域编排等见 `development-optimization.md` 第二、三步 |

---

## 4. 强约束（边界）

- 本文及矩阵未标为 **stable** 的能力，不得对外宣称为生产级完备能力。
- **`v2.0.0`** 的 Actor/ECS/集群等**不在** `v1.1.5` 讨论范围；请参阅 `docs/v2-roadmap.md` 且仅在 **`v1.2.0` 决策点**之后启动相关开发。
