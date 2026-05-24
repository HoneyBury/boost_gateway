# v2 下一阶段边界

## 1. 当前结论（2026-05-12 更新）

v2.0.0 全部七大模块已完成落地，473 测试全部通过。

- `M1` Actor 模型核心 ✅
- `M2` 多核 I/O（含 SO_REUSEPORT + actor 核心亲和 + SPSC mailbox） ✅
- `M3` 内存架构（BumpArena + ObjectPool + CacheLine/HotCold） ✅
- `M4` 分布式 S0-S4（gateway-only ingress + 独立 backend + ServiceRegistry + BackendMetrics） ✅
- `M5` 数据层 v2（LruCache + WriteBehind + Snapshotable + CachedBattleDataStore） ✅
- `M6` AOI/ECS Battle World（7-system pipeline + authoritative simulation + deterministic replay） ✅
- `M7` 运维成熟度（DiagnosticsManager + HealthCheck + FeatureFlags + TraceContext+Span） ✅

## 2. 当前状态与后续方向

v2.0.0 七大模块已全部完成，R1-R4 发布基础设施已落地。当前工作重心已从"模块开发"转向"企业级生产加固"。

后续迭代规划详见 **《v2.x 企业级迭代路线》（`../history-v2/v2-enterprise-roadmap.md`）**，核心路径：

```
v2.0.1 生产加固  →  v2.0.2 性能基线  →  v2.1.0 多进程验证
    (当前)              (待启动)            (待启动)
```

各阶段关键任务：

1. **v2.0.1（生产加固）** — 配置热加载、断路器、优雅降级、背压保护、内存保护
2. **v2.0.2（性能基线）** — 吞吐/延迟/资源基线、SLO/SLI 定义、容量规划
3. **v2.1.0（多进程验证）** — 4 进程 E2E 测试、故障注入、浸泡测试、客户端协议规范
4. **v2.2.0（安全与可观测性）** — JWT 认证、OpenTelemetry、审计日志、K8s 部署
5. **v2.3.0（高级特性）** — 匹配系统、排行榜、反外挂、帧同步优化

验收标准详见 **《架构验收标准》（`docs/architecture-acceptance-criteria.md`）**，
覆盖性能、可靠性、可观测性、安全性、可运维性五个维度。

说明：

- R1-R4 发布基础设施（`cmake --install`、Docker、systemd、CI/CD、监控面板）已全部完成
- `M2` SO_REUSEPORT + actor 核心亲和已完成
- `M5` LruCache + WriteBehind + Snapshotable 已完成
- `M3` BumpArena + ObjectPool + CacheLine/HotCold 已完成

## 3. 各模块进入门槛

### 3.1 `M2` 多核 I/O

已满足的进入条件：

- battle lifecycle 已稳定
- `GatewayServer` bridge seam 已有最小灰度方式
- v1/v2 smoke test 都可持续运行

当前已完成：

- `AsioIoEngine`
- `dispatch_to_core()` / `dispatch_to_all_cores()`
- pinned listen / multi-listener ingress
- `GatewayServer` / `DemoServer` 的 `IoEngine` 接入
- session-core aware outbound
- core diagnostics 与 management snapshot
- accept policy (RoundRobin/LeastLoaded/Fixed)
- SPSC lock-free ring buffer 跨核 mailbox
- session counting

**M2 全部完成**：SO_REUSEPORT（MultiIoAcceptor）+ actor 核心亲和（ActorRef::core_id + 跨核 SPSC 路由）已落地。

### 3.2 `M6` ECS world / battle runtime

**M6 全部完成**：7-system ECS pipeline（Clock→Input→Movement→Combat→AOI→Lifecycle→Replay）+ authoritative simulation + deterministic replay + AOI 空间管理。

### 3.3 `M5` 数据层 v2

**M5 全部完成**：LruCache + WriteBehindDataStore + Snapshotable mixin + CachedBattleDataStore。

### 3.4 `M4` 分布式原语

**M4 S0-S4 全部完成**：gateway-only ingress + login/room/battle 独立 backend + ServiceRegistry TTL/心跳/摘除 + BackendMetrics。

cluster router / remote actor transport / 一致性哈希 仍为远期目标。

### 3.5 `M3` 内存架构重构

**M3 全部完成**：BumpArena + ObjectPool<T, BlockSize> + CacheLine/HotCold。

### 3.6 `M7` 运维成熟度

**M7 全部完成**：DiagnosticsManager + HealthCheck + FeatureFlags + TraceContext+Span。

OpenTelemetry 外部 SDK 集成 / K8s operator 需外部基础设施支持时再推进。

## 4. v2.0.0 收束后建议

v2.0.0 七大模块全部落地后，后续可关注：

1. **gateway demo 接入 backend 服务** — 端到端多进程验证
2. **examples/ 代码瘦身** — v2_room_backend/v2_battle_backend 核心逻辑下沉到 library
3. **测试补强** — ecs/player/room 模块测试覆盖加深
4. **配置完整性** — gateway.json 补全 v2 shadow_bridge 字段

## 5. 远期目标（不进入当前批次）

- K8s Operator + 服务网格集成
- OpenTelemetry 外部 SDK（Jaeger/Zipkin）
- 一致性哈希分片 / 领导者选举
- 远程 Actor transport / 分布式 Runtime
- `v2` 替换 `v1` 默认入口
