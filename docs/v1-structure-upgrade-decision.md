# v1.2.0 结构升级决策（T21）

> **定位**：这是 `v1.x` 维护期的**决策记录**，不是 `v2.0.0` 开发入口。结论仅用于回答：在 `T17`–`T20` 完成后，`typed protocol` / `internal bus` / `battle replay` 是否应在当前维护分支转正。

## 1. 决策前提

本次评估以以下收口结果为前置条件：

- `T17`：业务边界测试已覆盖 login / room / battle 主链的错误路径与消息交错。
- `T18`：治理边界测试已覆盖默认装配不注册 admin、HTTP 管理端点固定行为、admin 最小审计键。
- `T19`：生命周期与装配测试已覆盖 `ConfigWatcher` 成败 reload 语义，以及 `GatewayServer::stop()` 的连接/房间态收口。
- `T20`：持久化 / 审计 / 回放测试已覆盖 JSON player store、`.replay` 读写、`ReplayPlayer` 读侧、`AUDIT_LOG` 近似 JSON 行。

换言之，当前维护分支已经完成了“先补保护网，再谈结构升级”的前置准备。

## 2. 评估对象

### 2.1 typed protocol

现状：

- `net::msg` / `message_serializer` 仍未进入业务主链。
- `v1.x` 实际线上契约仍是字符串 body，相关事实源见 `docs/v1-string-protocol.md`。

结论：

- **不在当前维护分支转正**。
- 保持 `experimental` 草案定位；如未来推进，应以单独批次替换主链协议事实源，并重做业务兼容回归。

### 2.2 internal bus / backend router

现状：

- `InternalBus` 仍与客户端分发链共享 envelope / middleware 模型。
- `ServiceRouter` / `BackendRouter` 仍不具备可宣称的拆服控制面能力。

结论：

- **不在当前维护分支转正**。
- 保持 `experimental` / `reserved` 定位；后续若推进，必须先定义独立 envelope、治理边界和跨进程语义，而不是在 `v1.x` 维护线上渐进混入。

### 2.3 battle replay 闭环

现状：

- `JsonFileBattleReplayStore` 与 `ReplayPlayer` 可用于读写已有 replay 文件。
- `BattleManager::end_battle()` 仍不产出 replay，主链未闭环。

结论：

- **不在当前维护分支补成正式能力**。
- 维持 `reserved`；当前维护期只冻结格式事实与测试读写行为，不把 replay 生产链包装成已完成能力。

## 3. 最终决策

本次 `T21` 的决策是：

1. **不**在当前 `develop` 维护分支推进 `typed protocol` 转正。
2. **不**在当前 `develop` 维护分支推进 `internal bus / backend router` 转正。
3. **不**在当前 `develop` 维护分支推进 `battle replay` 生产链闭环。
4. `v1.x` 继续以“事实源清晰、生命周期可测、边界有护栏”为交付目标。

这意味着：

- 当前分支的完成定义是“2.0 前维护准备工作做好”，不是“提前吸收 2.0 的结构升级内容”。
- `docs/v1-maturity-matrix.md` 中相关能力的 `experimental` / `reserved` 口径维持不变。
- `docs/v2-roadmap.md` / `docs/v2-design.md` 继续作为后续结构演进的独立输入，而不是本分支的开发任务单。

## 4. 后续约束

- 若后续要推进上述任一结构升级，必须以**新的明确批次**重新立项。
- 新批次必须重新声明：
  - 目标能力
  - 对 `v1.x` 客户端协议的兼容策略
  - 新旧主链切换条件
  - 回归测试扩展范围

在此之前，`v1.x` 维护分支不再把这些能力向前推进为“正式完成态”。
