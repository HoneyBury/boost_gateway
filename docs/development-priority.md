# 开发优先级看板

> **当前阶段**：`v1.x` 维护期。**优先级看板与 `docs/v1-maturity-matrix.md` 冲突时以矩阵为准**。本看板从 `v1.1.1` 起按 `docs/development-optimization.md` §11 任务表组织，不再以"扩展方向 / 优化方向 / 测试方向"散列优先级。

## 1. 维护规则

- 每完成一项核心任务，同步更新状态
- 每次调整技术方向或优先级顺序，同步更新原因
- 状态约定：`todo` `doing` `done` `blocked`
- 严格按版本批次推进，不跨批
- **`v2.0.0` 范畴在 `v1.2.0` 决策点之前不进入开发**

## 2. v1.0.0 已完成 (P0–P7)

> **历史记录**。维护期判断这些条目是否仍为 stable，以 `docs/v1-maturity-matrix.md` 为准（部分条目实际为 experimental / reserved，例如"游客账号"、"完整管理指令"、"自动分片传输"等）。

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
| （文档）login / room / battle 模块边界与四大验收问答 | `v1.1.5` | **done**（见 `docs/v1-business-fact-source.md`） |
| T01 校准 `v1.0.0` / `develop` 能力边界与文档表述 | `v1.1.1` | **done** |
| T02 冻结当前真实协议表，明确 1.x 主协议事实源 | `v1.1.1` / `v1.1.6` | **done**（`v1-string-protocol.md`；`kPlayerNotInBattle`/`to_string`） |
| T10 校准治理入口分层，明确 HTTP / admin 命令成熟度 | `v1.1.1` / `v1.1.9` | **doing**（v1.1.1 完成成熟度文档校准；分层与权限模型在 v1.1.9 / v1.1.11） |
| T12 给配置字段补「启动生效 / 热更新生效 / 仅预留」标记 | `v1.1.1` / `v1.1.12` | **doing**（v1.1.1 完成成熟度矩阵字段表；标准热更新流程在 v1.1.12） |
| T14 明确 player store / replay / audit 当前定位与成熟度 | `v1.1.1` / `v1.1.15` | **doing**（v1.1.1 完成定位说明；按生命周期收口在 v1.1.15） |

### 批次 B：主链生命周期与边界收口

| 任务 | 版本 | 状态 |
|---|---|---|
| T03 统一 `Session` 主动关闭 / 异常关闭收口路径 | `v1.1.2` | **done** |
| T04 固定协议增强顺序，明确分片当前未启用，修正 zlib 缺失时压缩标记位语义 | `v1.1.2` | **done** |
| T05 前置 ingress 鉴权白名单与限频，收口入口治理 | `v1.1.3` | **done** |
| T06 明确 `battle_started` 单一事实源，停止 room/battle 双写（第一阶段） | `v1.1.4` | **done** |
| T06 后续：`transfer_session` / 空房战场清理等与 T09 | `v1.1.8` | todo |
| T07 收敛重复登录恢复链 | `v1.1.7` | **done**（`login_recovery.*` + `docs/v1-cross-domain-flows.md` §A） |
| T08 收敛空房 battle 清理链 | `v1.1.7` | **done**（`room_battle_lifecycle.*` + `docs/v1-cross-domain-flows.md` §B） |
| T09 收紧房间态与战斗态边界，明确 `transfer_session()` 定位 | `v1.1.8` | todo |
| T11 明确 admin 权限模型与审计最小规则 | `v1.1.11` | todo |
| T13 收敛标准启动 / reload / shutdown 顺序 | `v1.1.13` / `v1.1.14` | todo |
| T15 按登录 / 结算 / 停服节点收口横切动作 | `v1.1.16` | todo |
| T16 冻结存储后端和审计/回放数据格式支持级别 | `v1.1.17` | todo |

### 批次 C：边界测试与回归面加固

| 任务 | 版本 | 状态 |
|---|---|---|
| T17 业务边界测试加固 | `v1.2.1` | todo |
| T18 治理边界测试加固 | `v1.2.2` | todo |
| T19 生命周期与装配测试加固 | `v1.2.3` | todo |
| T20 持久化 / 审计 / 回放测试加固 | `v1.2.4` | todo |

### 批次 D：结构升级决策

| 任务 | 版本 | 状态 |
|---|---|---|
| T21 评估是否正式推进 typed protocol / internal bus / battle replay 闭环 | `v1.2.0` | todo（决策点，非默认实施项） |

---

## 4. 推荐执行节奏

```
v1.1.1   基线校准                     ✅
v1.1.2   会话与协议收口（T03 / T04）  ✅
v1.1.3   入口收敛（T05）              ✅
v1.1.4   battle_started 事实源（T06 ①） ✅
v1.1.5   业务事实源校准（叙事文档） ✅
v1.1.6   业务协议冻结（T02 后半 / 错误码语义） ✅
v1.1.7   跨域编排收口（T07 / T08） ✅ 当前
v1.1.8   房间/战斗边界收紧（T09 / T06 第二阶段）
v1.1.9   治理入口分层（T10 后半）
v1.1.10  治理成熟度冻结
v1.1.11  admin 权限与审计规则（T11）
v1.1.12  配置成熟度表（T12 后半）
v1.1.13  标准启动 / reload / shutdown 顺序（T13）
v1.1.14  受控生命周期流程
v1.1.15  横切能力定位（T14 后半）
v1.1.16  横切动作按生命周期收口（T15）
v1.1.17  数据格式冻结（T16）
─────────────────────────────────────
v1.2.0   决策点：是否推进结构升级（T21）
v1.2.1   业务边界测试加固（T17）
v1.2.2   治理边界测试加固（T18）
v1.2.3   生命周期与装配测试加固（T19）
v1.2.4   持久化/审计/回放测试加固（T20）
```

> **强约束**：在 `v1.2.0` 决策点之前**不进入 v2.0.0 范畴**（Actor / ECS / 集群路由 / 状态生命周期系统 / 控制面，详见 `docs/v2-roadmap.md` 与 `docs/v2-design.md`）。

## 5. 最近一次更新

- 日期：`2026-05-06`
- 版本：`v1.1.7` 跨域编排收口（T07 / T08）：`login_recovery` 提取顶号房间迁移与登录后 room 推送拼装；`clear_battle_if_room_empty` 统一空房战斗清理（`GatewayServer` + `RoomService`）；`docs/v1-cross-domain-flows.md`；单元测 `RoomBattleLifecycleTest`。
- 历次更新：
  - `2026-05-06` `v1.1.6` — 协议冻结文档 + `kPlayerNotInBattle`
  - `2026-05-06` `v1.1.4` — T06 第一阶段 `battle_started` SSOT、`set_battle_active_query`
  - `2026-05-06` `v1.1.3` — T05 ingress 前置
  - `2026-05-06` `v1.1.2` — T03 / T04
  - `2026-05-06` `v1.1.1` — 文档基线与矩阵
