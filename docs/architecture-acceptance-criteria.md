# 架构验收标准

> 本文档定义 BoostAsioDemo 作为高性能游戏服务器框架在各架构维度的量化验收标准。
> 当前 R2 实测数据来源：`docs/architecture-baseline-r2.md` 与 `runtime/perf/v2-arch-baseline/summary.json`。

## 0. 2026-05-23 实测回填（R2 架构验收）

| 领域 | 当前事实 | 验收证据 |
| --- | --- | --- |
| R1/R2 性能基线 | `v2_arch_benchmark` 与 `scripts/collect_v2_arch_baseline.py` 已形成短运行采集闭环，`summary.json` 输出 release gate | `docs/architecture-baseline-r2.md`、`runtime/perf/v2-arch-baseline/summary.json` |
| R1 Release 全系统基线 | echo-1000-30s 三条 runs 全部通过，P99=5ms，throughtput~17.8K msg/s；battle-100-30s P99=200ms（gate=250ms） | `runtime/perf/20260523-165827/summary.json` |
| Actor 调度公平性 | `ActorSystem::dispatch_ready()` 已按 ready actor 轮转，每个 actor 每轮最多处理一条消息 | `V2ActorRuntimeTest.DispatchAllInterleavesReadyActorsFairly` |
| Actor shutdown 竞态 | dispatch 中触发 shutdown 后不会继续投递其他 ready actor | `V2ActorRuntimeTest.ShutdownDuringFairDispatchStopsOtherReadyActors` |
| R4 typed envelope | login/room/battle/match/leaderboard 主 handler 已经通过统一 adapter 解析 typed envelope，并保留 legacy raw JSON 兼容 | `V2ServiceBoundaryTest.*Envelope*`、`ServiceBusIntegrity.ProtoEnvelopeRoundTripsThrough*Backend` |
| trace/error 传播 | raw BackendEnvelope 与 typed envelope 桥接路径均有 trace/span/error 验证 | `ServiceBusIntegrity.GatewayBridgeRoutePropagatesTraceAndErrorCode`、`ServiceBusIntegrity.GatewayBridgeTypedEnvelopePreservesTraceAndError` |
| 恢复路径 | backend 配置更新后路由可恢复，超时后旧连接关闭，circuit breaker 半开探测可恢复，heartbeat 可恢复 readiness | `ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate`、`ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers`、`ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers`、`HealthCheckTest.BackendHeartbeatRestoresReadinessAfterUnhealthyMark` |
| proto transport 实验 | `check_v3_proto_schema` 校验基础 schema，`check_v3_proto_transport_contract` 校验生成传输实验所需 oneof contract | `scripts/check_v3_proto_schema.py`、`src/v3/CMakeLists.txt` |
| P2-P5 稳定性收束 | 稳定性短 soak 覆盖 I/O accept 策略、backend 恢复、WriteBehind drain/failure 与短架构基线 | `scripts/verify_stability_soak.py`、`runtime/validation/stability-soak-summary.json` |
| 微基准 release gate | 14 项 v2_arch_benchmark 指标全部通过 Debug 防退化 gate（阈值宽放 10-10000us） | `runtime/perf/v2-arch-baseline/summary.json` release_gates.passed=true |

## 1. Actor 模型

### 1.1 消息投递

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 本地 `tell()` 延迟 | Debug P99 `2.6us`（2000 samples） | Release P99 `<= 1us` | `v2_arch_benchmark` — `actor_local_tell_dispatch` |
| 跨核 `tell()` 延迟 | Debug P99 `3.9us`（2000 samples） | P99 `<= 10us` | `v2_arch_benchmark` — `actor_cross_core_tell_drain_dispatch` |
| 单 core 消息吞吐 | Debug fan-in `372086 msg/s`（P99 6.3us） | Release `>= 1M msg/s` | `v2_arch_benchmark` — `actor_fan_in_throughput` |
| 消息丢失率 | 已有单测覆盖，目标 actor 缺失时安全失败；跨核压力 10K msg 无丢失 | `0` | `CrossCoreMailboxStressDoesNotDropMessages`、`io_engine_test` |

### 1.2 Actor 生命周期

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 创建延迟 | Debug P99 `7.5us`（2000 actors） | P99 `<= 10us` | `v2_arch_benchmark` — `actor_create` |
| 停止延迟 | Debug `0.37295us/actor`（2000 actors） | P99 `<= 5us` | `v2_arch_benchmark` — `actor_shutdown_per_actor` |
| 并发 actor 上限 | Debug 10K actor smoke 通过（100K 配置上限） | `>= 100K actor` | `v2_arch_benchmark` — `actor_100k_create_smoke` |
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
| 1K echo | Release median `17846 msg/s`（1000 clients, 30s x3 runs, P99=5ms, 0 failed） | Release gate 持续收紧 | `collect_v2_perf_baseline.py` → `runtime/perf/20260523-165827` |
| 10K 连接吞吐 | 待测 | `>= 1M msg/s` 总吞吐目标线 | R1/R2 后续压力 |
| 背压阈值 | `max_pending_write_bytes=256KB` | 可配置 + 超阈值回压 | backpressure test |

## 3. 内存架构

### 3.1 分配性能

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| `BumpArena` 分配 | Debug P99 `0.1us`（14.6M ops/s） | Release P99 `<= 10ns` | `v2_arch_benchmark` — `bump_arena_alloc` |
| `ObjectPool` acquire/release | Debug P99 `0.1us`（9.6M ops/s） | Release P99 `<= 50ns` | `v2_arch_benchmark` — `object_pool_acquire_release` |
| SPSC enqueue/dequeue | Debug P99 `0.1us`（10.6M ops/s） | Release P99 `<= 10us` | `v2_arch_benchmark` — `spsc_queue_enqueue_dequeue` |
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
| LRU 淘汰 | O(1)，已有单测覆盖 evict/remove/clear/duplicate | 淘汰策略正确 | `lru_cache_test` |
| 缓存命中率 | 待测 | 读多写少场景 `>= 80%` | business simulation |
| WriteBehind flush | 可配置 interval，已有单测验证 flush/destructor drain/失败统计 | P99 `<= interval * 2` | `write_behind_store_test` |
| replay/result/snapshot 格式 | magic + version + length | 向前兼容 | `data_layer_test` |
| 每连接内存成本 | echo-100: gateway ~7.7KB/conn；echo-1000: gateway ~3.5KB/conn；battle-100: gateway ~40KB/conn（RSS 差值法） | `<= 150KB/conn` | `collect_v2_perf_baseline.py` process snapshot |

## 6. ECS Battle World

### 6.1 正确性

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 确定性重放 | 已实现，相同输入跨 runs 产生相同 snapshot | 相同输入得到相同输出 | `battle_determinism_test` |
| 权威模拟 | 服务端为唯一事实源；已测试 duplicate_frame / battle_not_running / finish 拒绝 | 客户端非法输入被拒绝 | `battle_authoritative_test` |
| 输入到广播延迟 | Release battle-100-30s: P50=100ms, P90=100ms, P99=200ms（gate=250ms，通过） | P99 `<= 100ms` | `collect_v2_perf_baseline.py` → `runtime/perf/20260523-165827` |

### 6.2 性能

| 标准 | 当前 | 目标 | 验证方法 |
|---|---|---|---|
| 单场 battle tick (100 entities) | Debug P99 `425.8us`（3587 ticks/s, 2000 samples） | `<= 5ms/tick` | `v2_arch_benchmark` — `battle_world_tick_100_entities` |
| 并发战斗数 (500 battles, 100 entities each) | Debug P99 `459.9us`（2561 ticks/s, 100 samples） | `>= 500` 场并发 | `v2_arch_benchmark` — `multi_battle_tick_100_entities` |
| 每场战斗内存 | battle-100 下 v2_battle_backend RSS delta ~11-22MB，估算 ~110-220KB/battle | `<= 1MB/battle`（100 entities） | `collect_v2_perf_baseline.py` process snapshot |

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
| 单元测试 | v2 单测 `>80` 文件，覆盖 Actor/IO/ECS/Data/Battle/Security/Observability | 持续增长并覆盖关键路径 |
| 集成测试 | 多进程与 backend routing 已有；Release 基线含 echo-100/echo-1000/battle-20/battle-100 | 核心链路 E2E 自动化 |
| 性能基准 | R1 全系统 baseline + R2 微基准 v2_arch_benchmark（14 项）, 含 release gate 自动判定 | CI 阻断明显退化 |
| 故障注入 | 基础 fault injector (`fault_injector_test`) | R4/R5 建立矩阵 |

## 10. 当前总体判断

| 维度 | 当前评级 | 下一步 |
|---|---|---|
| Actor 模型 | 高级 | 补 Release 构建实测（当前 Debug 未达 1us target）、R3 线程契约 |
| I/O 引擎 | 高级 | 补 connect storm 压测、10K 连接吞吐数据 |
| 内存架构 | 高级 | 补 Release 构建实测（Debug 0.1us 分辨率不足）、碎片率/soak |
| 服务拆分 | 高级 | 补连接恢复与故障注入 |
| 数据层 | 高级 | 补 WriteBehind flush 延时验收、Redis 恢复验收 |
| ECS World | 高级 | 补 battle 内存占用更长 soak；输入到广播 P99 当前 200ms 距目标 100ms 差 2x |
| 运维成熟度 | 可用 | 补 readiness、Operator e2e |
| 安全 | 基础 | 补生产模式 auth 边界 |
