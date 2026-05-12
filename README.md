# Boost 游戏服务器框架 v2.6.0

基于 Boost.Asio 构建的高性能 C++20 游戏服务器框架。

> **版本基线说明**
>
> - `v1.0.0` 对应 git tag `v1.0.0`，是稳定承诺的最小发布面。
> - 当前发布版为 **`v2.6.0`**（2026-05-13），全部模块落地，**576 测试通过**。
> - v2.6.0 整体评级为"生产"阶段，下一阶段为 v3.0.0"企业"阶段（分布式运行时）。
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

## 功能概览

v2.0.0 在 v1.x 基础上新增了以下企业级能力模块，构成完整的高性能游戏服务器框架。

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
- **登录 / 房间 / 战斗（stable / experimental）**：完整业务闭环
- **Prometheus / JSON 指标导出（stable）**
- **HTTP 观测端点**：`/health` + `/metrics`（DemoServer 自包含管理口，R4 已升级为真实健康检查）
- **Grafana 仪表板 / Prometheus 告警规则**：已扩展覆盖全部 4 服务 backend
- **CMake + FetchContent + 本地 third_party 内网构建（stable）**
- **Docker + docker-compose + GitHub Actions CI/CD（stable）**
- **压测体系**：9 种场景（echo / invalid_token / slow_echo / broadcast_storm / malicious_packet / battle_broadcast / chaos / stability / benchmark）

详细的 v1 能力成熟度说明见 `docs/v1-maturity-matrix.md`。

## 服务拓扑

| 服务 | 端口 | 说明 |
|------|------|------|
| gateway | 9201 | 客户端唯一接入点 |
| login backend | 9202 | 登录认证 (JWT/dev) |
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

# 运行全部测试（473 个）
ctest --preset default

# 启动 v2 网关（开发演示入口）
./build/default/examples/v2_gateway_demo/v2_gateway_demo --management-port 9080

# `/health` 真实健康检查端点
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
├── v2/                     # v2.0.0 新增
│   ├── actor/              Actor 模型（Actor, ActorRef, Message）
│   ├── aoi/                AOI 空间管理（AoiSystem, SpatialGrid）
│   ├── battle/             BattleActor, BattleWorld, 消息类型
│   ├── common/             模块标记
│   ├── config/             FeatureFlags 特性开关
│   ├── data/               LruCache, WriteBehindDataStore, CachedDataStore
│   ├── diagnostics/        DiagnosticsManager, HealthCheck
│   ├── ecs/                ECS World, Component, Entity, System
│   ├── gateway/            DemoServer, GatewayActor, GatewayServiceBridge, Runtime
│   ├── io/                 IoEngine, Mailbox
│   ├── memory/             BumpArena, ObjectPool, CacheLine
│   ├── player/             PlayerActor
│   ├── room/               RoomActor, RoomBackendService
│   ├── runtime/            ActorSystem
│   ├── service/            BackendEnvelope, ServiceRegistry, ServiceManifest
│   └── tracing/            TraceContext
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
examples/                    示例程序（v2_* 为 v2 入口）
tests/                       测试（含 tests/v2/ 的 300+ 单元测试和 30+ 集成测试）
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
- **Docker**：`main` 分支 push 后自动构建镜像并执行冒烟测试（`--management-port 9080` + `/health` curl 验证）
- **Release**：tag `v*` 后构建、测试、安装、打包并发布 release asset
- 发布与归档细则见 `docs/release-process.md`

## 文档导航

- **v2 架构规划与当前状态**：`docs/v2-roadmap.md`
- **v2 设计文档**：`docs/v2-design.md`
- **v2 启动清单与实现状态**：`docs/v2-startup-checklist.md`
- **v2.x 企业级迭代路线**：`docs/v2-enterprise-roadmap.md`
- **架构验收标准**：`docs/architecture-acceptance-criteria.md`
- **部署手册**：`deploy/README.md`
- **v1 维护能力可依赖性判断**：`docs/v1-maturity-matrix.md`
- **运行手册**：`docs/runtime-playbook.md`
- **发布流程**：`docs/release-process.md`
- **当前发布说明**：`docs/releases/v1.2.5.md`
- **AI 团队模式协作**：`docs/ai/README.md`

## 许可证

MIT
