# v1.x 横切能力定位：持久化 / 回放 / 审计（v1.1.15 / T14）

## 1. 文档定位

- **任务**：落实 **`development-optimization.md` §11 T14** 与 **§9.43 / Persistence·Audit 路线图第一步**：把 **player store**、**battle replay**、**文本审计（`AUDIT_LOG`）** 从矩阵条目提升为一份可读的 **「接在哪些节点 / 不接在哪些节点」** 事实说明，避免「接口或示例存在」被误读成「主链已按生命周期统一绑定」。
- **单一事实源**：**成熟度等级**仍以 **`docs/v1-maturity-matrix.md`** **§6**（持久化 / 回放）与 **§4.4**（审计）为准；本文只补充 **跨三类能力的对照表** 与 **生命周期矩阵**，不与矩阵等级定义冲突。
- **不在本版承诺**：按节点 **收口横切动作**（登录是否落盘、结算是否产 replay 等）的 **规范矩阵** 见 **`docs/v1-cross-cutting-lifecycle-binding.md`（`v1.1.16` / T15）**；**格式与后端支持级别冻结**归 **`v1.1.17`（T16）**；**自动化回归**归 **`v1.2.4`（T20）**。

---

## 2. 三类能力一句话定位

| 能力 | 矩阵锚点 | v1.x 定位（执行摘要） |
|------|----------|----------------------|
| **Player 持久化**（`IPlayerStore` / JSON / SQLite） | §6 | **experimental**：接口与实现存在；**落盘时机由各 showcase 在停服回调中手写**，不是领域策略；**无**运行时后端选择配置键。 |
| **Battle 回放**（`IBattleReplayStore` / `ReplayPlayer`） | §6 | **reserved**：存储与读取工具存在；**`BattleManager::end_battle()` 主链不写 replay**；`battle_demo` 中 **`JsonFileBattleReplayStore` 已构造但未接入战斗结束路径**（注释为规划用语，以代码为准）。 |
| **审计**（`AUDIT_LOG` → `logs/audit.log`） | §4.4 | **experimental**：登录成功/失败、连接拒绝、ingress 限频、**L3 admin** `admin_invoke` 等路径已写；**整行仍为近似 JSON 文本**，**不是**稳定结构化日志后端；**shutdown / config_reload** 等多见于 **示例** `GracefulShutdown` / watcher 回调，**非**统一框架强制步骤。 |

---

## 3. 生命周期节点 × 当前接线（事实表）

下列仅描述 **当前仓库** 内常见路径；**「示例」** 表示仅在部分 `examples/*` 中存在，**复制粘贴到自己的进程装配才算接入**。

| 节点 / 触发点 | Player store | Battle replay 产出 | `AUDIT_LOG`（典型 `event`） |
|---------------|-------------|-------------------|----------------------------|
| **登录成功 / 失败** | ❌ 主链不落盘 | ❌ | ✅ `login_success` / `login_failure`（`LoginService`） |
| **连接拒绝**（容量 / 每 IP） | — | — | ✅ `connection_rejected`（`GatewayServer`） |
| **Ingress 限频拒绝** | — | — | ✅ `rate_limited`（`GatewayService`） |
| **二进制 Admin 请求** | — | — | ✅ `admin_invoke`（`AdminService`，键约定见 **`docs/v1-admin-audit-rules.md`**） |
| **战斗进行中 / 结算** | ❌ | ❌ **主链不调用** `save_replay` | ❌ **无**统一审计钩子 |
| **配置热更新回调** | ❌ | — | **示例**：`config_reload`（`echo` / `login_demo` / `admin_demo` 等） |
| **优雅停服回调** | **示例**：`JsonFilePlayerStore::save` 遍历会话（`echo` / `login_demo` / `admin_demo`） | ❌ | **示例**：`shutdown`（同上）；**`battle_demo` / `room_demo` 无** `GracefulShutdown` 装配时不适用 |

**与 `development-optimization.md` §9.43 的对照**：当前正是「登录侧偏审计、停服侧偏演示型持久化、battle 侧未闭环 replay」的 **散装** 状态；**不是缺陷报告**，而是 **集成前必须知晓的边界**。

---

## 4. 示例入口差异（维护用）

| 入口 | 停服 `PlayerStore::save` | `GracefulShutdown` + `AUDIT_LOG("shutdown")` | Replay 相关 |
|------|-------------------------|-----------------------------------------------|-------------|
| `echo` / `login_demo` / `admin_demo` | ✅ | ✅ | — |
| `battle_demo` | ❌ | ❌ | 仅构造 `replay_store`，**未**接 `end_battle` |
| `room_demo` | ❌ | ❌ | — |

完整启动 / reload / shutdown 清单见 **`docs/v1-runtime-lifecycle.md`**（**v1.1.13–v1.1.14**）。

---

## 5. 与 roadmap 的关系

| 版本 | 内容 |
|------|------|
| **`v1.1.15`**（本文） | **T14**：三类能力 **定位 + 生命周期事实矩阵** |
| **`v1.1.16`**（T15） | **`docs/v1-cross-cutting-lifecycle-binding.md`**：节点 **N1–N7** × 审计 / 持久化 / replay **规范矩阵** + showcase 自检清单 |
| **`v1.1.17`**（T16） | 冻结存储后端与 audit / replay **格式支持级别** |
| **`v1.2.4`**（T20） | persistence / audit / replay **回归测试**加固 |
