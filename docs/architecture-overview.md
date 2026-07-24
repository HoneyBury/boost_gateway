# BoostGateway 架构总览

更新时间：2026-07-24
当前发布基线：v3.6.2

本文档描述当前默认代码和部署边界。实验能力、历史设计和候选交付记录分别以
[当前状态](current-state.md)、[决策目录](decisions/)和
[历史归档](archive/README.md)为准。

## 系统拓扑

```text
Clients
  C++ SDK | Python ctypes | C# P/Invoke
                    |
                    | length-prefixed TCP
                    v
             Gateway :9201
          management :9080
                    |
       +------------+------------+------------+--------------+
       |            |            |            |              |
 Login :9202  Room :9302  Battle :9303  Match :9304  Leaderboard :9305
                                                    |
                                             optional Redis
```

Gateway 是唯一默认客户端入口。五个 backend 使用内部 `BackendEnvelope` 帧协议，不把
backend TCP 端口作为 HTTP 或公共 SDK 接口。Prometheus 默认只抓取 Gateway management
端口的 `/metrics`。

## 组件职责

| 组件 | 主要职责 | 代码入口 |
|---|---|---|
| Gateway | session、packet 校验、Actor dispatch、路由、熔断、限流、health/metrics | `src/v2/gateway/`, `examples/v2_gateway_demo/` |
| Login | 开发身份、外部 JWT/JWKS 验证、登录和注册 contract | `src/v2/login/` |
| Room | 房间生命周期、成员、owner、ready 和 battle 协调 | `src/v2/room/` |
| Battle | 实例生命周期、ECS、输入、快照、结算和 replay | `src/v2/battle/`, `src/v2/realtime/` |
| Matchmaking | 匹配队列、结果推送和可选 Raft 状态复制 | `src/v2/match/` |
| Leaderboard | 提交、查询、可选 Redis 持久化和 Raft 状态复制 | `src/v2/leaderboard/` |
| SDK | TCP transport、协议 codec、C++ API、C ABI 和语言 wrapper | `sdk/` |

## 请求链路

主请求路径是：

```text
Client -> Gateway Session -> GatewayActor -> GatewayServiceBridge
       -> ClusterRouter/static backend -> BackendConnection
       -> backend handler -> response/push -> Client
```

具体行为：

1. `project_net` 解析 length-prefixed packet 并执行大小、版本和 session 流控检查。
2. `DemoServer` 将非 fast-path 消息送入 `SessionAdapter` 和 `GatewayActor`。
3. `Runtime` 校验 typed request、session 状态和角色权限，并选择目标 service。
4. `GatewayServiceBridge` 通过 cluster router 或静态配置选择 backend，应用 connection
   pool、timeout、retry 和 circuit breaker。
5. Backend handler 返回 typed response；Gateway 保留 request correlation 并把 response
   或 push 写回 SDK client。

Matchmaking 到 Battle 的业务链路为：

```text
MatchJoin -> Matchmaking -> MatchFound push -> Room/ready
          -> Battle instance -> input/snapshot -> settlement
          -> Leaderboard submit
```

## 运行时和扩展边界

`v2::realtime::InstanceRuntime` 管理 creating/waiting/running/finishing/finished/closed
生命周期、输入序列、tick、snapshot、settlement 和 resume。业务实现通过
`InstancePlugin` SPI 接入，plugin 异常被 runtime 隔离。

- `BattleInstancePlugin` 是默认 battle backend 实现。
- `TankBattlePlugin` 和 `demo/games/tank_battle/` 用于验证 SPI，不属于默认生产主链。
- `examples/realtime_echo_plugin` 是可选最小 plugin 示例，默认不构建。

ECS 默认执行 movement、combat、AOI、lifecycle 和 replay 等系统。具体地图、碰撞、计分
或胜负规则必须留在 demo/plugin，不能进入 Gateway、Room、Leaderboard 或公共 SDK。

## 协议和一致性

客户端 TCP 帧的固定元数据包含 length、version、message ID、request ID、sequence、
error code 和 flags。业务 payload 使用 schema-backed typed contract；不得新增只靠 raw
JSON 字符串约定的业务消息。

Backend 使用 `BackendEnvelope`，提供 correlation ID、service、message kind 和 payload
encoding。Raft command/state/wire codec 位于 `src/v3/cluster/`，支持受治理的 legacy/
protobuf 兼容窗口。

gRPC 位于 `BOOST_BUILD_GRPC=ON` 条件构建面。它已有 generated schema、SDK 和专项测试，
但默认值仍为 OFF，也不是生产客户端的默认 transport。

## 数据和持久化

| 能力 | 默认状态 | 边界 |
|---|---|---|
| JSON replay/archive | 可用 | Battle 可按配置落盘 |
| Redis leaderboard/event store | 可选 | 未配置 Redis 时 Leaderboard 使用内存实现 |
| Raft | 可选部署 | Matchmaking/Leaderboard 使用显式 HA 配置 |
| SQLite | 构建默认 OFF | 仅在 `BOOST_BUILD_SQLITE=ON` 时启用 |
| OTel persistence/export | 可配置 | 生产启用需 collector 和证据门禁 |

## 部署模型

### 进程内 smoke

```bash
build/contributor-debug/examples/v2_gateway_demo/v2_gateway_demo --script
```

该模式不监听端口，只验证内置 login/room/battle 交换。它不是单进程生产服务器。

### 本地多进程

backend 默认读取 `config/environments/local/<service>.json`。从仓库根目录启动 Login、
Room、Battle、Matchmaking、Leaderboard 后，再启动 Gateway。多进程测试目标会自动管理
所需子进程，优先推荐：

```bash
python3.12 scripts/run_tests.py e2e --build-dir build/contributor-debug --verbose
```

### Docker Compose

`env/docker/docker-compose.yml` 启动六服务、Redis 和观测组件。镜像构建前必须使用
`prepare_docker_runtime_context.py` staging 完整 Release 二进制；具体命令见
[部署快速入门](deployment/deployment-quickstart.md)。

### Kubernetes 和 Operator

- Helm source of truth：`env/k8s/helm/boost-gateway/`
- Operator：`operator/boostgateway-operator/`
- CRD：`operator/boostgateway-operator/config/crd/bases/`

Kubernetes/Operator 结论必须来自真实 kind 或目标集群验证，静态 manifest 校验不能代替
rollout、restart、rollback、scale 和 delete/cleanup 证据。

## 构建和测试边界

默认 CMake provider 是 Conan，默认启用测试、Raft protobuf，关闭 gRPC、SQLite 和业务
demo。首次配置见 [开发者入门](ONBOARDING.md)。

| 层级 | 主要范围 | 本地入口 |
|---|---|---|
| unit | codec、数据结构、Actor、service、runtime | `scripts/run_tests.py unit` |
| integration | gateway/backend、Raft、service bus | `scripts/run_tests.py integration` |
| e2e | 真实多进程业务闭环 | `scripts/run_tests.py e2e` |
| sdk | C++/C ABI 和 business flow | `scripts/run_tests.py sdk` |
| perf/security/fuzz | 显式可选构建 | 对应 CMake option 和专项 workflow |

## 相关文档

- [当前状态](current-state.md)
- [开发者入门](ONBOARDING.md)
- [发布治理](release-governance.md)
- [性能基线](performance-baseline.md)
- [TLS/mTLS Runbook](tls-mtls-runbook.md)
- [平台生产边界](platform-production-boundaries.md)
