# 生产配置 Runbook

更新时间：2026-05-18

本文档说明当前项目的生产配置治理模型。目标是让配置来源、热重载边界、发布校验和回滚路径都可预期，避免 gateway、backend、环境变量和启动参数各自成为事实源。

## 配置治理模型

当前配置按三类管理：

| 类型 | 用途 | 来源 | 生效方式 |
| --- | --- | --- | --- |
| Bootstrap Config | 服务名、监听端口、线程数、配置路径、管理端口 | `config/environments/<env>/*.json`，少量 env/argv 兼容覆盖 | 重启生效 |
| Runtime Config | gateway 后端路由、feature flag、TLS/security policy、部分业务策略 | `config/environments/<env>/*.json` | gateway 部分热重载，backend 当前重启生效 |
| Secret Config | JWT secret、Redis password、Grafana password、外部 webhook token | `config/secrets/.env.example` 作为模板，生产由 Secret Manager / `.env` 注入 | 默认重启生效 |

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

- [config.h](/Users/honeybury/workspace/BoostAsioDemo/include/app/config.h)
- [config.cpp](/Users/honeybury/workspace/BoostAsioDemo/src/app/config.cpp)
- [demo_server.cpp](/Users/honeybury/workspace/BoostAsioDemo/src/v2/gateway/demo_server.cpp)
- [config_watcher.cpp](/Users/honeybury/workspace/BoostAsioDemo/src/v2/config/config_watcher.cpp)

## Backend 配置

backend 已统一走 `app::config::load_backend_service_config()`。环境变量仍保留为兼容 overlay，但不再是唯一事实源。

### Login

文件：`config/environments/<env>/login.json`

主要字段：

- `service.name`
- `service.port`
- `service.config_version`
- `auth.mode`
- `auth.jwt_issuer`
- `auth.jwt_audience`

Secret 通过环境变量注入：

- `V2_LOGIN_JWT_SECRET`
- `V2_LOGIN_JWT_PUBLIC_KEY`
- `V2_LOGIN_JWT_PRIVATE_KEY`

生产模式下 `auth.mode=production|prod|jwt`，必须配置 JWT secret 或 public key，否则启动失败。

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

### Leaderboard

文件：`config/environments/<env>/leaderboard.json`

主要字段：

- `service.port`
- `redis.host`
- `redis.port`
- `redis.leaderboard_key`
- `raft.*`

`REDIS_PASSWORD` 属于 secret，不建议写入普通 JSON 文件。未配置 `redis.host` 时使用内存 leaderboard。

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
