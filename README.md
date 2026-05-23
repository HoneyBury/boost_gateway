# Boost 游戏服务器框架 v3.4.0

基于 Boost.Asio 构建的高性能 C++20 游戏服务器框架。

> **版本基线说明**
>
> - `v1.0.0` 对应 git tag `v1.0.0`，是稳定承诺的最小发布面。
> - 当前主线已完成 **`v3.4.0`** 收口：P0 性能优化轮次（连接池/战斗线程/高精度定时器/断路器）、R7 模块收束（持久化/内存/诊断/鉴权）。
> - 当前执行重点已切换到 **v3.x 生产就绪加强阶段**：性能数据闭环、架构实测、交付面收束、Actor 并发边界、通信契约正式化、控制面与恢复链路验收。
> - `develop` / `main` 当前在 **v3.4.0 基线之上**继续推进架构验收闭环与性能数据沉淀，测试总数以当前 `ctest -N` / CI 为准。
> - **v1.x 能力成熟度以 `docs/v1-maturity-matrix.md` 为准**——该矩阵是维护期单一事实源。

## 版本演进

| 版本 | Tag | 测试 | 核心交付 |
|------|-----|------|---------|
| v2.0.0 | ✅ | 473 | M1-M7 七大模块架构 |
| v2.0.1 | ✅ | — | H1-H6 生产加固 |
| v2.0.2 | ✅ | — | B1-B6 性能基线 |
| v2.1.0 | ✅ | 484 | E1-E6 多进程集成验证 + E2E 框架 |
| v2.2.0 | ✅ | 517 | S1-S7 安全加固 (JWT/RBAC/限频/审计/健康检查) |
| v2.3.0 | ✅ | 548 | G1-G5 游戏特性 (匹配/排行榜/反外挂/Schema) |
| v2.4.0 | ✅ | 556 | 客户端 SDK 封装 |
| v2.5.0 | ✅ | 576 | 全量客户端集成测试 |
| v2.6.0 | ✅ | 576 | 文档收束 + 环境配置 (env/) |
| v3.0.0 | ✅ | 655 | D1-D8 分布式运行时 (Cluster/Raft/OTel/gRPC/TLS/一致性哈希) |
| v3.1.0 | ✅ | 751 | Redis + Docker + K8s + TLS/mTLS + FeatureFlags |
| v3.2.0 | ✅ | 780 | RedisLeaderboard + RedisConnectionPool + Raft 集群验证 |
| v3.3.0 | ✅ | 780 | P0-P3 模块集成 (Matchmaking/Leaderboard路由 + ClusterRouter + OtlpExporter + CachedDataStore + SchemaValidator + InputValidator) |
| v3.3.2 | ✅ | 780 | 初轮版本与交付面收束、SDK 企业封装、生产证据框架 |
| v3.4.0 | ✅ | 772+ | P0 性能优化（连接池/战斗线程/断路器/高精度定时器）、R7 模块收束（持久化/内存/诊断/鉴权） |
| v3.x 生产就绪 | 持续进行 | 持续验证 | 性能基线实测、架构验收闭环、交付面收束、Actor 线程边界、proto/gRPC 正式化、发布门槛 |

## 功能概览

当前主线基于 v3.4.0 已完成的 P0 性能优化与 R7 模块收束，继续推进生产就绪加强：

- 性能基线实测与瓶颈分析（echo/battle/capacity 多场景覆盖）
- 架构验收闭环（Actor/I/O/内存/数据/Battle 微基准）
- Actor 多核线程边界固化（并发模型文档、debug 断言、shutdown 验证）
- 通信契约正式化（proto/gRPC 迁移计划、兼容测试）
- 控制面与发布门槛（Operator e2e、Redis/Raft 恢复、CI 性能门禁）

核心架构已覆盖 Actor 模型、多核 I/O、内存管理、服务拆分、数据持久化、ECS 战斗世界、运维成熟度、分布式运行时、生产基础设施（Redis/Docker/K8s/TLS）。

成熟度等级：参考 `docs/v1-maturity-matrix.md` 的 `stable` / `experimental` / `reserved` / `demo-only` 体系，v2 新增模块状态见 `docs/v2-roadmap.md` §2.5。

### v2 核心架构

- **Actor 模型（M1）**：`ActorSystem` 单线程调度，`ActorRef` 消息投递，`PlayerActor` / `RoomActor` / `BattleActor` 业务 Actor，`Snapshotable` mixin 快照/恢复
- **多核 I/O 引擎（M2）**：每核独立 `io_context`，`SO_REUSEPORT` 多核 accept，Actor 核心亲和 + SPSC 跨核 mailbox，`IoEngine` 统一入口
- **内存架构（M3）**：`BumpArena` 指针碰撞分配器，`ObjectPool<T>` 对象池，`CacheLinePad` / `HotCold<T>` 缓存行对齐
- **服务拆分（M4）**：gateway-only ingress（S1），login/room/battle 独立 backend（S2-S3），`ServiceRegistry` TTL 心跳摘除（S4），`BackendMetrics` 路由计数器，`GatewayServiceBridge` 三槽路由
- **数据层 v2（M5）**：`LruCache<K,V>` 线程安全 LRU，`WriteBehindDataStore` 异步写队列，`CachedBattleDataStore` 组合读写缓存
- **AOI/ECS 战斗世界（M6）**：7-system pipeline（Clock→Input→Movement→Combat→AOI→Lifecycle→Replay），权威模拟，确定性回放，`SpatialGrid` + `AoiSystem` 空间管理
- **运维成熟度（M7）**：`DiagnosticsManager` 统一快照，`HealthCheck` 真实健康检查，`FeatureFlags` 百分比灰度，`TraceContext`+`Span` 自研追踪

### v1 保留能力（网络/业务/可观测性/工程）

以下 v1 能力继续保留并在 v2 共仓运行：

- **协议格式（stable）**：`[4字节长度][2字节消息号][4字节请求序号][4字节错误码][1字节标记位][消息体]`
- **批量发包（stable）**：`Session::send_batch()` 用于广播优化
- **零拷贝读包（stable）**：BufferPool 集成
- **登录 / 房间 / 战斗 / 匹配 / 排行榜（stable）**：完整业务闭环（6 服务）
- **Prometheus / JSON 指标导出（stable）**
- **HTTP 观测端点**：`/health` + `/ready` + `/metrics`（DemoServer 自包含管理口）
- **Grafana 仪表板 / Prometheus 告警规则**：已扩展覆盖全部 6 服务 backend + Redis
- **分布式追踪**：`OtlpExporter` OTLP/jaeger 导出（P1b，`OTEL_EXPORT_ENDPOINT` env opt-in）
- **服务发现**：`ClusterRouter` 动态路由 + 静态 BackendConfig 回退（P1a）
- **消息校验**：`SchemaValidator` JSON Schema 6 条桥接路径校验（P2）
- **反外挂**：`InputValidator` 服务端输入校验，静默拒绝（P3）
- **typed envelope 传输**：`include/v3/proto/envelope_codec.h` + `login/room/battle/match/leaderboard` typed request/response 兼容层
- **Operator 控制面**：`BoostGatewayCluster` + `Certificate` reconcile + `Ready/Progressing/Degraded/TLSReady`
- **CMake + FetchContent + 本地 third_party 内网构建（stable）**
- **Docker + docker-compose + GitHub Actions CI/CD（stable）**
- **压测体系**：9 种场景（echo / invalid_token / slow_echo / broadcast_storm / malicious_packet / battle_broadcast / chaos / stability / benchmark）

详细的 v1 能力成熟度说明见 `docs/v1-maturity-matrix.md`。

## 服务拓扑

| 服务 | 端口 | 说明 |
|------|------|------|
| gateway | 9201 | 客户端唯一接入点 |
| login backend | 9202 | 登录认证；本地默认 dev token，生产模式必须配置 JWT |
| room backend | 9302 | 房间管理 |
| battle backend | 9303 | 战斗模拟 |
| matchmaking backend | 9304 | MMR 匹配 |
| leaderboard backend | 9305 | 排行榜 |
| management HTTP | 9080 | /health /ready /metrics |

## 客户端 SDK

项目提供 C++ 客户端 SDK（`sdk/` 目录），封装了 TCP 连接、协议编解码和请求/响应处理。

```cpp
#include "boost_gateway/sdk/client.h"
boost_gateway::sdk::SdkClient client;
client.connect("127.0.0.1", 9201);
client.login("player1", "token:player1");
client.create_room("room_001");
client.send_battle_input("move:100,200");
client.disconnect();
```

生产运行 login backend 时设置：

```bash
V2_LOGIN_AUTH_MODE=production V2_LOGIN_JWT_SECRET=<secret> v2_login_backend 9202
```

未设置 `V2_LOGIN_JWT_SECRET` 或 `V2_LOGIN_JWT_PUBLIC_KEY` 时，生产模式会拒绝启动，避免 dev token fallback 进入生产发布。

详细文档见 `sdk/docs/README.md`，示例见 `sdk/examples/`。

## 环境配置 (env/)

`env/` 目录包含生产运维相关的环境配置：
- `docker/` — Docker Compose 编排（6 服务 + Redis + 监控）
- `k8s/` — Kubernetes 部署清单
- `monitoring/` — Prometheus + Grafana 配置
- `redis/` — Redis 缓存配置
- `cicd/` — GitHub Actions CI/CD 流水线

详见 `env/README.md`。

## 快速开始

```bash
# 构建（macOS / Linux）
cmake --preset default
cmake --build --preset default

# 构建（Windows）
cmake --preset windows-ninja-debug
cmake --build --preset windows-ninja-debug

# 运行当前测试集
ctest --preset default

# 启动 v2 网关（开发演示入口，含全部 6 服务后端路由）
./build/default/examples/v2_gateway_demo/v2_gateway_demo --management-port 9080

# 启动排行榜后端（可选 Redis 持久化）
REDIS_HOST=127.0.0.1 ./build/default/examples/v2_leaderboard_backend/v2_leaderboard_backend

# `/health` 真实健康检查端点（含 6 服务 + registry 检查）
curl http://localhost:9080/health

# 启动 v1 网关（维护入口）
./build/default/examples/echo/echo_server config/gateway.json
```

## 示例程序

| 示例 | 路径 | 类型 | 说明 |
|---|---|---|---|
| **v2 入口** |
| v2_gateway_demo | `examples/v2_gateway_demo/` | 主展示入口 | v2 Actor + IoEngine + 多进程 backend 路由，**当前推荐的运行参考** |
| v2_login_backend | `examples/v2_login_backend/` | backend 服务 | 独立登录后端进程 |
| v2_room_backend | `examples/v2_room_backend/` | backend 服务 | 独立房间后端进程 |
| v2_battle_backend | `examples/v2_battle_backend/` | backend 服务 | 独立战斗后端进程 |
| v2_match_backend | `examples/v2_match_backend/` | backend 服务 | 独立匹配后端进程 |
| v2_leaderboard_backend | `examples/v2_leaderboard_backend/` | backend 服务 | 排行榜后端（可选 Redis 持久化） |
| **v1 入口** |
| echo_server | `examples/echo/` | 集成样例 | 完整 v1 网关装配 |
| echo_client | `examples/echo/` | 工具 | 基础 Echo 客户端 |
| login_demo | `examples/login_demo/` | showcase | 登录流程演示 |
| room_demo | `examples/room_demo/` | showcase | 房间系统演示 |
| battle_demo | `examples/battle_demo/` | showcase | 战斗系统演示 |
| admin_demo | `examples/admin_demo/` | showcase | 管理工具演示（无权限校验，仅 demo 用途） |
| gateway_pressure | `examples/pressure/` | 压测工具 | 9 种场景，支持 JSON 配置 |
| login_server | `examples/login/` | 独立入口 | 实验性独立登录服务入口 |
| room_server | `examples/room/` | 独立入口 | 实验性独立房间服务入口 |
| battle_server | `examples/battle/` | 独立入口 | 实验性独立战斗服务入口 |

## 模块架构

```
include/
├── v2/                     # v2.0.0 核心模块
│   ├── actor/              Actor 模型（Actor, ActorRef, Message）
│   ├── aoi/                AOI 空间管理（AoiSystem, SpatialGrid）
│   ├── battle/             BattleActor, BattleWorld, InputValidator (P3反外挂)
│   ├── common/             模块标记
│   ├── config/             FeatureFlags 特性开关
│   ├── data/               LruCache, WriteBehindDataStore, CachedDataStore
│   ├── diagnostics/        DiagnosticsManager, HealthCheck
│   ├── ecs/                ECS World, Component, Entity, System
│   ├── gateway/            DemoServer, GatewayActor, GatewayServiceBridge, Runtime, SchemaValidator(P2)
│   ├── io/                 IoEngine, Mailbox
│   ├── leaderboard/        LeaderboardService (可选Redis持久化 P0b)
│   ├── match/              MatchmakingService (P0a)
│   ├── memory/             BumpArena, ObjectPool, CacheLine
│   ├── player/             PlayerActor
│   ├── room/               RoomActor, RoomBackendService
│   ├── runtime/            ActorSystem
│   ├── service/            BackendEnvelope, ServiceRegistry, ServiceManifest
│   └── tracing/            TraceContext
├── v3/                     # v3.0.0 分布式
│   ├── cluster/            ClusterRouter(P1a), Raft, ConsistentHash, SecurityPolicy, RemoteActor
│   ├── proto/              ServiceEnvelope typed helper / proto generation入口
│   ├── persistence/        RedisClient, RedisConnectionPool, RedisLeaderboard, RedisEventStore
│   └── tracing/            OtlpExporter(P1b)
├── app/                    配置、日志、崩溃处理、热加载
├── net/                    会话、协议编解码、HTTP管理、速率限制、TLS
├── game/
│   ├── gateway/            网关服务器、会话管理、推送服务、管理指令
│   ├── login/              登录服务、Token校验
│   ├── room/               房间管理
│   ├── battle/             战斗管理、回放播放器
│   ├── match/              匹配服务
│   └── persistence/        玩家数据存储
src/                         各模块实现文件（含 src/v2/）
examples/                    示例程序（v2_* 为 v2 入口，含 match/leaderboard backend）
tests/                       测试（含 780 测试：460+ 单元测试 + 50+ 集成测试 + 多进程测试）
config/                      配置文件
docs/                        项目文档
deploy/                      部署文件（Dockerfile, docker-compose, systemd）
```

## 配置文件

- **v2 服务**：`config/gateway.json`（网关）、`config/login_backend.json`、`config/room_backend.json`、`config/battle_backend.json`
- **v1 服务**：`config/gateway.json`（共用）
- v1 配置字段成熟度（启动生效 / 热更新生效 / 仅预留）见 `docs/v1-maturity-matrix.md` §5.1

## 第三方依赖

通过 CMake `FetchContent` 或本地 `third_party/` 目录管理：

- Boost 1.90.0（Asio、Beast）
- fmt 11.2.0
- spdlog 1.15.3
- nlohmann/json 3.12.0
- GoogleTest 1.17.0

内网构建说明参见 `third_party/README.md`。

## CI/CD

- **CI**：Linux / macOS 走 `default` preset，Windows 走 `windows-ninja-debug` preset
- **Operator**：CI 已拆分 `go test`、`envtest`、`kind smoke`，并断言 `BoostGatewayCluster.status.conditions`
- **Docker**：`main` 分支 push 后自动构建镜像并执行冒烟测试（`--management-port 9080` + `/health` curl 验证）
- **Release**：tag `v*` 后构建、测试、安装、打包并发布 release asset
- 发布与归档细则见 `docs/release-process.md`

## 文档导航

- **开发日志**：`docs/development-log.md`（含 v3.3.0 P0-P3 阶段记录）
- **v2 架构规划与当前状态**：`docs/v2-roadmap.md`
- **v2 设计文档**：`docs/v2-design.md`
- **v2 启动清单与实现状态**：`docs/v2-startup-checklist.md`
- **v2.x 企业级迭代路线**：`docs/v2-enterprise-roadmap.md`
- **v3.x 生产就绪加强规划**：`docs/v3-production-readiness-plan.md`
- **P4 验证清单**：`docs/p4-validation-checklist.md`
- **Operator 实现计划**：`docs/k8s-operator-implementation.md`
- **Proto / envelope 说明**：`proto/README.md`
- **v3.x 环境依赖与生产就绪规划**：`docs/v3-environment-roadmap.md`
- **架构验收标准**：`docs/architecture-acceptance-criteria.md`
- **部署手册**：`deploy/README.md`
- **v1 维护能力可依赖性判断**：`docs/v1-maturity-matrix.md`
- **运行手册**：`docs/runtime-playbook.md`
- **发布流程**：`docs/release-process.md`
- **当前发布说明**：`docs/releases/v1.2.5.md`
- **AI 团队模式协作**：`docs/ai/README.md`

## 许可证

MIT
