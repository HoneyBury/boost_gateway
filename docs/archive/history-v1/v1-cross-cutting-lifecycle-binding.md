# v1.x 横切动作生命周期绑定规范（v1.1.16 / T15）

## 1. 文档定位

- **任务**：落实 **`development-optimization.md` §11 T15** 与 **Persistence·Audit 路线图第二步**：把 **审计**、**player 持久化**、**battle replay 产出** 绑定到 **具名生命周期节点**，形成维护期 **「应收口在哪里」** 的规范表；**不是**声称主链已全部实现。
- **与 T14 的关系**：**事实现状**（当前代码接在哪）见 **`../history-v1/v1-cross-cutting-capabilities.md`**；本文写 **应收口 / 收敛方向**。二者冲突时以 **T14 代码事实** 为准，本文条目标记 **「主链已有」** / **「showcase 约定」** / **「reserved（待接线）」**。
- **不在本版**：存储后端与日志 **格式支持级别** 的冻结归 **`v1.1.17`（T16）**；自动化回归归 **`v1.2.4`（T20）**。本文 **不改变** 矩阵 §4.4 / §6 的 **`stable` / `experimental` / `reserved` 等级**。

---

## 2. 生命周期节点（词汇表）

| 节点 | 含义（边界） |
|------|----------------|
| **N1** | **接入拒绝**：`GatewayServer` 因 **`max_connections`** / **`per_ip_connection_limit`** 拒绝新连接 |
| **N2** | **Ingress 限频拒绝**：`GatewayService` ingress middleware 判定超限 |
| **N3** | **登录结果**：`LoginService` 校验完成后的成功或失败路径 |
| **N4** | **L3 二进制 Admin**：`AdminService` handler **入口**（见 **`../history-v1/v1-admin-audit-rules.md`**） |
| **N5** | **战斗结算**：`BattleManager::end_battle()`（或语义等价的战斗结束边界）完成时 |
| **N6** | **配置热更新成功**：`ConfigWatcher` 回调 **且** `try_load_gateway_config` **成功**之后（应用层可选审计） |
| **N7** | **优雅停服**：`GracefulShutdown` 回调路径（与 **`../history-v1/v1-runtime-lifecycle.md` §4 / §7** 一致） |

> **未单独开行的节点**（如重复登录踢旧会话、开战瞬间）在 **v1.x** 仍归 **业务编排**；若需审计 / 落盘，集成方应在 **handler / 助手** 内自行挂载，**框架不在本表单列强制项**。

---

## 3. 节点 → 横切动作（维护期规范矩阵）

**列语义**：**应有** = 维护期认为该节点 **应当** 具备该类观测或副作用（若能力已装配）；**不适用** = 不强求；**绑定点 reserved** = **规范指定接线位置**，主链或 showcase **可能尚未实现**（见 T14）。

| 节点 | 审计（`AUDIT_LOG`） | Player 持久化（`IPlayerStore`） | Battle replay 产出（`IBattleReplayStore`） |
|------|---------------------|---------------------------------|-------------------------------------------|
| **N1** | **应有**（主链已有：`connection_rejected`） | 不适用 | 不适用 |
| **N2** | **应有**（主链已有：`rate_limited`） | 不适用 | 不适用 |
| **N3** | **应有**（主链已有：`login_success` / `login_failure`） | **可选**：v1.x **不要求**登录点写盘；档案类持久化仍以 **N7** 或领域自选节点为准 | 不适用 |
| **N4** | **应有**（主链已有：`admin_invoke`；键约定见 **`v1-admin-audit-rules.md`**） | 不适用 | 不适用 |
| **N5** | **推荐**：战斗结束审计（如 `battle_end`）；**主链当前未统一** → **绑定点 reserved** | **可选**（非框架契约） | **绑定点 reserved**：**若**启用回放管线，应在 **结算边界** 序列化并 **`save_replay`**；当前 **`end_battle()` 不调用** |
| **N6** | **可选**（showcase：`config_reload`） | 不适用 | 不适用 |
| **N7** | **推荐**（showcase：`shutdown`） | **推荐**：若进程装配了 player store，宜在 **`server.stop()` 之前** **best-effort** 写入与会话关联的快照（与 **`echo` / `login_demo` / `admin_demo`** 模式一致） | 不适用（无统一「停服 flush replay buffer」）

---

## 4. 横切能力 → 主归属节点（反向索引）

| 能力 | 主归属节点 | 说明 |
|------|------------|------|
| **接入 / 限频审计** | **N1**、**N2** | 主链已接线 |
| **登录审计** | **N3** | 主链已接线 |
| **Admin 最小审计** | **N4** | **T11** 契约 |
| **Player 演示型归档** | **N7**（首选）；**N3** 仅可选 | 与 **`v1-cross-cutting-capabilities.md` §3** 一致 |
| **Battle replay 生产** | **N5**（唯一规范绑定点） | 读取 / 工具类见矩阵 §6 **`reserved`** |
| **停服 / 热更观测** | **N7**、**N6** | 以 showcase 与运维约定为准，非结构化日志后端 |

---

## 5. Showcase 收敛检查清单（维护用）

集成型入口（**`echo` / `login_demo` / `admin_demo`**）若宣称与 **T13 / T14 文档**一致，建议自检：

1. **N7**：`GracefulShutdown` 顺序遵循 **`v1-runtime-lifecycle.md` §4**；宜含 **`AUDIT_LOG("shutdown", …)`** 与（若启用）**player store** 保存。
2. **N6**：若在 reload 回调写 **`config_reload`**，应与 **`try_load` 成功语义**（**v1.1.14**）一致，避免失败路径误写「成功重载」语义。
3. **`battle_demo` / `room_demo`**：未装配 **N7** 全套装配 **不违规**，但 **不得**在 README 中暗示已具备与 echo 同级的停服持久化 / 审计。

**`battle_demo`** 若宣传「回放录制」而未在 **N5** 接线 **`save_replay`**，属于 **文档 / 注释与代码不一致**，应以代码为准并逐步收敛注释（工程清理项，非本版强制改代码）。

---

## 6. 与 roadmap 的关系

| 版本 | 内容 |
|------|------|
| **`v1.1.15`**（T14） | **`v1-cross-cutting-capabilities.md`**：现状事实矩阵 |
| **`v1.1.16`**（本文 / T15） | **应收口规范矩阵** + showcase 自检清单 |
| **`v1.1.17`**（T16） | **`../history-v1/v1-cross-cutting-data-formats.md`**：后端与 **audit / replay 格式** 支持级别冻结 |
| **`v1.2.4`**（T20） | persistence / audit / replay **回归测试** |
