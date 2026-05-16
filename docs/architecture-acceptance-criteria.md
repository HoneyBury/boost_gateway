# 架构验收标准

> 本文档定义 BoostAsioDemo 作为高性能游戏服务器框架在各架构维度的量化验收标准。
> 当前 R2 实测数据来源：`docs/architecture-baseline-r2.md` 与 `runtime/perf/v2-arch-baseline/summary.json`。

## 0. 2026-05-16 实测回填

| 领域 | 当前事实 | 验收证据 |
| --- | --- | --- |
| R1/R2 性能基线 | `v2_arch_benchmark` 与 `scripts/collect_v2_arch_baseline.py` 已形成短运行采集闭环，`summary.json` 输出 release gate | `docs/architecture-baseline-r2.md`、`runtime/perf/v2-arch-baseline/summary.json` |
| Actor 调度公平性 | `ActorSystem::dispatch_ready()` 已按 ready actor 轮转，每个 actor 每轮最多处理一条消息 | `V2ActorRuntimeTest.DispatchAllInterleavesReadyActorsFairly` |
| Actor shutdown 竞态 | dispatch 中触发 shutdown 后不会继续投递其他 ready actor | `V2ActorRuntimeTest.ShutdownDuringFairDispatchStopsOtherReadyActors` |
| R4 typed envelope | login/room/battle/match/leaderboard 主 handler 已经通过统一 adapter 解析 typed envelope，并保留 legacy raw JSON 兼容 | `V2ServiceBoundaryTest.*Envelope*`、`ServiceBusIntegrity.ProtoEnvelopeRoundTripsThrough*Backend` |
| trace/error 传播 | raw BackendEnvelope 与 typed envelope 桥接路径均有 trace/span/error 验证 | `ServiceBusIntegrity.GatewayBridgeRoutePropagatesTraceAndErrorCode`、`ServiceBusIntegrity.GatewayBridgeTypedEnvelopePreservesTraceAndError` |
| 恢复路径 | backend 配置更新后路由可恢复，超时后旧连接关闭，circuit breaker 半开探测可恢复，heartbeat 可恢复 readiness | `ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate`、`ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers`、`ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers`、`HealthCheckTest.BackendHeartbeatRestoresReadinessAfterUnhealthyMark` |
| proto transport 实验 | `check_v3_proto_schema` 校验基础 schema，`check_v3_proto_transport_contract` 校验生成传输实验所需 oneof contract | `scripts/check_v3_proto_schema.py`、`src/v3/CMakeLists.txt` |

## 1. Actor 模型

### 1.1 消息投递

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 本地 `tell()` 延迟 | Debug P99 `2.2us` | Release P99 `<= 1us` | `v2_arch_benchmark` |
| 跨核 `tell()` 延迟 | Debug P99 `3.3us` | P99 `<= 10us` | `v2_arch_benchmark` + mailbox drain |
| 单 core 消息吞吐 | Debug fan-in `511962 msg/s` | Release `>= 1M msg/s` | `v2_arch_benchmark` |
| 消息丢失率 | 已有单测覆盖，目标 actor 缺失时安全失败 | `0` | `actor_runtime_test` / `io_engine_test` |

### 1.2 Actor 生命周期

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 创建延迟 | Debug P99 `1.1us`（10K actor） | P99 `<= 10us` | `v2_arch_benchmark` |
| 停止延迟 | Debug `0.36455us/actor`（10K actor） | P99 `<= 5us` | `v2_arch_benchmark` |
| 并发 actor 上限 | Debug 100K actor smoke 通过 | `>= 100K actor` | `v2_arch_benchmark` |
| 调度公平性 | 单线程 ready queue，待补多 actor 分布数据 | 无饥饿 | multi-actor latency benchmark |

### 1.3 监督与容错

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 未处理异常 | actor 异常被捕获并记录，当前策略为继续运行调度循环 | 可配置 stop/restart/escalate | fault injection test |
| 监督树 | 未实现 | v2.3+ 后续目标 | 后续设计 |

## 2. I/O 引擎

### 2.1 多核接入

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| SO_REUSEPORT / 多 listener | 已实现 `MultiIoAcceptor` | n 核 accept 分布可量化 | `io_engine_test` + 后续压力 |
| Accept 策略 | `RoundRobin` / `LeastLoaded` / `Fixed` | 负载不均时 `LeastLoaded` 生效 | `io_engine_test` |
| 连接建立延迟 | R1 未覆盖 | P99 `<= 1ms` | connect storm benchmark |

### 2.2 网络吞吐

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 1K echo | Windows Debug median `12270 msg/s`（1000 clients） | Release gate 持续收紧 | `collect_v2_perf_baseline.py` |
| 10K 连接吞吐 | 待测 | `>= 1M msg/s` 总吞吐目标线 | R1/R2 后续压力 |
| 背压阈值 | `max_pending_write_bytes=256KB` | 可配置 + 超阈值回压 | backpressure test |

## 3. 内存架构

### 3.1 分配性能

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| `BumpArena` 分配 | Debug P99 `0.1us` | Release P99 `<= 10ns` | `v2_arch_benchmark` |
| `ObjectPool` acquire/release | Debug P99 `0.1us` | Release P99 `<= 50ns` | `v2_arch_benchmark` |
| SPSC enqueue/dequeue | Debug P99 `0.1us` | Release P99 `<= 10us` | `v2_arch_benchmark` |
| 内存碎片率 | 待测 | arena `<= 10%`，pool `<= 5%` | soak + allocation stats |

### 3.2 缓存效率

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| CacheLine 对齐 | `kCacheLineSize=64`，已有单测 | 热数据避免伪共享 | `cache_line_test` |
| Hot/Cold 分离 | 已实现 | 冷数据不污染 L1/L2 | perf stat / hotspot analysis |
| 缓存命中率 | 待测 | L1 `>= 95%`，L2 `>= 90%` | perf 工具 |

## 4. 服务拆分与通信

### 4.1 后端连接

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 连接建立 | 懒加载，首次路由时连接 | 首次延迟 `<= 10ms` | `backend_routing_test` |
| 连接复用 | 每后端单 TCP 连接 | 连接池 `>= 4` 可配置 | connection pool test |
| 连接恢复 | 基础错误返回，自动恢复仍需补强 | 自动重连 + 指数退避 | fault injection test |
| 熔断器 | 已有 `CircuitBreaker` 单测 | 连续失败后熔断并半开探测 | `circuit_breaker_test` |

### 4.2 服务发现

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| TTL 过期 | 已实现 | 过期实例 `<= 5s` 摘除 | `service_registry_test` |
| 心跳恢复 | 已实现 | 恢复实例 `<= 5s` 上线 | `service_registry_test` |
| 负载均衡 | 基础路由，策略待扩展 | 最少连接 / 轮询 | routing benchmark |

### 4.3 API 契约

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| `BackendEnvelope` 格式 | 已冻结 JSON envelope | 向后兼容升级 | `service_boundary_test` |
| typed envelope | login/room/battle/match/leaderboard 已有 helper | generated proto/gRPC 路线明确 | R4 契约文档 + 测试 |
| 错误传播 | 基础 `error_code` 透传 | gateway/backend/SDK 一致 | E2E / compatibility test |
| 超时传播 | 部分路径已有超时，取消语义待补 | gateway 超时后不泄漏 pending request | timeout test |

## 5. 数据层

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| LRU 淘汰 | O(1)，已有单测 | 淘汰策略正确 | `lru_cache_test` |
| 缓存命中率 | 待测 | 读多写少场景 `>= 80%` | business simulation |
| WriteBehind flush | 可配置 interval | P99 `<= interval * 2` | `write_behind_store_test` |
| replay/result/snapshot 格式 | magic + version + length | 向前兼容 | `data_layer_test` |

## 6. ECS Battle World

### 6.1 正确性

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 确定性重放 | 已实现 | 相同输入得到相同输出 | `battle_determinism_test` |
| 权威模拟 | 服务端为唯一事实源 | 客户端非法输入被拒绝 | `battle_authoritative_test` |
| 输入到广播延迟 | R1 battle-100 P99 `100ms`，贴近 gate | P99 `<= 100ms` | `collect_v2_perf_baseline.py` |

### 6.2 性能

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 单场 battle tick | Debug P99 `269.7us`（100 entities） | `<= 5ms/tick` | `v2_arch_benchmark` |
| 并发战斗数 | Debug 500 场 tick P99 `484.5us` | `>= 500` 场并发 | `v2_arch_benchmark` |
| 每场战斗内存 | 待测 | `<= 1MB/battle`（100 entities） | memory snapshot |

## 7. 运维成熟度

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| `/health` | DemoServer 已支持 | 核心服务全部支持 | HTTP smoke |
| `/ready` | 待补依赖检查 | 依赖可达性明确 | readiness test |
| 诊断 JSON | `diagnostics_json()` 已存在 | 标准化字段 | `health_check_test` |
| Trace | 自建 TraceContext | W3C TraceContext 兼容 | `trace_context_test` |
| OTel 导出 | JSON/OTLP 兼容基础 | exporter 不阻塞主链 | `otel_persistence_test` |

## 8. 安全

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| Token 校验 | dev provider + JWT validator | 生产模式禁用 dev token 或明确不可达 | `jwt_validator_test` |
| Rate limit | global/IP/user/message-type/login bucket | 突发容忍和稳定速率可量化 | `rate_limiter_test` + pressure |
| 输入校验 | 基础 JSON parse + schema validator | JSON schema 覆盖主链 | `schema_validator_test` |
| 反外挂 | 基础 validator | 移动/距离/冷却规则完善 | `anti_cheat_test` |

## 9. 测试与发布门槛

| 维度 | 当前 | 目标 |
|---|---|---|
| 单元测试 | v2 单测 575 个，Redis live 依赖缺失时跳过 | 持续增长并覆盖关键路径 |
| 集成测试 | 多进程与 backend routing 已有 | 核心链路 E2E 自动化 |
| 性能基准 | R1 + R2 已有短运行 collector | CI 阻断明显退化 |
| 故障注入 | 基础 fault injector | R4/R5 建立矩阵 |

## 10. 当前总体判断

| 维度 | 当前评级 | 下一步 |
|---|---|---|
| Actor 模型 | 高级 | 补 ping-pong 分布、R3 线程契约 |
| I/O 引擎 | 高级 | 补 connect storm、10K 连接数据 |
| 内存架构 | 高级 | 补碎片率/soak |
| 服务拆分 | 高级 | 补连接恢复与故障注入 |
| 数据层 | 高级 | 补 WriteBehind/Redis 恢复验收 |
| ECS World | 高级 | 补 battle 内存占用和更长 soak |
| 运维成熟度 | 可用 | 补 readiness、Operator e2e |
| 安全 | 基础 | 补生产模式 auth 边界 |
