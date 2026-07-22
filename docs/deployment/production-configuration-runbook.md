# 生产配置 Runbook

更新时间：2026-05-18

本文档说明当前项目的生产配置治理模型。目标是让配置来源、热重载边界、发布校验和回滚路径都可预期，避免 gateway、backend、环境变量和启动参数各自成为事实源。

## 配置治理模型

当前配置按三类管理：

| 类型 | 用途 | 来源 | 生效方式 |
| --- | --- | --- | --- |
| Bootstrap Config | 服务名、监听端口、线程数、配置路径、管理端口 | `config/environments/<env>/*.json`，少量 env/argv 兼容覆盖 | 重启生效 |
| Runtime Config | gateway 后端路由、feature flag、TLS/security policy、部分业务策略 | `config/environments/<env>/*.json` | gateway 部分热重载，backend 当前重启生效 |
| Secret Config | JWT verification public key、Redis password、Grafana password、外部 webhook token | `config/secrets/.env.example` 作为模板，生产由 Secret Manager / `.env` 注入 | 默认重启生效 |

不要追求“所有配置都热重载”。企业级游戏服务器更重要的是：哪些能热重载、哪些必须重启、失败时是否保留旧配置，这些语义必须稳定。

## 目录结构

统一配置目录：

```text
config/
  environments/
    local/
    docker/
    production/
  schemas/
    gateway.schema.json
    backend-service.schema.json
    login.schema.json
    room.schema.json
    battle.schema.json
    matchmaking.schema.json
    leaderboard.schema.json
  secrets/
    .env.example
```

实际服务默认读取：

- gateway：`CONFIG_PATH` 或 `GATEWAY_CONFIG_PATH`，默认回退 `config/gateway.json`
- login：`CONFIG_PATH` 或 `LOGIN_CONFIG_PATH`，默认回退 `config/environments/local/login.json`
- room：`CONFIG_PATH` 或 `ROOM_CONFIG_PATH`，默认回退 `config/environments/local/room.json`
- battle：`CONFIG_PATH` 或 `BATTLE_CONFIG_PATH`，默认回退 `config/environments/local/battle.json`
- matchmaking：`CONFIG_PATH` 或 `MATCHMAKING_CONFIG_PATH`，默认回退 `config/environments/local/matchmaking.json`
- leaderboard：`CONFIG_PATH` 或 `LEADERBOARD_CONFIG_PATH`，默认回退 `config/environments/local/leaderboard.json`

Docker Compose 默认给每个进程注入对应 `CONFIG_PATH=/app/config/environments/docker/<service>.json`。

## 配置入口矩阵

| 组件 | 主事实源 | 兼容覆盖 | 热重载 |
| --- | --- | --- | --- |
| gateway | `config/environments/<env>/gateway.json` | `CONFIG_PATH` / `GATEWAY_CONFIG_PATH` | 部分支持 |
| login backend | `config/environments/<env>/login.json` | `SERVICE_PORT`、`LOGIN_PORT`、`V2_LOGIN_*`、旧端口 argv | 否 |
| room backend | `config/environments/<env>/room.json` | `SERVICE_PORT`、`ROOM_PORT`、旧端口 argv、`V2_BATTLE_MAX_FRAMES` | 否 |
| battle backend | `config/environments/<env>/battle.json` | `SERVICE_PORT`、`BATTLE_PORT`、旧端口 argv | 否 |
| matchmaking backend | `config/environments/<env>/matchmaking.json` | `SERVICE_PORT`、`MATCH_PORT`、`MATCHMAKING_PORT`、`RAFT_*` | 否 |
| leaderboard backend | `config/environments/<env>/leaderboard.json` | `SERVICE_PORT`、`LEADERBOARD_PORT`、`REDIS_*`、`RAFT_*` | 否 |
| Redis | `env/redis/redis.conf` | Compose env / command | 按 Redis 自身语义 |
| Prometheus | `env/monitoring/prometheus*.yml` | Compose env | 重载或重启容器 |
| Alertmanager | `env/monitoring/alertmanager.yml` | Compose env | 重载或重启容器 |
| Grafana | provisioning 文件 + `GRAFANA_ADMIN_PASSWORD` | Compose env | 通常重启生效 |

## Gateway 配置

主配置文件：

- 本地：`config/environments/local/gateway.json`
- Docker：`config/environments/docker/gateway.json`
- 生产模板：`config/environments/production/gateway.json`
- 历史兼容：`config/gateway.json`

核心字段：

- `gateway.port`：业务 TCP 入口，重启生效
- `gateway.http_management_port`：管理面 HTTP 端口，重启生效
- `gateway.io_threads` / `gateway.business_threads`：线程装配，重启生效
- `session.*`：连接和心跳策略，当前按重启生效
- `backends.*.host` / `backends.*.port`：gateway 到后端的路由，支持热重载
- `feature_flags`：支持热重载
- `tls` / `security_policy`：支持热重载，但证书文件本身仍需按发布流程管理

代码入口：

- `include/app/config.h`
- `src/app/config.cpp`
- `src/v2/gateway/demo_server.cpp`
- `src/v2/config/config_watcher.cpp`

## Backend 配置

backend 已统一走 `app::config::load_backend_service_config()`。环境变量仍保留为兼容 overlay，但不再是唯一事实源。

### Login

文件：`config/environments/<env>/login.json`

主要字段：

- `service.name`
- `service.port`
- `service.config_version`
- `auth.mode`：本地/Docker 使用 `dev`；生产必须为 `external-jwt`。
- `auth.jwt_public_key_pem`、`auth.jwt_key_ring`、`auth.jwks_uri`：互斥的三种
  RS256 public key source。
- `auth.jwt_issuer`、`auth.jwt_audience`：生产 JWT claim 边界。

生产 Login Backend 只验证外部身份提供方签发的、带 `exp` 的 RS256 JWT。它不接受 `jwt_secret` 或私钥，不负责注册账户、guest 登录或 refresh token 签发；这些操作必须由外部身份提供方完成。该约束避免把进程内存中的演示账户状态误用作生产凭证库。

生产启动前必须从 Secret Manager 注入单公钥、静态多 `kid` key ring 或显式
JWKS URI 三种来源之一，并同时配置 issuer 与 audience。单公钥示例：

```bash
export V2_LOGIN_AUTH_MODE=external-jwt
export V2_LOGIN_JWT_PUBLIC_KEY="$(cat issuer-public.pem)"
export V2_LOGIN_JWT_ISSUER=https://issuer.example.invalid
export V2_LOGIN_JWT_AUDIENCE=boost-game-client
v2_login_backend --config config/environments/production/login.json
```

静态轮换时先部署同时包含旧、新 `kid` 的 `jwt_key_ring`，再由身份提供方切换
签发 key。JWKS 模式使用 `jwks_uri` 和精确的 `jwks_allowed_hosts`，生产只允许
HTTPS；resolver 会执行有界后台刷新、TTL/stale grace 和过期 fail-closed。迁移、
outage 与静态 key-ring 回滚步骤见
`docs/deployment/identity-key-rotation-runbook.md`。默认生产配置不会自动切换到
JWKS，只有对应候选的真实轮换门禁通过后才能显式启用。

生产验签配置通过环境变量注入：

- `V2_LOGIN_JWT_PUBLIC_KEY`
- `V2_LOGIN_JWT_KEY_RING`
- `V2_LOGIN_JWKS_URI`
- `V2_LOGIN_JWKS_ALLOWED_HOSTS`
- `V2_LOGIN_JWKS_CONNECT_TIMEOUT_MS`
- `V2_LOGIN_JWKS_READ_TIMEOUT_MS`
- `V2_LOGIN_JWKS_TTL_MS`
- `V2_LOGIN_JWKS_STALE_GRACE_MS`
- `V2_LOGIN_JWKS_MINIMUM_REFRESH_INTERVAL_MS`
- `V2_LOGIN_JWKS_MAX_RESPONSE_BYTES`
- `V2_LOGIN_JWKS_MAX_KEYS`
- `V2_LOGIN_JWT_ISSUER`
- `V2_LOGIN_JWT_AUDIENCE`

生产模式下 `auth.mode=external-jwt` 必须且只能配置一种 public key source，并
同时配置非空 issuer/audience。`V2_LOGIN_JWT_SECRET` 和
`V2_LOGIN_JWT_PRIVATE_KEY` 只属于开发/测试路径，在生产会导致启动失败。

### Room

文件：`config/environments/<env>/room.json`

主要字段：

- `service.port`
- `battle.max_frames`

`battle.max_frames` 会影响 room 创建战斗时向 battle backend 转发的 `max_frames`。历史环境变量 `V2_BATTLE_MAX_FRAMES` 仍可覆盖，但生产建议改文件配置并重启 room backend。

### Battle

文件：`config/environments/<env>/battle.json`

当前主要配置：

- `service.port`
- `service.config_version`

战斗业务参数后续应继续归入该文件或专门的 runtime config 段，避免散落到环境变量。

### Matchmaking

文件：`config/environments/<env>/matchmaking.json`

主要字段：

- `service.port`
- `raft.node_id`
- `raft.peers`
- `raft.storage_dir`
- `raft.election_timeout_min_ms`
- `raft.election_timeout_max_ms`
- `raft.heartbeat_interval_ms`

`RAFT_*` 环境变量仍兼容，但只作为部署层 overlay。

配置了非空 `raft.storage_dir` 时，`raft.node_id` 必须是非空、最长 256 bytes 且不含 `/`、`\\`、NUL、`.` 或 `..` 的安全路径段，Raft 持久化状态位于 `raft.storage_dir/<node_id>.raft.json`。v3.6 Phase A 写入严格 v1 JSON，包含 schema version、node identity 和 SHA-256 完整性校验；节点启动时会校验字段类型、term/index/log 不变量、checksum 与配置中的 `raft.node_id`。peer ID 作为 opaque 标识允许普通路径字符，但同样要求非空、最长 256 bytes 且不含 NUL。`leader_id` 是易失状态，不写入磁盘。legacy v0 文件首次成功读取后会在同目录保留 `<node_id>.raft.json.v0.bak` 和 `<node_id>.raft.json.migration-v0-v1.json`，再以 durable atomic replace 写入 v1；主文件和两个 sidecar 必须作为同一恢复单元保留。

损坏、截断、未知未来版本、identity 不匹配或迁移 sidecar 冲突都属于启动阻断项；matchmaking 在状态恢复和首次持久化成功前不会监听端口。不要通过删除或清空 `.raft.json` 恢复服务，这会把节点变成全新 Raft 身份状态并可能破坏一致性。应先停止节点，保留主文件及全部 sidecar，采集日志与文件 checksum，再使用已验证备份或 `raft_state_tool downgrade` 处理受支持的 v1-to-v0 回滚；工具失败或不符合 `docs/deployment/raft-schema-migration-runbook.md` 的场景必须保持节点离线。

默认 codec 上限为单个 state 64 MiB、100000 条 log entry、单条 command 1 MiB、node/peer ID 256 bytes。单条非法 RPC/command 会在修改 term/log 前被拒绝，不会污染节点健康状态；本地 state 累积越过总容量或实际 durable write 失败属于 fail-closed 容量/存储故障，节点会锁存为 unhealthy，即使目录随后恢复也不会自动重新加入一致性读写。应在到达上限前规划后续 snapshot/compaction；当前 Phase A 不包含在线压缩或自动清除 state。

Phase B 默认构建会以 `with_raft_protobuf=True` 引入内部 protobuf runtime，但这不会启用 gRPC。Raft reader 可识别严格 legacy JSON 和带 `BGRT` framing/protocol version 的 protobuf v1，节点通过独立 `raft_capabilities` 消息显式记录 peer 能力；超时、无响应或错误响应都不会被推断为支持。当前 RequestVote/AppendEntries writer 仍固定发送 legacy JSON，部署配置没有 protobuf writer 开关；在 Phase C 混合集群门禁完成前不得通过非受支持补丁切换 writer。

### Leaderboard

文件：`config/environments/<env>/leaderboard.json`

主要字段：

- `service.port`
- `redis.host`
- `redis.port`
- `redis.leaderboard_key`
- `raft.*`

`REDIS_PASSWORD` 属于 secret，不建议写入普通 JSON 文件。未配置 `redis.host` 时使用内存 leaderboard。

Leaderboard 使用与 matchmaking 相同的 `raft.storage_dir/<node_id>.raft.json` v1 状态格式、迁移 sidecar 和 fail-closed 恢复规则；Raft 状态未通过校验或首次持久化失败时，服务不会监听端口。Redis 数据恢复不能替代 Raft state 恢复。

## 修改流程

本地开发：

```bash
build/release/examples/v2_room_backend/v2_room_backend --config config/environments/local/room.json
```

Docker：

```bash
docker compose -f env/docker/docker-compose.yml config --quiet
docker compose -f env/docker/docker-compose.yml up -d --no-build --force-recreate room-backend
```

配置治理校验：

```bash
python3 scripts/check_config_governance.py --summary-path runtime/validation/config-governance-summary.json
```

## Config Drift 规则

发布前必须把配置漂移当成阻断项处理。`scripts/check_config_governance.py` 会检查：

- `config/environments/<env>/*.json` 是否齐全、可解析、端口和 `service.config_version` 是否符合约定。
- Docker Compose 是否挂载统一配置目录，并为每个服务注入 `/app/config/environments/docker/<service>.json`。
- K8s gateway ConfigMap 是否保留生产 gateway 的端口、backend route、`feature_flags`、`tls` 和 `security_policy`。
- Docker/K8s/Helm 是否保留 backend TLS profile 的 cert mount / Secret mount，并保持默认关闭。
- Helm 默认值是否保持 TLS 默认关闭，gateway/backend 端口是否与生产配置一致。
- 本 runbook 是否保留配置入口、热重载边界、发布回滚和漂移检查说明。

发现漂移时不要只修改部署文件或只修改 JSON。处理顺序是：先确认 `config/environments/production/*.json` 是否仍是期望事实源，再同步 Docker/K8s/Helm 映射，最后重新生成 summary 并归档到发布记录。

完整发布前建议叠加：

```bash
python3 scripts/check_deploy_operability.py --build-dir build/release
python3 scripts/check_monitoring_operability.py
build/release/sdk/examples/sdk_full_flow_client 127.0.0.1 9201
```

## 热重载边界

当前支持热重载：

- gateway `backends`
- gateway `feature_flags`
- gateway `tls`
- gateway `security_policy`

当前按重启生效：

- gateway 监听端口、管理端口、线程数
- backend 端口
- backend Redis / Raft / JWT / battle 参数
- backend TLS listener 开关、证书路径、CA 和 verify mode
- secret
- Prometheus / Alertmanager / Grafana provisioning

热重载失败时，gateway 现有逻辑会跳过无法解析的配置文件并保留运行态。后续如果扩展 backend 热重载，必须满足：先校验，再原子替换，失败保留旧配置，并暴露配置版本和 reload 结果指标。

## 发布和回滚建议

配置变更发布前：

1. 修改 `config/environments/<env>/*.json`
2. 更新 `service.config_version`
3. 运行 `scripts/check_config_governance.py`
4. 运行 Compose/K8s 渲染校验
5. 对需要重启的服务执行滚动重启
6. 检查 `/health`、Prometheus targets、SDK full-flow

回滚：

- 回退配置文件到上一版本
- 对非热重载字段重启对应服务
- 对 gateway runtime 字段等待 watcher 或主动重启 gateway
- 确认 `service.config_version` 和业务 smoke 结果回到预期

## 下一步治理方向

- 将 backend reload 结果和 config version 暴露为指标
- 将 schema 校验从轻量 gate 升级为完整 JSON Schema 校验
- 将 secret 接入 Vault、云 Secret Manager 或 Kubernetes Secret
- 在 K8s/Helm 中统一挂载 `config/environments/production/*.json`
