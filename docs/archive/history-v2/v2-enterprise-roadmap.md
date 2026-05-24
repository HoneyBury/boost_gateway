# v2.x 企业级迭代路线

> 基准：v2.0.0 七大模块（M1-M7）全部落地，473 测试通过。
> 目标：将 BoostAsioDemo 从"功能完整的游戏服务器原型"演进为"企业级高性能游戏服务器框架"。

## 1. 企业级框架验收标准

一个企业级游戏服务器框架需要在以下五个维度满足最低标准：

### 1.1 性能（Performance）

| 指标 | 当前状态 | 目标（v2.3.0） |
|---|---|---|
| 单核连接吞吐 | 未测定 | ≥ 50K connections/s accept |
| 消息处理延迟 | 未测定 | P99 ≤ 5ms (local), P99 ≤ 50ms (multi-process) |
| 战斗帧同步延迟 | 未测定 | P99 ≤ 100ms 输入到广播 |
| 内存占用基线 | 未测定 | ≤ 2GB @ 10K 在线玩家 |
| CPU 利用率 | 未测定 | ≥ 80% 多核利用率（SO_REUSEPORT） |
| 零拷贝路径覆盖 | BufferPool 已集成 | 读/写完整路径零拷贝 |

### 1.2 可靠性（Reliability）

| 指标 | 当前状态 | 目标（v2.3.0） |
|---|---|---|
| 故障恢复时间 | 无数据 | 后端故障自动摘除 ≤ 5s（ServiceRegistry TTL） |
| 优雅关闭 | GatewayServer 已实现 | 全部 4 服务 ≤ 10s 排空连接 |
| 数据持久化 | M5 WriteBehind 已实现 | ≥ 99.99% 战斗结算落盘成功率 |
| 断路器 | 未实现 | 后端连续失败 ≥ 3 次自动熔断 30s |
| 背压保护 | 未实现 | 写队列超阈值拒绝新连接 |

### 1.3 可观测性（Observability）

| 指标 | 当前状态 | 目标（v2.2.0） |
|---|---|---|
| 指标标准 | Prometheus 文本格式 | OpenMetrics 标准格式 |
| 分布式追踪 | 自建 TraceContext | OpenTelemetry OTLP 导出 |
| 健康检查 | DemoServer HTTP /health | 全部 4 服务标准化 /health + /ready |
| 告警规则 | 17 条规则 | 覆盖全部 4 服务 + 基础设施 |
| 仪表板 | Grafana dashboard.json | 预配置导入即用 |

### 1.4 安全性（Security）

| 指标 | 当前状态 | 目标（v2.2.0） |
|---|---|---|
| 认证 | dev provider + token 校验 | JWT + 可插拔 auth provider |
| 授权 | 无 | 基于角色的消息级访问控制 |
| 速率限制 | 网关全局限频 | 每用户/每 IP/每消息类型三级限频 |
| 传输安全 | 未启用 | TLS 1.3 可选开启 |
| 审计日志 | AUDIT_LOG 基础 | 结构化 JSON 审计事件 |

### 1.5 可运维性（Operability）

| 指标 | 当前状态 | 目标（v2.2.0） |
|---|---|---|
| 部署方式 | Docker Compose + systemd | + K8s Helm Chart |
| 配置管理 | 4 个 JSON 配置文件 | 热加载 + 环境变量覆盖 + 配置校验 |
| 滚动更新 | 不支持 | K8s rolling update + 优雅终止 |
| 容量规划 | 无数据 | 性能基线报告 + 扩容公式 |
| 灾难恢复 | 无方案 | 备份策略 + 恢复 runbook |

---

## 2. 版本迭代规划 (实际完成)

```
v2.0.0 ✅    v2.0.1 ✅    v2.0.2 ✅    v2.1.0 ✅    v2.2.0 ✅    v2.3.0 ✅
M1-M7       H1-H6       B1-B6       E1-E6       S1-S7       G1-G5
473 测试    生产加固     性能基线     集成验证     安全加固     游戏特性

v2.4.0 ✅    v2.5.0 ✅    v2.6.0 ✅    v3.0.0
SDK 封装     全量测试     文档+环境     分布式运行时
556 测试     576 测试     576 测试
```

### 2.1 v2.0.1 — 生产加固 ✅ (已完成)

### 2.2 v2.0.2 — 性能基线与负载测试 ✅ (已完成 2026-05-12)

**目标**：建立性能数字基线，指导容量规划和优化方向。

| 编号 | 条目 | 说明 |
|---|---|---|
| B1 | 性能基准测试套件 | 基于 `gateway_pressure` 的 9 种场景扩展至 v2 多进程架构 |
| B2 | 吞吐量基线 | 单 gateway 多核吞吐上限、线性扩容系数 |
| B3 | 延迟基线 | P50/P90/P99 端到端延迟分布（gateway → backend → 响应） |
| B4 | 资源基线 | 10K 在线玩家下的 CPU/内存/fd 用量 |
| B5 | SLO/SLI 定义 | 可用性目标（99.9%）、延迟目标（P99 ≤ 50ms）、错误率目标（≤ 0.1%） |
| B6 | 容量规划文档 | 基于 B1-B4 数据产出扩容公式和硬件推荐 |

**验收标准**：
- ✅ 输出 `docs/performance-baseline.md` 含全部 6 项框架（待填充实测数据）
- ✅ 负载测试脚本可重复执行（`v2_gateway_pressure` 工具）
- ✅ 测量基础设施就绪（`LatencyHistogram`、`ThroughputTracker`、`BackendMetrics::record_latency`、`DiagnosticsSnapshot::messages_per_second`）
- 待运行：确定单 gateway 实例的推荐连接数上限（需启动 4 进程拓扑后实测）

**实现清单**：
| 文件 | 说明 |
|---|---|
| `include/v2/benchmark/latency_histogram.h` | 指数分桶延迟直方图 (14 桶, 1ms→30s)，支持 P50/P90/P99 |
| `include/v2/benchmark/throughput_tracker.h` | 滑动窗口吞吐量计数器 (5s 窗口, 10 子桶) |
| `examples/v2_gateway_pressure/` | v2 多进程 benchmark harness (echo/battle/stability 场景, JSON 输出) |
| `include/v2/gateway/backend_metrics.h` | 新增 `total_latency_us`/`latency_sample_count` + `record_latency()` |
| `src/v2/gateway/gateway_service_bridge.cpp` | `route()` 中记录 backend 往返延迟 |
| `include/v2/diagnostics/diagnostics_manager.h` | `SystemSummary` 新增 `total_outbound_dispatches`/`messages_per_second` |
| `docs/performance-baseline.md` | 性能基线报告 (测量方法 + SLO/SLI 定义 + 容量规划公式) |

### 2.3 v2.1.0 — 多进程集成验证

**目标**：4 个服务以真实 TCP 连接协作，覆盖完整的生命周期和异常场景。

| 编号 | 条目 | 说明 |
|---|---|---|
| E1 | 多进程集成测试框架 | 启动 4 个真实进程，通过 socket 通信，Python 或 C++ 测试 driver |
| E2 | 完整业务流程测试 | 登录 → 创建房间 → 加入 → 准备 → 开战 → 帧同步 → 结算 → 回放 |
| E3 | 故障注入测试 | 后端宕机恢复、网络分区、超时场景、重复连接 |
| E4 | 服务发现演练 | ServiceRegistry TTL 过期、心跳恢复、实例上下线 |
| E5 | 长时间浸泡测试 | 8 小时连续运行，验证无内存泄漏、fd 泄漏、日志不爆盘 |
| E6 | 客户端协议规范 | 输出 `../history-v2/v2-protocol-spec.md`：消息格式、错误码、状态机、重连协议 |

**验收标准**：
- E2E 测试覆盖 ≥ 15 个业务场景
- 故障注入测试覆盖 ≥ 10 个异常场景
- 8 小时浸泡测试无资源泄漏
- 协议规范文档对外可交付

### 2.4 v2.2.0 — 安全与可观测性

**目标**：达到企业级安全基线，可观测性满足生产运维需求。

| 编号 | 条目 | 说明 |
|---|---|---|
| S1 | JWT 认证 | 替换 dev provider，支持 RS256 签名 JWT，token 过期和刷新 |
| S2 | 消息级授权 | 基于用户角色（player/admin/observer）限制消息类型 |
| S3 | 多级速率限制 | 全局 + 每 IP + 每用户 + 每消息类型，令牌桶算法 |
| S4 | OpenTelemetry 集成 | OTLP/gRPC 导出到 Jaeger，W3C TraceContext 格式 |
| S5 | 结构化审计日志 | JSON 格式审计事件，含 schema 定义，可对接 SIEM |
| S6 | 标准化健康检查 | `/health`（就绪）、`/ready`（可服务）、`/metrics`（OpenMetrics 格式） |
| S7 | Grafana 仪表板完善 | 4 个服务各一个 dashboard，含 RED 指标（Rate/Error/Duration） |

**验收标准**：
- JWT 认证覆盖率 100%（所有 v2 入口）
- OpenTelemetry trace 从 gateway → login → room → battle 全链路可见
- 告警规则覆盖全部 4 服务
- 安全测试覆盖认证绕过、重放攻击、令牌伪造

### 2.5 v2.3.0 — 高级游戏特性与性能优化

**目标**：补充游戏服务器核心业务特性，达到生产性能上限。

| 编号 | 条目 | 说明 |
|---|---|---|
| G1 | 匹配系统 | 基于 MMR 的匹配队列，支持 1v1/2v2/4v4，可配置匹配超时 |
| G2 | 排行榜 | 基于 Redis 风格 Sorted Set 的排行榜服务 |
| G3 | 反外挂基础 | 服务端输入校验（移动距离/速度/冷却时间）、异常行为检测 |
| G4 | 帧同步优化 | 客户端预测 + 服务端和解（reconciliation）基础框架 |
| G5 | 消息 Schema 校验 | 基于 JSON Schema 的请求/响应格式校验，拒绝畸形消息 |
| G6 | 性能优化 | 基于 v2.0.2 基线的热点优化：序列化/反序列化路径、内存分配热点 |
| G7 | 高可用架构 | 多 gateway 实例负载均衡、battle backend 无状态化改造 |

**验收标准**：
- 匹配延迟 ≤ 30s（P99）
- 排行榜查询 ≤ 1ms（P99）
- 反外挂检测覆盖率 ≥ 80% 已知作弊模式
- 性能基线相比 v2.0.2 提升 ≥ 30%

### 2.6 v3.0.0 — 分布式运行时 ✅ (已完成 2026-05-13)

| 编号 | 条目 | 说明 |
|---|---|---|
| D1 | Cluster Router | ✅ 跨节点服务发现和路由 |
| D2 | Remote Actor Transport | ✅ 跨进程 `actor::tell()`，当前为轻量 typed envelope 编码 |
| D3 | 一致性哈希分片 | ✅ 按 room_id/battle_id 分配 |
| D4 | 领导者选举 | ✅ Raft 基础实现 |
| D5 | K8s Operator | ✅ 基础框架 (k8s_operator_test.cpp) |
| D6 | gRPC Proto | ✅ 4 个 proto 定义 |
| D7 | TLS 配置 | ✅ SecurityPolicy + TlsSessionConfig |
| D8 | OpenTelemetry | ✅ OtlpExporter + TraceContext |

### 2.7 v3.1.0 — 生产基础设施 ✅ (已完成 2026-05-14)

| 编号 | 条目 | 说明 |
|---|---|---|
| E1 | Redis 集成 | ✅ hiredis + RedisClient + RedisEventStore (16 tests) |
| E2 | Docker 生产构建 | ✅ 9 服务栈 + multi-stage build + build_docker.sh |
| E3 | K8s 部署验证 | ✅ 6 个独立 Deployment + HPA + PDB + 反亲和 |
| E4 | TLS/mTLS + FeatureFlags | ✅ SecurityPolicy 接入 bridge + 灰度控制 (751 tests) |

### 2.8 v3.2.0 — Redis + Raft 集群 ✅ (已完成 2026-05-14)

| 编号 | 条目 | 说明 |
|---|---|---|
| P1 | CachedBattleDataStore 写回修正 | ✅ write-back 语义 + flush() + LruCache 增强 |
| P2 | RedisConnectionPool | ✅ 连接池 + RAII PooledConnection + 7 tests |
| P3 | Raft 集群验证 | ✅ 3节点选举 + AB/BA死锁修复 + 10 tests |

### 2.9 v3.3.0 — P0-P3 模块全量集成 ✅ (已完成 2026-05-14)

| 优先级 | 条目 | 说明 |
|---|---|---|
| P0a | Matchmaking/Leaderboard 路由 | ✅ ServiceId 枚举 + Bridge 槽位 + DemoServer 配置 + 健康检查 |
| P0b | Redis 持久化 Leaderboard | ✅ env-opt-in RedisLeaderboard 接入 Leaderboard 后端 |
| P1a | ClusterRouter 接入 | ✅ 服务发现路由 + 静态 BackendConfig 回退 |
| P1b | OtlpExporter 接入 | ✅ OTLP 导出，OTEL_EXPORT_ENDPOINT env opt-in |
| P1c | CachedBattleDataStore 接入 | ✅ JsonFileBattleDataStore 包装为 LRU+WriteBehind |
| P2 | SchemaValidator 接入 | ✅ Runtime 6 条桥接路径 JSON Schema 校验 |
| P3 | InputValidator 接入 | ✅ BattleActor 输入校验，静默拒绝 |

### 2.10 v3.3.x — 验证与控制面收口（已完成）

> 注：v3.3.x 收口期已在 v3.4.0 转化为生产就绪加强阶段。本节记录已在 v3.3.2 全部完成。

| 条目 | 当前状态 | 说明 |
|---|---|---|
| V1 | Typed ServiceEnvelope helper | ✅ | `include/v3/proto/envelope_codec.h` 已从轻量 wrapper 演进到 typed kind helper |
| V2 | Backend envelope兼容 | ✅ | `login/room/battle/match/leaderboard` 后端均接受 wrapped payload |
| V3 | 恢复/追平验证 | ✅ | 已补重启恢复与 follower catch-up 验证 |
| V4 | Operator TLS/cert-manager | ✅ | `Secret` + `Certificate` reconcile |
| V5 | Operator rollout-aware status | ✅ | `desiredReplicas`、`components[]`、`Ready/Progressing/Degraded/TLSReady` |
| V6 | Proto generation入口 | ✅ | `scripts/generate_proto_cpp.ps1` + `generate_v3_proto_cpp` helper target |

---

## 3. 文档产出计划

| 版本 | 文档 | 内容 |
|---|---|---|
| v2.0.1 | `docs/architecture-acceptance-criteria.md` | 五维度验收标准（本文档 §1） |
| v2.0.2 | `docs/performance-baseline.md` | 吞吐/延迟/资源基线 + 容量规划 |
| v2.1.0 | `../history-v2/v2-protocol-spec.md` | 客户端协议规范 |
| v2.1.0 | `docs/e2e-test-plan.md` | 端到端测试用例清单 |
| v2.2.0 | `docs/security-model.md` | 认证/授权/审计安全模型 |
| v2.2.0 | `docs/observability-guide.md` | 指标/追踪/告警运维手册 |
| v2.3.0 | `docs/matchmaking-design.md` | 匹配系统设计文档 |
| v2.3.0 | `docs/anti-cheat-baseline.md` | 反外挂基础策略 |

---

## 4. 版本节奏

```
v2.0.0  ████████████████████████████████████ 已完成 (2026-05-12, 473 tests)
v2.0.1  ████████████████████████████████████ 已完成 (2026-05-12, 6 项加固)
v2.0.2  ████████████████████████████████████ 已完成 (2026-05-12, 6 项性能基线)
v2.1.0  ████████████████████████████████████ 已完成 (2026-05-12, 6 项集成验证)
v2.2.0  ████████████████████████████████████ 已完成 (2026-05-12, 7 项安全/可观测性)
v2.3.0  ████████████████████████████████████ 已完成 (2026-05-12, 7 项高级特性)
v2.4.0  ████████████████████████████████████ 已完成 (2026-05-13, SDK 封装)
v2.5.0  ████████████████████████████████████ 已完成 (2026-05-13, 全面测试)
v2.6.0  ████████████████████████████████████ 已完成 (2026-05-13, 文档+环境)
v3.0.0  ████████████████████████████████████ 已完成 (2026-05-13, 655 tests)
v3.1.0  ████████████████████████████████████ 已完成 (2026-05-14, 751 tests)
v3.2.0  ████████████████████████████████████ 已完成 (2026-05-14, 780 tests)
v3.3.0  ████████████████████████████████████ 已完成 (2026-05-14, 780 tests, P0-P3 13模块集成)
v3.3.2  ████████████████████████████████████ 已完成 (2026-05-16, 版本与交付面初轮收束)
v3.4.0  ████████████████████████████████████ 已完成 (2026-05-23, P0性能优化+R7模块收束)
v3.x 生产就绪 ████████████░░░░░░░░░░░░░░░░░░ 持续进行 (架构验收/性能闭环/Actor线程边界/通信契约)
```

---

## 5. 当前决策点

v2.0.0 已具备发布基础（`cmake --install`、Docker 镜像、systemd 部署、473 测试）。

当前建议优先级：

1. **收口 CI / 平台差异测试问题** — 先恢复主线稳定回归能力
2. **继续推进 generated protobuf/gRPC** — 将 typed helper 升级为正式生成代码链
3. **扩大多节点故障注入矩阵** — leader 切换、follower 追平、重启顺序扰动

> **当前核心原则**：先收口验证链，再推进正式传输层，最后扩展平台控制面能力。
> 
> **当前状态**：v3.4.0 已完成 P0 性能优化与 R7 模块收束，主线推进至 v3.x 生产就绪加强阶段。

## 6. v3.x 生产就绪加强计划

v3.4.0 完成 P0 性能优化与 R7 模块收束后，主线进入生产就绪加强阶段。执行基准见
[`docs/v3-production-readiness-plan.md`](./v3-production-readiness-plan.md)。

该阶段以 12 周为一个完整收口周期，核心目标是把当前的"架构和模块已经接入"推进到"性能有事实数据、架构有实测验收、交付面一致、Actor 多核边界明确、通信契约正式化、控制面和恢复链路可验证"。

| 阶段 | 时间 | 主题 | 必须交付 |
|---|---:|---|---|
| R0 | 1 周 | 版本与交付面收束 | 版本口径统一、install targets 对齐、发布清单 |
| R1 | 3 周 | 性能数据闭环 | Release 基准环境、标准压测矩阵、首版实测性能表 |
| R2 | 2 周 | 架构验收实测 | Actor/I/O/内存/数据/Battle 微基准和验收表 |
| R3 | 2 周 | Actor 多核线程边界 | 并发模型文档、debug 断言、跨核压力测试、shutdown 验证 |
| R4 | 2 周 | 通信契约与可靠性 | proto/gRPC 迁移计划、兼容测试、故障注入矩阵 |
| R5 | 2 周 | 控制面与发布门槛 | Operator e2e、Redis/Raft 恢复、CI 性能门槛、v3.x 生产候选版 |
