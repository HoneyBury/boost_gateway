# 开发优先级看板

> **当前阶段**：`v1.x` 维护已完成，`v2.0.0` 七大模块（M1-M7）全部落地。本文档为 `v1.x` 维护期历史记录，不再作为当前开发优先级依据。当前开发参见 `../history-v2/v2-roadmap.md`。

## 1. 维护规则

- 每完成一项核心任务，同步更新状态
- 每次调整技术方向或优先级顺序，同步更新原因
- 状态约定：`todo` `doing` `done` `blocked`
- **`v2.0.0` 已于 2026-05-12 完成七大模块（M1-M7），`v1.2.0` 决策点已执行（不转正 typed protocol/bus）

## 2. v1.0.0 已完成 (P0–P7)

> **历史记录**。维护期判断这些条目是否仍为 stable，以 `../history-v1/v1-maturity-matrix.md` 为准（部分条目实际为 experimental / reserved，例如"游客账号"、"完整管理指令"、"自动分片传输"等）。

### P0 — 网络层基础

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.1 | `SessionManager / RoomManager / BattleManager` 状态拆分 | done |
| 2.2 | 协议统一化：`request_id + error_code` | done |
| 2.3 | 网关限频中间层 | done |

### P1 — 业务骨架

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.4 | 完整登录链路：token 校验 + 登录上下文 | done |
| 2.5 | 房间系统独立化：创建/加入/房主/准备/广播 | done |
| 2.6 | 战斗系统独立化：上下文/输入路由/状态广播 | done |

### P2 — 工程增强

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.7 | 广播与推送链路：PushService 统一推送 | done |
| 2.8 | 重连恢复与踢线：旧连接替换/会话恢复 | done |
| 2.9 | 配置系统：key=value + JSON 双格式 | done |

### P3 — 可观测性与后端

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.10 | 可观测性：Prometheus + JSON 指标导出 | done |
| 2.11 | 压测体系：6 种场景 + JSON 配置 | done（实际 9 种） |
| 2.12 | 后端持久化：JsonFileTokenValidator + HTTP 鉴权 | done（HTTP 鉴权 experimental） |

### P4 — 管理端点与部署

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.13 | HTTP 管理端点：/health + /metrics | done（/health 固定字符串） |
| 2.14 | 压测场景扩展：broadcast_storm / malicious / battle | done |
| 2.15 | 远程鉴权客户端：HttpTokenValidator | done（同步阻塞） |
| 2.16 | Docker + CI：Dockerfile / docker-compose / CI 扩展 | done |

### P5 — 基础设施深化

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.17 | 结构化序列化：18 种 message types + binary serializer | done（草案，未接主链） |
| 2.18 | 链路追踪 ID：trace_id 贯穿全链路 | done |
| 2.19 | 协议 flags 字节：压缩/加密标记位 | done（kCompressed 语义不稳定，kEncrypted 仅常量） |
| 2.20 | Buffer 池 + 对象池：BufferPool / ObjectPool | done |
| 2.21 | 批量发包：Session::send_batch() | done |
| 2.22 | 慢连接检测：写队列积压 > 50% 告警 | done |

### P6 — 生产加固

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.23 | Per-second 速率指标：6 种 _rate gauge | done |
| 2.24 | 零拷贝：BufferPool 集成到 Session 读包路径 | done |
| 2.25 | 崩溃转储：SEH/signal handler + crash report | done |
| 2.26 | 服务拆分路由：ServiceRouter + ServiceId | done（本地路由，experimental） |
| 2.27 | 战斗帧同步：frame_number + advance_frame | done（短局演示级） |

### P7 — 生产安全

| 编号 | 条目 | 状态 |
|---|---|---|
| 2.28 | 连接限制：max_connections + per_ip_connection_limit | done |
| 2.29 | 线程池拆分：按消息 ID 范围路由到专用池 | done |
| 2.30 | 战斗结算：end_battle() + winner/scores | done（输入条数策略，演示级） |
| 2.31 | Handler 注册工具：HandlerRegistry | done |

---

## 3. v1.x 维护期任务（基于 `development-optimization.md` §11）

### 批次 A：基线与事实源校准

| 任务 | 版本 | 状态 |
|---|---|---|
| （文档）login / room / battle 模块边界与四大验收问答 | `v1.1.5` | **done**（见 `../history-v1/v1-business-fact-source.md`） |
| T01 校准 `v1.0.0` / `develop` 能力边界与文档表述 | `v1.1.1` | **done** |
| T02 冻结当前真实协议表，明确 1.x 主协议事实源 | `v1.1.1` / `v1.1.6` | **done**（`v1-string-protocol.md`；`kPlayerNotInBattle`/`to_string`） |
| T10 校准治理入口分层，明确 HTTP / admin 命令成熟度 | `v1.1.1` / `v1.1.9` | **done**（`../history-v1/v1-governance-layers.md` §1–§5；**权限/审计留给 v1.1.11**） |
| （文档）治理能力成熟度冻结，禁绝示例/README 误导「已实现 = 正式能力」 | `v1.1.10` | **done**（`../history-v1/v1-governance-layers.md` **§6**；`admin_demo`/`login_demo`/`README.md`/`runtime-playbook`） |
| T12 给配置字段补「启动生效 / 热更新生效 / 仅预留」标记 | `v1.1.1` / `v1.1.12` | **done**（矩阵 §5.1 指针 + **`../history-v1/v1-config-maturity.md`**；标准 reload/shutdown 与 **v1.1.14** 受控语义见 **`v1-runtime-lifecycle.md`**） |
| T14 明确 player store / replay / audit 当前定位与成熟度 | `v1.1.1` / `v1.1.15` | **done**（**v1.1.1** 矩阵基线；**v1.1.15** **`../history-v1/v1-cross-cutting-capabilities.md`** 生命周期事实矩阵） |

### 批次 B：主链生命周期与边界收口

| 任务 | 版本 | 状态 |
|---|---|---|
| T03 统一 `Session` 主动关闭 / 异常关闭收口路径 | `v1.1.2` | **done** |
| T04 固定协议增强顺序，明确分片当前未启用，修正 zlib 缺失时压缩标记位语义 | `v1.1.2` | **done** |
| T05 前置 ingress 鉴权白名单与限频，收口入口治理 | `v1.1.3` | **done** |
| T06 明确 `battle_started` 单一事实源，停止 room/battle 双写（第一阶段） | `v1.1.4` | **done** |
| T06 后续：`transfer_session` / 房战边界 / `member_user_id` 等与 T09 | `v1.1.8` | **done**（`../history-v1/v1-room-battle-boundary.md`；`RoomMember.member_user_id`） |
| T07 收敛重复登录恢复链 | `v1.1.7` | **done**（`login_recovery.*` + `../history-v1/v1-cross-domain-flows.md` §A） |
| T08 收敛空房 battle 清理链 | `v1.1.7` | **done**（`room_battle_lifecycle.*` + `../history-v1/v1-cross-domain-flows.md` §B） |
| T09 收紧房间态与战斗态边界，明确 `transfer_session()` 定位 | `v1.1.8` | **done**（同上 + 代码注释与矩阵 §3.2） |
| T11 admin **调用前提**与 **`admin_invoke`** **最小审计**（文档 + handler 边界；**无运行时 ACL**） | `v1.1.11` | **done**（见 **`../history-v1/v1-admin-audit-rules.md`**；`admin_service.cpp`） |
| T13 收敛标准启动 / reload / shutdown 顺序 | `v1.1.13` / `v1.1.14` | **done**（**v1.1.13** ✅ 清单 + showcase **`io.stop()`**；**v1.1.14** ✅ **`try_load_gateway_config`** + **`v1-runtime-lifecycle.md` §6–§7**） |
| T15 按登录 / 结算 / 停服节点收口横切动作 | `v1.1.16` | **done**（**`../history-v1/v1-cross-cutting-lifecycle-binding.md`**：规范矩阵 **§3** + showcase **§5**） |
| T16 冻结存储后端和审计/回放数据格式支持级别 | `v1.1.17` | **done**（**`../history-v1/v1-cross-cutting-data-formats.md`**） |

### 批次 C：边界测试与回归面加固

| 任务 | 版本 | 状态 |
|---|---|---|
| T17 业务边界测试加固 | `v1.2.1` | **done**（**`battle_manager_test`** / **`room_manager_test`** / **`gateway_integration_test`**；集成侧 `read_until_message` 跳过 **`kRoomStatePush`/`kRoomReadyResponse` 交错**） |
| T18 治理边界测试加固 | `v1.2.2` | **done**（**`admin_service_test`** / **`http_management_test`** / **`gateway_integration_test`**：默认不注册 admin、HTTP 固定行为、`admin_invoke` 最小审计键） |
| T19 生命周期与装配测试加固 | `v1.2.3` | **done**（**`lifecycle_assembly_test`**：`ConfigWatcher` 成败 reload、`GatewayServer::stop()` 连接/房间态收口） |
| T20 持久化 / 审计 / 回放测试加固 | `v1.2.4` | **done**（**`persistence_replay_audit_test`**：player store、`.replay` 读写、`ReplayPlayer`、`AUDIT_LOG` 行） |

### 批次 D：结构升级决策

| 任务 | 版本 | 状态 |
|---|---|---|
| T21 评估是否正式推进 typed protocol / internal bus / battle replay 闭环 | `v1.2.0` | **done**（见 **`../history-v1/v1-structure-upgrade-decision.md`**；结论：**当前维护分支不转正，不提前进入 2.0 结构开发**） |

---

## 4. 推荐执行节奏

```
v1.1.1   基线校准                     ✅
v1.1.2   会话与协议收口（T03 / T04）  ✅
v1.1.3   入口收敛（T05）              ✅
v1.1.4   battle_started 事实源（T06 ①） ✅
v1.1.5   业务事实源校准（叙事文档） ✅
v1.1.6   业务协议冻结（T02 后半 / 错误码语义） ✅
v1.1.7   跨域编排收口（T07 / T08） ✅
v1.1.8   房间/战斗边界收紧（T09 / T06 第二阶段） ✅
v1.1.9   治理入口分层（T10） ✅
v1.1.10  治理成熟度冻结 ✅
v1.1.11  admin 权限前提与最小审计（T11） ✅
v1.1.12  配置字段成熟度（T12） ✅
v1.1.13  标准启动 / reload / shutdown 顺序（T13） ✅
v1.1.14  受控生命周期流程（T13 后半） ✅
v1.1.15  横切能力定位（T14） ✅
v1.1.16  横切动作按生命周期收口（T15） ✅
v1.1.17  数据格式冻结（T16） ✅
─────────────────────────────────────
v1.2.0   结构升级决策（T21） ✅ 结论：维护分支不转正
v1.2.1   业务边界测试加固（T17） ✅
v1.2.2   治理边界测试加固（T18） ✅
v1.2.3   生命周期与装配测试加固（T19） ✅
v1.2.4   持久化/审计/回放测试加固（T20） ✅
v1.2.5   CI / Docker / 发布链路稳定性修复 ✅
v2.0.0   七大模块全部落地 ✅ 2026-05-12
```

> **v1.2.0 决策已执行**：`v1.x` 维护期结束，`v2.0.0` 七大模块（M1-M7）全部落地，473 测试全部通过。

## 5. 最近一次更新

- 日期：`2026-05-12`
- 版本：`v2.0.0` — `v1.x` 维护期结束。七大模块（M1-M7）全部落地：Actor 模型、多核 I/O（含 SO_REUSEPORT + 核心亲和）、内存架构（BumpArena + ObjectPool + CacheLine）、分布式 S0-S4 服务拆分、数据层 v2（LruCache + WriteBehind + Snapshotable + CachedBattleDataStore）、AOI/ECS battle world、运维成熟度（DiagnosticsManager + HealthCheck + FeatureFlags + TraceContext+Span）。473 测试。
- 历次更新：
  - `2026-05-08` `v1.2.5` — Windows CI 稳定性、Docker 依赖获取与 `gtest_discover_tests` timeout 修复
  - `2026-05-07` `v1.2.4` — **`persistence_replay_audit_test.cpp`**、**`../history-v1/v1-structure-upgrade-decision.md`**
  - `2026-05-07` `v1.2.3` — **`lifecycle_assembly_test.cpp`**：`ConfigWatcher` 与 `GatewayServer::stop()`
  - `2026-05-07` `v1.2.2` — **`admin_service_test.cpp`**、**`http_management_test.cpp`**、**`gateway_integration_test.cpp`**：治理边界回归
  - `2026-05-07` `v1.2.1` — **T17**：业务边界测试（**`BattleManager`** / **`RoomManager`** 单元；**`gateway_integration_test`** 房主 / 全员就绪 / 人数 / 未开战输入）；矩阵 §8 / §10。
  - `2026-05-06` `v1.1.17` — **`v1-cross-cutting-data-formats.md`**（T16）
  - `2026-05-06` `v1.1.16` — **`v1-cross-cutting-lifecycle-binding.md`**（T15）
  - `2026-05-06` `v1.1.15` — **`v1-cross-cutting-capabilities.md`**（T14 事实矩阵）
  - `2026-05-06` `v1.1.14` — **`try_load_gateway_config`**、`v1-runtime-lifecycle` §6–§7
  - `2026-05-06` `v1.1.13` — 装配清单 + showcase **`io_context.stop()`**
  - `2026-05-06` `v1.1.12` — `v1-config-maturity.md`、`v2-design.md` 入库
  - `2026-05-06` `v1.1.11` — admin 规则、`admin_invoke`
  - `2026-05-06` `v1.1.10` — 治理成熟度冻结 §6
  - `2026-05-06` `v1.1.9` — 治理入口分层、`v1-governance-layers.md`
  - `2026-05-06` `v1.1.8` — 房战边界、`member_user_id`
  - `2026-05-06` `v1.1.6` — 协议冻结 + `kPlayerNotInBattle`
  - `2026-05-06` `v1.1.4` — T06 第一阶段 `battle_started` SSOT、`set_battle_active_query`
  - `2026-05-06` `v1.1.3` — T05 ingress 前置
  - `2026-05-06` `v1.1.2` — T03 / T04
  - `2026-05-06` `v1.1.1` — 文档基线与矩阵
