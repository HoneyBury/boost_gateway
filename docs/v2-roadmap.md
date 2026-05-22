# v2.x 架构路线与当前状态

> 本文档已从最初的 `v2.0.0` 规划稿收口为”规划 + 当前实现事实源”。
> 当前主线已经不处于 `v2` 启动阶段，而是在 `v3.4.x` 收口阶段继续推进验证链、typed envelope、控制面能力和实时实例框架。

## 1. 核心结论

`v2.0.0` 的七大模块已经全部落地：

- `M1` Actor 模型核心
- `M2` 多核 I/O（含 SO_REUSEPORT + actor 核心亲和 + SPSC mailbox）
- `M3` 内存架构（BumpArena + ObjectPool + CacheLine/HotCold）
- `M4` 分布式 S0-S4（gateway-only ingress + login/room/battle 独立 backend + ServiceRegistry + BackendMetrics）
- `M5` 数据层 v2（LruCache + WriteBehind + Snapshotable + CachedBattleDataStore）
- `M6` AOI/ECS battle world（7-system pipeline + authoritative simulation + deterministic replay）
- `M7` 运维成熟度（DiagnosticsManager + HealthCheck + FeatureFlags + TraceContext/Span）

`v3.0.0` 之后新增的分布式与生产能力也已进入主线：

- ClusterRouter
- Remote Actor typed transport
- ConsistentHash
- Raft（选举 + 基础日志复制/恢复验证）
- RedisClient / RedisConnectionPool / RedisEventStore / RedisLeaderboard
- TLS/mTLS + SecurityPolicy + FeatureFlags
- OtlpExporter
- K8s Operator scaffold + controller

当前主线重点不是“再定义 v2 模块”，而是：

1. 收口 typed `ServiceEnvelope` helper 与后端兼容层
2. 补全恢复/故障/平台差异测试
3. 细化 Operator rollout-aware `status` 与 smoke/CI 断言

## 2. 当前实现状态

| 模块 | 状态 | 当前说明 |
|---|---|---|
| `M1 Actor` | done | `ActorSystem`、`ActorRef`、`PlayerActor`、`RoomActor`、`BattleActor` 主链稳定，含 schedule / snapshot / 生命周期测试 |
| `M2 多核 I/O` | done | `IoEngine`、多 listener ingress、accept policy、SPSC mailbox、session core aware outbound 均已接线 |
| `M3 内存架构` | done | `BumpArena`、`ObjectPool<T>`、`CacheLinePad`、`HotCold<T>` 已落地并有测试 |
| `M4 分布式原语` | done | `BackendEnvelope`、`ServiceManifest`、`GatewayServiceBridge`、`ServiceRegistry`、`ClusterRouter` 均已进入主链 |
| `M5 数据层 v2` | done | `WriteBehindDataStore`、`CachedBattleDataStore`、battle archive/snapshot/replay 路径已落地 |
| `M6 battle world` | done | 8-system ECS pipeline（BattleClock/BattleInput/Movement/Combat/Aoi/BattleLifecycle/BattleReplay）+ ProjectileSystem（弹道/AoE/DoT）、authoritative simulation、deterministic replay、AOI 已全部完成 |
| `M7 运维成熟度` | done | `DiagnosticsManager`、`HealthCheck`、`FeatureFlags`、`TraceContext`、`OtlpExporter` 接线完成 |

## 3. v3.4.0 收口项

当前已经额外推进的收口内容：

- typed `ServiceEnvelope` helper：`include/v3/proto/envelope_codec.h`
- `login/room/battle/match/leaderboard` typed envelope 兼容
- `ProtoSchemaTest` / `ServiceBusIntegrity.ProtoEnvelopeRoundTripsThrough*` 覆盖
- `LeaderboardRestoresCommittedScoresAfterRestart`
- `MatchmakingRestoresCommittedMatchAfterRestart`
- `LeaderboardFollowerCatchesUpAfterLeaderRestart`
- Operator `Certificate` reconcile
- Operator `status.desiredReplicas`
- Operator `status.components[]`
- Operator `Ready / Progressing / Degraded / TLSReady`

### Realtime Instance Framework（v3.4.0+）

实时实例框架已在主线落地，覆盖 M3-M5 的全部能力：

- **InstanceRuntime**（`v2::realtime::InstanceRuntime`）：通用实时实例运行时，管理实例生命周期、tick 调度、输入队列、snapshot push、resume/reconnect
- **InstancePlugin SPI**（`v2::realtime::InstancePlugin`）：8 虚方法业务插件接口，noexcept 契约 + try-catch 错误隔离
- **TankBattlePlugin**（`src/v2/battle/`）：框架集成参考实现，基于 ECS SimpleWorld 的完整业务插件
- **EchoPlugin**（`examples/realtime_echo_plugin/`）：SPI 最小示例
- **TankPlugin**（`demo/games/tank_battle/`）：独立仿真适配 demo

### 新增 ECS 系统

当前 ECS 管线已从最初规划的 7 系统扩展到 9 系统：

| 系统 | 注册位置 | 功能 |
|---|---|---|
| BattleClockSystem | create_battle_world() | 帧计数器与 trigger 追踪 |
| BattleInputSystem | create_battle_world() | 解析 pending input 字符串 |
| MovementSystem | create_battle_world() | 限速移动 + 反外挂传送检测 |
| CombatSystem | create_battle_world() | 攻击冷却 + 伤害边界 |
| AoiSystem | create_battle_world() | ECS 集成 AOI + SpatialGrid |
| BattleLifecycleSystem | create_battle_world() | 自动状态机（kCreated/kRunning/kFinished） |
| BattleReplaySystem | create_battle_world() | 逐帧 ECS 状态快照 |
| ProjectileSystem | TankBattlePlugin | 弹道飞行/AoE/DoT |
| ParallelSystemExecutor | ECS 框架层 | 拓扑排序并行系统执行 |

## 4. 当前边界

当前不应再把下列能力表述为“纯规划”：

- `ClusterRouter`
- `OtlpExporter`
- `RedisLeaderboard`
- `RedisConnectionPool`
- `SchemaValidator`
- `InputValidator`
- `typed envelope` helper
- Operator controller scaffold

当前仍属于“过渡/持续收口”的部分：

- 真正 generated protobuf/gRPC stub 接入
- Operator 在真实 cluster 上更严格的 rollout/dependency health 判定
- 全平台 CI 稳定性（尤其 Windows 特定 integration 测试）

## 6. 当前建议优先级

1. 修复 CI / 平台差异测试，恢复主线稳定回归能力
2. 将 typed helper 继续推进到 generated protobuf/gRPC 构建链
3. 扩大故障注入与恢复验证矩阵
4. 增强 Operator smoke/CI 对 `status.components[]` 和 `Degraded` 的断言
