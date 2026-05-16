# 更新日志

## v3.3.2 — 版本与交付面收束（2026-05-16）

> **范围**：启动 `v3.x` 生产就绪阶段的第一批收口工作，先统一版本口径、安装目标和发布清单，不扩展新的业务能力。

### 代码与构建

- `CMakeLists.txt`：顶层 `project(boost_gateway VERSION ...)` 更新到 `3.3.2`，与当前主线 `v3.3.x` 文档口径对齐。
- `install(TARGETS ...)`：补齐 `v2_match_backend`、`v2_leaderboard_backend`，使 README 中列出的 backend 入口与实际安装产物一致。
- `install(FILES ...)`：安装 `docs/v3-production-readiness-plan.md` 与 `docs/v3-release-checklist.md`。

### 文档

- `README.md`：明确当前主线已进入 **v3.x 生产就绪加强阶段**，增加对应版本行与文档入口。
- **新增** `docs/v3-production-readiness-plan.md`：定义 12 周生产就绪收口计划，覆盖性能数据闭环、架构实测、Actor 多核线程边界、通信契约、控制面和发布门槛。
- **新增** `docs/v3-release-checklist.md`：定义 v3.x 阶段的版本口径、安装产物、配置脚本、控制面入口和发布阻断条件。
- **新增** `scripts/collect_v2_perf_baseline.py`：跨平台 v2 多进程基线采集入口，统一启动 backend/gateway、运行压测、抓取 diagnostics 和进程资源快照、落盘 `runtime/perf/<timestamp>/`。
- `scripts/collect_v2_perf_baseline.ps1`：调整为 Windows 包装器，调用同一份 Python 主逻辑，避免脚本双份漂移。
- `docs/README.md`、`docs/v2-enterprise-roadmap.md`：把 v3.x 生产就绪规划与发布清单纳入当前主线文档索引。
- `docs/performance-baseline.md`：将 Python 跨平台脚本纳入标准采集入口。

### 测试

- 本次为 R0 文档与构建面收口，未新增运行时行为改动。
- 未执行构建与测试，下一步进入性能基线与架构实测闭环。

## v1.2.5 — CI / Docker / 发布链路稳定性修复（2026-05-08）

> **范围**：不扩展 `v1.x` 业务能力，不进入 `v2.0.0` 开发；仅补齐稳定发布所需的 CI、Docker 与测试发现可靠性问题。

### 代码

- 暂无业务主链结构变更。

### 文档

- `README.md`、`docs/README.md`、`docs/release-process.md`：统一当前版本口径到 `v1.2.5`。
- **新增** `docs/releases/v1.2.5.md`：记录稳定性补丁版定位。
- **新增** `docs/v2-startup-checklist.md`：整理从当前 `develop` 启动 `v2.0` 的清单。

### 发布工程

- CI：稳定 Windows runner 与 GoogleTest discovery timeout。
- Docker：修正依赖下载与 Boost 压缩包 locale 处理。
- `CMakeLists.txt`：版本升至 `1.2.5`，安装包携带 `docs/releases/v1.2.5.md`。

### 测试

- 本版以既有回归面为基线，重点提升 CI 环境稳定性。

## v1.2.4 — 生命周期/横切测试闭环 + T21 决策记录（2026-05-07）

> **范围**：完成 `T18`–`T20` 测试收口，并记录 `T21` 结构升级决策。**不进入 `v2.0.0` 开发**；当前维护分支仅补齐治理、生命周期装配、持久化/审计/回放的回归护栏。

### 代码

- `tests/unit/admin_service_test.cpp`：校验 admin callback 调用与 `admin_invoke` 最小审计键。
- `tests/integration/http_management_test.cpp`：校验 `/health` 固定返回、未知路径 `404`、非 `GET` 请求 `405`。
- `tests/integration/gateway_integration_test.cpp`：校验默认装配不注册 admin，以及 `GatewayServer::stop()` 的连接/房间态收口。
- `tests/unit/lifecycle_assembly_test.cpp`：覆盖 `ConfigWatcher` reload 成败语义与停用后的回调抑制。
- `tests/unit/persistence_replay_audit_test.cpp`：覆盖 `JsonFilePlayerStore`、`.replay` 读写、`ReplayPlayer` 与 `AUDIT_LOG` 行格式。

### 文档

- **新增** `docs/v1-structure-upgrade-decision.md`：记录 `T21` 决策，明确 `typed protocol` / `internal bus` / `battle replay` **不**在当前维护分支转正。
- `docs/README.md`、`docs/development-priority.md`：同步 `T18`–`T21` 完成状态与约束。
- **新增** `docs/release-process.md`、`docs/releases/v1.2.4.md`：固定 `develop -> main` 合并、三平台 CI/CD、tag release 与归档说明。

### 发布工程

- `.github/workflows/ci.yml`：改为 Linux / macOS `default` + Windows `windows-msvc-debug` 的三平台矩阵。
- `.github/workflows/release.yml`：改为 tag 触发的三平台构建 / 测试 / install / 打包 / GitHub Release 上传流程。
- `CMakeLists.txt`、`CMakePresets.json`：版本升至 `1.2.4`，补 `install()` 规则与 release test preset。

### 测试

- 新增治理、生命周期装配、持久化/审计/回放回归面。

---

## v1.2.1 — 业务边界测试加固（T17）(2026-05-07)

> **范围**：**`BattleManager` / `RoomManager` 单元测试** + **`gateway_integration_test`** 集成路径；覆盖房主开战、全员就绪、单人房间 **`not_enough_players`**、未开战 **`battle_input`**；辅助函数 **`read_until_message`** 跳过 **`kRoomStatePush`** 与就绪响应的交错到达。

### 代码

- `tests/unit/battle_manager_test.cpp`、`tests/unit/room_manager_test.cpp`、`tests/integration/gateway_integration_test.cpp`。

### 文档

- 矩阵 §8 / §10；`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`CHANGELOG.md`、`development-log.md`。

### 测试

- `ctest`：**81/81**。

---

## v1.1.17 — 横切数据格式与后端支持级别（T16）(2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-cross-cutting-data-formats.md`**：`JsonFilePlayerStore` / **`HAS_SQLITE`** `SqlitePlayerStore`、**`.replay` 载荷**、**`ReplayPlayer`** JSON 读侧契约、**`AUDIT_LOG`** 行模板与 **格式脆弱性**；与矩阵 §6 / §4.4 交叉引用。

### 文档

- **`docs/v1-cross-cutting-data-formats.md`**；`docs/v1-maturity-matrix.md` §6 引言、§4.4、§10；**`docs/v1-runtime-lifecycle.md`** §1；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、`v1-cross-cutting-capabilities.md` §5、`v1-cross-cutting-lifecycle-binding.md` §6、`CHANGELOG.md`、`development-log.md`。

### 测试

- **无代码变更**；`ctest` **68/68**。

---

## v1.1.16 — 横切动作生命周期绑定规范（T15）(2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-cross-cutting-lifecycle-binding.md`**：节点 **N1–N7** × 审计 / player 持久化 / battle replay **应收口规范矩阵**；showcase 自检清单；与 **T14 事实**文档交叉引用。

### 文档

- **`docs/v1-cross-cutting-lifecycle-binding.md`**；**`docs/v1-cross-cutting-capabilities.md`** §1 / §5；**`docs/v1-runtime-lifecycle.md`** §1 指针；`docs/v1-maturity-matrix.md` §6 引言、§10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、`CHANGELOG.md`、`development-log.md`。

### 测试

- **无代码变更**；`ctest` **68/68**。

---

## v1.1.15 — 横切能力定位：持久化 / 回放 / 审计（T14）(2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-cross-cutting-capabilities.md`**：三类横切能力与业务生命周期节点的 **当前接线事实矩阵**（不等同于 T15「收口」）；矩阵 **§6**、**§4.4** 增加指针。

### 文档

- **`docs/v1-cross-cutting-capabilities.md`**；`docs/v1-maturity-matrix.md` §6 引言、§4.4、§10；`docs/v1-config-maturity.md` §5；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`CHANGELOG.md`、`development-log.md`。

### 测试

- **无代码变更**；`ctest` **68/68**（与 **v1.1.14** 二进制一致，冒烟确认）。

---

## v1.1.14 — 受控 reload / shutdown 语义（T13 后半）(2026-05-06)

> **范围**：**`ConfigWatcher`** 仅在 **`try_load_gateway_config`** 成功时调用 reload 回调（失败则 WARN、**不**回调，避免默认配置误触 **`set_connection_limits`**）。**`docs/v1-runtime-lifecycle.md`**：**§6** reload 成败语义；**§7** shutdown 最小保证与仍为 **reserved** 的分界。

### 代码

- `include/app/config.h`、`src/app/config.cpp`：`try_load_gateway_config`、`fill_gateway_from_store`；`load_gateway_config` 失败日志文案（*not found or invalid*）。
- `include/app/config_watcher.h`：`check_and_reload` 使用 **`try_load_gateway_config`**。
- `tests/unit/config_test.cpp`：缺失文件 / 坏 JSON 时 **`nullopt`** 用例。

### 文档

- **`docs/v1-runtime-lifecycle.md`**（§5–§8 顺序与交叉引用）；矩阵 §5 / §10；`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §6、`docs/README.md`、`development-log.md`。

### 测试

- `ctest`：**68/68**。

---

## v1.1.13 — 标准运行时装配 / shutdown 顺序（T13）(2026-05-06)

> **范围**：**`docs/v1-runtime-lifecycle.md`**（启动 / reload / shutdown 清单）；**`examples/echo`**、`login_demo`、`admin_demo` 在 **`GracefulShutdown`** 回调中 **`watcher.stop()` + `server.stop()` + `io_context.stop()`**，避免信号停服后 IO 线程无法 **`join`**。

### 代码

- `examples/echo/server_main.cpp`、`examples/login_demo/login_demo_main.cpp`、`examples/admin_demo/admin_demo_main.cpp`。

### 文档

- **`docs/v1-runtime-lifecycle.md`**；矩阵 §5 / §10；`docs/README.md`、`v1-config-maturity.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`development-log.md`。

### 测试

- `ctest`：**66/66**。

---

## v1.1.12 — 配置字段成熟度（T12）+ v2 设计文档入库 (2026-05-06)

> **范围**：**文档**。新增 **`docs/v1-config-maturity.md`**（`GatewayAppConfig` 字段表 + 热更新/`ConfigWatcher` 叙事）；**`docs/v1-maturity-matrix.md` §5.1** 改为指向该文；节拍与 playbook 指针同步。**`docs/v2-design.md`** 纳入仓库（v2 草案，**不**代表已进入 v2 实施）。

### 文档

- **`docs/v1-config-maturity.md`**；矩阵 §5.1 / §10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`development-log.md`。
- **`docs/v2-design.md`**（既有草案文本，本次首次跟踪）。

### 测试

- `ctest`：**66/66**。

---

## v1.1.11 — 二进制 Admin 最小规则（T11）(2026-05-06)

> **范围**：**`docs/v1-admin-audit-rules.md`**（调用前提 / 动作语义 / `admin_invoke` 审计键）；`AdminService::register_handlers` 迁入 **`admin_service.cpp`** 并在每条 admin 请求上写 **`AUDIT_LOG(admin_invoke, …)`**。**不引入**令牌/角色 ACL，**不改变** `kAdminResponse` 成功/失败细分策略。

### 代码

- `src/game/gateway/admin_service.cpp`（新建）、`include/game/gateway/admin_service.h`、`src/game/gateway/CMakeLists.txt`。
- `examples/admin_demo`：移除与边界重复的 kick/ban `AUDIT_LOG`。

### 文档

- **新增** **`docs/v1-admin-audit-rules.md`**；矩阵 §4.2 / §4.4 / §9 / §10；`docs/README.md`、`development-priority.md`、`development-log.md`、`runtime-playbook.md`、`v1-governance-layers.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、根 `README.md`。

### 测试

- `ctest`：**66/66**。

---

## v1.1.10 — 治理成熟度冻结（文档 + 示例用语）(2026-05-06)

> **范围**：落实 **`development-optimization.md`** 路线图**第二步**验收点：不再让「消息号存在 / 示例已接线」被误读为 **正式、可依赖**的治理能力；**不改变** TCP、HTTP、admin 的默认行为。

### 文档

- **`docs/v1-governance-layers.md`**：新增 **§6**（Admin / `HttpManager` / `GatewayServer` 装配的**禁止暗示**与事实表）；文首版本范围更新为 **v1.1.9–v1.1.10**。
- `docs/v1-maturity-matrix.md` §4 引言与 §10 版本表；`development-priority.md`；`docs/README.md`；`v1-string-protocol.md` / `v1-cross-domain-flows.md` 后续版本指针；`runtime-playbook.md`（含 **HTTP 观测端点**小节标题与 **§6** 引用）；根 `README.md`。

### 示例与头文件注释

- `examples/admin_demo/admin_demo_main.cpp`、`examples/login_demo/login_demo_main.cpp`：注释与日志用语与 **§6** 对齐。
- `include/game/gateway/admin_service.h`：标明 **demo-only** 与 **§6** 引用。

### 测试

- `ctest`：**66/66**。

---

## v1.1.9 — 治理入口分层（T10）(2026-05-06)

> **范围**：仅文档。**不改变** TCP ingress、`HttpManager`、二进制 `AdminService` 的默认接线或行为。

### 文档

- **`docs/v1-governance-layers.md`**：L0（`GatewayService` ingress）/ L1（业务 handler）/ L2（`HttpManager`）/ L3（`AdminService`）职责与边界；`/health` stub 与 `GatewayServer` 装配说明；与 v1.1.10 / v1.1.11 / v1.2.2（T18）的归属。
- `docs/README.md`、`v1-maturity-matrix.md` §4、`development-priority.md`（T10 **done**）同步。

### 测试

- `ctest`：**66/66**。

---

## v1.1.8 — 房间/战斗边界收紧（T09 + T06②）(2026-05-06)

> **范围**：**不改变**对外 body 形状；增强 **Room 侧身份缓存** 与 **房战状态文档**。战斗仍以 `user_id` 为主键（与 `Session` 解耦）。

### 核心变更

- `RoomManager::RoomMember::member_user_id` + `set_member_user_id`；**`RoomService`** 在 create/join 成功后写入当前登录 `user_id`。
- **`RoomService::build_room_state_body`**、**`BattleService` 开战 `player_ids`**：**优先** `member_user_id`，空则回退 `login_context_of`（兼容未走 `RoomService` 的装配）。
- **`transfer_session`**：注释明确**战斗中允许**迁移；单测 **`TransferSessionPreservesMemberUserId`**。

### 文档

- **`docs/v1-room-battle-boundary.md`**（状态表、`transfer_session` 契约、`end_battle` 与房间关系）；导航与 `v1-string-protocol.md` / `v1-cross-domain-flows.md` / 矩阵 §3.2 同步。

### 测试

- `ctest`：**66/66**。

---

## v1.1.7 — 跨域编排收口（T07 / T08）(2026-05-06)

> **范围**：**不修改**对外冻结的字符串协议（`docs/v1-string-protocol.md`）。将「顶号房间恢复」与「空房即清战斗」收口为**可查文档 + 单一策略函数**，避免 `GatewayServer` 与 `RoomService` 各写一份 battle 清理条件。

### T07 重复登录恢复链

- 新增 `include/game/login/login_recovery.h`、`src/game/login/login_recovery.cpp`：`transfer_room_for_duplicate_login`、`build_login_room_notify_paths`。
- `LoginService` 仅编排 push/响应，顶号房间迁移与 `login_ok`/`session_resumed` 拼装迁入上述模块。

### T08 空房 battle 清理链

- 新增 `include/game/room/room_battle_lifecycle.h`、`src/game/room/room_battle_lifecycle.cpp`：`clear_battle_if_room_empty`。
- `GatewayServer` 断线回调与 `RoomService::leave_room` 成功路径均调用该函数（替代分散的 `member_count==0` + `remove_room`）。

### 文档

- `docs/v1-cross-domain-flows.md`，`docs/README.md`、`development-priority.md`、`v1-maturity-matrix.md`、`runtime-playbook.md` 指针更新。

### 测试

- `RoomBattleLifecycleTest.ClearBattleWhenRoomBecomesEmptyAfterLeaves`
- `ctest`：65/65。

---

## v1.1.6 — 业务协议冻结（T02 后半）(2026-05-06)

> **范围**：`development-optimization.md`「第二步」——冻结 login / room / battle **字符串 body** 事实源；消除 battle 业务错误误用 **auth** 错误码；**对外 body 文本不变**（仍为 `player_not_in_battle`），**wire 上 `error_code` 变更**（兼容旧客户端需自知）。

### 协议与错误码

- 新增 `net::protocol::ErrorCode::kPlayerNotInBattle` = **3004**，`to_string` → **`player_not_in_battle`**。
- `BattleService`：`SubmitInputResult::kPlayerNotInBattle` 回复 **`kPlayerNotInBattle`**，不再使用 **`kAuthRequired`**。

### 文档

- **`docs/v1-string-protocol.md`** — 消息号、body 形态、`ErrorCode` 表、与 `net::msg` 分叉说明。
- **`docs/runtime-playbook.md`、`docs/v1-maturity-matrix.md`、`docs/README.md`、`development-priority.md`**：`v1.1.6` 交叉引用与矩阵更新。

### 测试

- `BattleManagerTest.SubmitInputUnknownPlayerReturnsNotInBattle`
- `ctest`：64/64 通过（相对 `v1.1.5` +1 单元测）。

---

## v1.1.5 — 业务事实源校准（叙事文档）(2026-05-06)

> **范围**：维护期 **`development-optimization.md`**「第一步」的**文档验收**——能明确回答登录 vs 恢复、席位建模、battle 与房间关系、`battle_started` SSOT。**无代码与协议变更**，运行时行为与 **`v1.1.4`** 一致。

### 新增文档

- `docs/v1-business-fact-source.md` — login / room / battle 三条线的职责边界与四个核心问答（与 `docs/v1-maturity-matrix.md` §3 互补）。

### 导航更新

- `docs/README.md`：`v1.x` 维护期文档列表增加本条链接。
- `docs/v1-maturity-matrix.md` §「业务层」首部增加对本文件的指引。
- `docs/development-priority.md`、`docs/runtime-playbook.md` §10：`v1.1.5` 收尾表述。

---

## v1.1.4 — `battle_started` 单一事实源（T06 第一阶段）(2026-05-06)

> **范围**：以 `BattleManager` 为房间内「是否在战斗中」的**唯一持久状态**；移除 `RoomManager` 内部的 `battle_started` 双写及 `BattleService` 成功后对 `RoomManager::mark_battle_started` 的回填。**不改变**对外字符串协议（如 `battle_started:{room}:{n}`、`session_resumed:…:battle=1`）。
>
> 对应 `docs/development-optimization.md` §11 任务表 **T06**（第一阶段）。

### 行为与设计

- `RoomManager`：删除 `RoomState::battle_started`、`mark_battle_started()`、以及仅靠房间局部存储的 `battle_started(room_id)`；`RoomSnapshot::battle_started`、`join_room`/`set_ready` 的 `kRoomInBattle` 分支改为调用可选的 **`set_battle_active_query(std::function<bool(const std::string&)>)`**，由各网关装配点在启动时绑定为：`[&battle_mgr](auto& id) { return battle_mgr.battle_started(id); }`。
- `BattleManager::battle_started(room_id)` / `active_battles_`**仍是唯一事实源**（与 `BattleService::start_battle` 成功写入一致）。
- `BattleService`：起战成功后**不再**调用 `room_manager_.mark_battle_started`。
- 未绑定 `set_battle_active_query` 的程序（纯房间演示）：房间侧战斗中视图恒视为未开战（与原行为在无战斗装配时等价）。

### 装配点

已在 `examples/*/…_main.cpp`、`tests/integration/*`（`gateway_integration_test`、`http_management_test`）、`examples/echo/server_main.cpp` 等同时具备 `RoomManager`+`BattleManager` 的入口加上述 wiring。

### 测试

- 单元：`RoomManagerTest.JoinAndReadyRejectedWhenBattleManagerMarksRoomInBattle`
- `ctest`：63/63 通过。

### T06 后续（仍属 roadmap，非本版必选）

- 与 **`v1.1.8`/T09**：`transfer_session` 在战斗中语义、空房与 `BattleManager.remove_room` 清理顺序等仍可继续收口。

---

## v1.1.3 — 入口治理前置 (2026-05-06)

> **范围**：`MessageDispatcher` 增加 **ingress** 中间件层，在投递到业务线程池**之前**同步执行；`GatewayService` 的白名单与限频迁至该层。**不修改业务协议、不改变白名单消息号集合、不触碰配置结构与治理 HTTP 分层**。
>
> 对应 `docs/development-optimization.md` §11 任务表的 **T05**。

### 行为变更

- 新增 `net::MessageDispatcher::register_ingress_middleware` / `ingress_middleware_count()`。
- 客户端连接的 `dispatch(session, …)`：`session != nullptr` 时先顺序执行 ingress，再 `asio::post` 到默认或按号段指定的业务线程池。被白名单拒绝或限频拒绝的请求**不会再占用业务 worker 队列槽位**。
- `dispatch(nullptr, …)`（实验性内部总线路径，`InternalBus`）**跳过** ingress，避免会话级策略误作用于无 `Session` 的链路；沿用 post-pool 的 `register_middleware` 链（网关默认不注册该链）。
- 保留 `register_middleware` / `middleware_count()` 表示 **post-pool** 链，供兼容与未来扩展。

### 测试与验证

- 单元：`MessageDispatcherTest.{IngressMiddlewareRunsSynchronouslyBeforeBusinessPool, IngressSkippedWhenSessionIsNull_InternalBusStyle}`，`ServiceRegistrationTest` 断言 ingress=2、middleware=0。
- `ctest`：62/62 通过。

### 兼容性

- 对已登录客户端、合法未登录业务流程（heartbeat / login / echo）无协议层差异；被拒时的错误响应（`auth_required`、`rate_limited`）保持不变。

---

## v1.1.2 — 主链生命周期与协议增强收口 (2026-05-06)

> **范围**：主链代码层面收敛 `Session` 关闭路径与协议增强标志位语义，**不引入新功能、不修改业务协议、不触碰配置/治理结构**。
>
> 对应 `docs/development-optimization.md` §11 任务表中的 T03 / T04。

### 主链行为修正

- **统一 `Session` 关闭路径（T03）**：`Session::stop()` 不再直接 `socket_.shutdown/close`，改为经由 `strand_` 上的 `handle_close(asio::error::operation_aborted)` 收口。这意味着主动关闭与心跳超时 / 网络异常 / 写队列溢出 / 包非法等异常关闭走同一条单事实源路径，`close_handler_` 一定会被触发且仅触发一次。
  - **顺带修复**：v1.0.0 中 `LoginService` 顶号踢线时调用 `replaced_session->stop()` 实际上绕过了 `close_handler_`，导致 `SessionManager` / `GatewayMetrics::on_session_closed()` / `active_connection_count_` 没有针对被踢号执行清理。本版本随 T03 一并修复。
- **协议增强顺序与压缩标志位语义（T04）**：
  - 出站固定为 `serialize -> compress (only when zlib available) -> encode`；
  - 入站固定为 `decode -> decompress (only when zlib available) -> dispatch`；
  - 新增 `net::packet::is_compression_available()`（编译期常量，绑定 `HAS_ZLIB`）。**仅当其为真时**才允许设置 `packet::flags::kCompressed`，避免无 zlib 的 build 用 fallback 长度前缀透传冒充压缩造成跨 build 语义错乱。
  - 当对端把 `kCompressed` 发到一个 *没有压缩后端* 的 build 上，服务端直接 `invalid_argument` 关闭连接，而不是错误地走 fallback decompress。
  - 分片标志位（`kFragment*`）仍为 `reserved`，主链不分片不组帧，与 `docs/v1-maturity-matrix.md` §2.3 一致。

### 测试

- 新增 `tests/unit/session_close_test.cpp`（4 用例，覆盖 stop 收口 / 幂等 / aborted 语义 / 无 close_handler 安全性）
- `tests/unit/compressor_test.cpp` 新增 `IsCompressionAvailableMatchesBuildBackend`
- 新增集成回归 `tests/integration/gateway_integration_test.cpp::CompressedFlagWithoutBackendIsRejected`
- `ctest`：60/60 通过（v1.1.1 基线 54 + 本版本新增 6）

### 兼容性

- 二进制协议 wire format 不变
- 业务消息号 / `LoginService` / `RoomService` / `BattleService` / 配置字段不变
- 没有压缩后端的客户端（含 v1.0.0 默认 build）继续工作；只有"伪造 `kCompressed`"的对端会被严格拒绝

---

## v1.1.1 — 基线校准 (2026-05-06)

> **范围**：纯文档基线校准，**不涉及主链协议、业务、运行时行为变更**。
>
> 对应 `docs/development-optimization.md` §11 任务表中的 T01 / T02 / T10 / T12 / T14。

### 文档新增

- 新增 `docs/v1-maturity-matrix.md` — `v1.x` 维护期能力成熟度**单一事实源**。覆盖：网络/协议、业务、治理、配置、持久化、可观测性、工程能力，每项均标注 `stable` / `experimental` / `reserved` / `demo-only`。

### 文档修正（消除"代码事实 / 文档承诺"不一致）

- `README.md`：
  - 新增"版本基线说明"区分 `v1.0.0` 发布版与 `develop` 维护期
  - 全量补成熟度标记，纠正以下过度承诺：
    - 自动分片传输（实际 reserved，主链未接入）
    - TLS 加密（实际 reserved，`GatewayServer` 主链未启用 SSL stream）
    - Token 生命周期失效（实际 experimental，`SessionManager` 不存储过期时间，运行时不主动失效）
    - 登录防爆破（实际 reserved，`LoginService` 未调用 `RateLimiter`）
    - 游客账号（实际 reserved，`max_guests` 主链未引用）
    - 完整热更新（实际 experimental，仅 `max_connections` / `per_ip_connection_limit` 真正应用）
    - 完整管理面 / 管理命令 5001-5005（实际 demo-only，无权限校验）
    - 多进程拆服架构（实际为按模块拆出的独立 demo 入口）
  - 测试规模与压测场景数量的表述按代码事实重新表达（实际 `PressureScenario` 9 个枚举值；ctest 用例以 `ctest -N` 为准）
- `docs/README.md`：重组文档导航，按"v1.x 维护期 / v2.0.0 路线"分组，明确"v2.0.0 在 v1.2.0 决策点前不进入开发"

### 维护版本节奏（来自 `development-optimization.md` §11）

| 版本 | 主题 |
|---|---|
| `v1.1.1` | 基线校准（**本版**） |
| `v1.1.2` | 会话与协议收口（T03 / T04） |
| `v1.1.3` | 入口收敛（T05） |
| `v1.1.4` | 状态边界收敛（T06） |
| `v1.1.5 - v1.1.8` | 业务线收口 |
| `v1.1.9 - v1.1.11` | 治理线收口 |
| `v1.1.12 - v1.1.14` | 运行时装配线收口 |
| `v1.1.15 - v1.1.17` | 持久化/审计/回放横切线收口 |
| `v1.2.0` | 协议与内部结构升级决策点 |
| `v1.2.1 - v1.2.4` | 各主线回归面加固 |

### 兼容性

- 协议、API、配置、运行时行为**完全不变**
- 所有现有测试用例保持通过

---

## v1.0.0 (2026-05-05)

### 核心架构
- 二进制协议：长度前缀 + 消息号 + 请求序号 + 错误码 + 标记位
- Session：异步 TCP + 心跳 + 限频 + 最大包长校验 + 反压保护
- MessageDispatcher：消息注册 + 中间件链 + 按消息范围线程池路由
- SessionManager：认证状态 + 重复登录处理 + 会话迁移
- RoomManager：创建/加入/离开/准备 + 房主机制 + COW 广播快照
- BattleManager：起战斗/结束 + 帧同步（advance_frame）+ 输入历史 + 观战

### 业务服务
- LoginService：三种鉴权模式（dev/json_file/http），Token TTL 24h，顶号踢线
- RoomService：房间生命周期 + 状态广播 + 准备追踪
- BattleService：战斗启动 + 输入路由 + 帧同步 + 结算
- PushService：统一成功/错误/推送响应
- GatewayService：鉴权白名单 + 限频中间件
- AdminService：踢人/封禁/状态/重载管理指令
- MatchmakingService：队列匹配 + ELO 分差控制

### 可观测性
- 10 种累计计数器 + 6 种每秒速率仪表盘
- Prometheus 文本 + JSON 双格式导出
- HTTP 管理端点：/health /metrics /metrics/json
- 请求链路追踪 ID（Session → Dispatcher → Handler）
- 审计日志：登录成功/失败、限频触发、连接拒绝、配置重载
- 崩溃转储：Windows SEH + POSIX 信号
- 日志采样宏：LOG_INFO_SAMPLED / LOG_DEBUG_SAMPLED

### 性能优化
- BufferPool / ObjectPool 复用分配
- 大包自动压缩（>512B）+ 分片传输（>8KB）
- 批量发包（send_batch）+ COW 广播快照
- 零拷贝读包路径 + 写队列反压
- 慢连接检测（积压 > 50% 告警）
- 连接预热（线性提升至全速）

### 安全能力
- Token 生命周期管理（expires_at + TTL）
- 连接限制（总量 + 单 IP）
- 多维限频（连接/用户/消息类型）
- 登录防暴力破解（IP + 用户维度）
- 游客账号（受限权限 + 降速限制）
- TLS 配置（证书 + 私钥 + SSL 上下文）

### 工程能力
- CMake Presets + FetchContent + 本地 third_party 内网构建
- Docker 多阶段构建 + docker-compose + GitHub Actions CI
- 54 个测试（34 单元 + 8 集成 + 7 模糊 + 5 其他）
- 8 种压测场景（echo/invalid_token/slow_echo/broadcast_storm/malicious/battle/chaos/stability）
- 6 个可执行文件

> **维护期补注（自 `v1.1.1` 起，详见 `docs/v1-maturity-matrix.md`）**：
> 以上 v1.0.0 描述中关于"自动分片"、"TLS 上下文已接入主链"、"登录防爆破"、"游客账号"、"完整管理指令"、"完整 Token 生命周期失效"等条目，主链实际为预留或半接入状态，请以 v1.1.1 起的 `docs/v1-maturity-matrix.md` 为准。
