# 生产部署运行手册

更新时间：2026-05-18

本文档是 v3.3.2 生产稳定化 P0 的部署事实源，覆盖云服务器、Docker Compose、systemd、Kubernetes、监控、备份、回滚和发布后验证。当前目标是稳定现有 gateway + 5 backend 拓扑，不扩展新的业务模块。

## 生产拓扑

```text
client -> gateway:9201
             |
             +-> login-backend:9202
             +-> room-backend:9302
             +-> battle-backend:9303
             +-> matchmaking-backend:9304
             +-> leaderboard-backend:9305 -> redis:6379

gateway:9080 -> /health, /metrics, /metrics/json, /metrics/diagnostics, /metrics/diagnostics/json
prometheus:9090 -> scrapes gateway /metrics, prometheus self, redis-exporter, optional cadvisor
alertmanager:9093 -> receives Prometheus alerts
grafana:3000 -> dashboard backed by Prometheus
```

说明：
- 当前默认部署口径下，Docker Compose、systemd、Kubernetes 都按 `gateway -> login/room/battle/matchmaking/leaderboard` 五后端装配。
- 其中 `leaderboard` 默认可进入业务闭环，但 Redis 持久化仍是可选后端；Redis 不可用时后端会降级到内存路径。
- `TankBattlePlugin`、`GatewayGrpcServer` 和 TLS profile 不属于默认部署拓扑：前者是 demo/plugin 侧能力，gRPC 为 `BOOST_BUILD_GRPC` 条件编译实验能力，TLS 为 opt-in profile，默认生产链路仍是 plain TCP。

生产暴露边界：

- 对公网只开放 gateway TCP `9201`。
- gateway HTTP management `9080`、Prometheus `9090`、Grafana `3000` 只允许内网、堡垒机或 VPN 访问。
- Alertmanager `9093`、Redis host publish `6380`、Redis exporter `9121` 和可选 cAdvisor `8081` 也只允许内网、堡垒机或 VPN 访问。
- 后端服务是自定义 TCP 协议，不暴露 HTTP `/health` 或 `/metrics`。
- Redis 不应暴露到公网。
- Grafana 默认口令必须通过环境变量覆盖，不允许生产继续使用默认值。

## 健康检查语义

- gateway `/health` is a liveness stub：当前只能证明 gateway HTTP management 端口可响应，不等价于完整业务 readiness。
- gateway `/metrics` 是当前 Prometheus 唯一 scrape 目标。
- 后端 readiness/liveness 使用 TCP socket probe，因为后端当前没有 HTTP 管理口。
- Kubernetes 中 gateway readinessProbe 暂时仍使用 `/health`，生产判定必须叠加 SDK full-flow 或 P6 生产证据，不得只依赖该 probe。

## 云服务器 Docker Compose 部署

适用于首次生产试运行、灰度、压测和运维演练。

前置条件：

- Docker 24+ 和 Compose v2。
- 机器防火墙或云安全组只向客户端开放 `9201`。
- 内网访问 `9080`、`9090`、`3000`。
- 挂载卷可持久化 Redis 和日志。

部署：

```bash
git clone <repo>
cd boost_gateway
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:9080/health
curl -fsS http://localhost:9080/metrics
curl -fsS http://localhost:9090/-/ready
curl -fsS http://localhost:9093/-/ready
```

业务冒烟：

```bash
cmake --preset release
cmake --build --preset release --target v2_gateway_demo sdk_full_flow_client
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/release
```

常用排查：

```bash
docker compose logs -f gateway
docker compose exec login-backend nc -z 127.0.0.1 9202
docker compose exec room-backend nc -z 127.0.0.1 9302
docker compose exec battle-backend nc -z 127.0.0.1 9303
docker compose exec redis redis-cli ping
```

回滚：

```bash
docker compose pull
docker compose up -d --no-build
docker compose logs --tail=200 gateway
```

生产镜像必须使用明确 tag；本仓库 Compose 默认适合本地源码构建，正式发布时应将镜像 tag 写入发布记录。

## systemd 部署

适用于不使用容器的单机或内网服务器。

```bash
cmake --preset release
cmake --build --preset release --parallel
ctest --preset release
cmake --install build/release --prefix /usr/local

useradd -r -s /bin/false -d /var/lib/boost-gateway boost-gateway
mkdir -p /var/lib/boost-gateway/{login,room,battle,match,leaderboard}
mkdir -p /var/log/boost-gateway
chown -R boost-gateway:boost-gateway /var/lib/boost-gateway /var/log/boost-gateway
cp deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable boost-login-backend boost-room-backend boost-battle-backend boost-match-backend boost-leaderboard-backend boost-gateway
systemctl start boost-login-backend boost-room-backend boost-battle-backend boost-match-backend boost-leaderboard-backend
systemctl start boost-gateway
```

验证：

```bash
systemctl status 'boost-*'
journalctl -u boost-gateway -f
curl -fsS http://127.0.0.1:9080/health
```

回滚原则：

- 停止 gateway，再停止后端。
- 安装上一版本二进制。
- 先启动后端，再启动 gateway。
- 运行 SDK full-flow 验证。

## Kubernetes 部署

适用于滚动发布、多副本和资源隔离。P0 约束：核心应用镜像必须使用 `v3.3.2` 这样的明确 tag，禁止生产 manifest 使用 `latest`。

部署前检查：

```bash
python3 -m py_compile scripts/deploy_k8s.py scripts/check_deploy_operability.py
python3 scripts/check_deploy_operability.py --summary-path runtime/validation/p0-deploy-operability-summary.json
python3 scripts/deploy_k8s.py --dry-run
```

部署：

```bash
python3 scripts/deploy_k8s.py
kubectl -n boost-gateway get pods
kubectl -n boost-gateway get svc
kubectl -n boost-gateway port-forward svc/gateway 9080:9080
curl -fsS http://localhost:9080/health
```

发布后验证：

```bash
kubectl -n boost-gateway rollout status deploy/gateway
kubectl -n boost-gateway rollout status deploy/login-backend
kubectl -n boost-gateway rollout status deploy/room-backend
kubectl -n boost-gateway rollout status deploy/battle-backend
kubectl -n boost-gateway rollout status deploy/matchmaking-backend
kubectl -n boost-gateway rollout status deploy/leaderboard-backend
```

回滚：

```bash
kubectl -n boost-gateway rollout undo deploy/gateway
kubectl -n boost-gateway rollout status deploy/gateway
kubectl -n boost-gateway logs deploy/gateway --tail=200
```

完整删除：

```bash
python3 scripts/deploy_k8s.py --delete
```

## 监控

当前 Prometheus 默认 scrape gateway HTTP `/metrics`、Prometheus 自身、Redis exporter，并为 `host-observability` profile 预留 cAdvisor：

```bash
docker compose up -d prometheus alertmanager redis-exporter grafana
curl -fsS http://localhost:9090/-/ready
curl -fsS http://localhost:9093/-/ready
python3 scripts/check_monitoring_operability.py --summary-path runtime/validation/monitoring-operability-summary.json
```

Grafana 导入：

- `env/monitoring/grafana-dashboard.json`
- Docker Compose 会通过 `env/monitoring/grafana-datasource.yml` 和 `env/monitoring/grafana-dashboard-provider.yml` 自动配置 Prometheus datasource 与 dashboard。

Prometheus 告警：

- `env/monitoring/prometheus-alerts.yml`
- `env/monitoring/alertmanager.yml`

运维排障：

- `docs/deployment/production-operations-runbook.md`

当前限制：

- 后端服务没有 HTTP `/metrics`，不得配置 Prometheus 直接 scrape 后端 TCP 端口。
- gateway `/health` 不是业务 ready，生产发布后必须叠加 SDK full-flow、P6 production evidence 或固定 runner 证据。
- N2 起 gateway `/metrics` 已导出 backend route latency histogram 和 per-service P50/P90/P99 gauge；P99 告警使用 `gateway_backend_*_p99_latency_us`，容量和长稳结论仍以固定 runner 性能报告为准。
- RSS/fd 告警需要 process exporter 或等价 agent；默认 Compose 没有启用该 exporter。
- cAdvisor 属于可选 `host-observability` profile，只有在宿主允许挂载 `/sys`、`/var/run`、`/var/lib/docker` 等目录时才启用。
- 启用该 profile 时，额外使用 `env/monitoring/prometheus.host-observability.yml` 在 `127.0.0.1:9091` 提供隔离的容器 runtime metrics scrape，不污染默认 Prometheus target 状态。

## 云服务器生产收束

如果当前 Linux 云服务器被指定为生产环境或生产候选环境，执行口径应从“开发环境预演”切换为“固定生产验证主机”。推荐顺序：

```bash
cmake --preset release
python3 scripts/check_fixed_runner_environment.py --profile cloud-production --build-dir build/release
python3 scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --run-2h-soak --run-capacity
python3 scripts/run_cloud_production_closure.py --build-dir build/release --configuration Release --include-compose --include-kind --include-production-evidence
```

说明：

- 第一步确认生产主机依赖完整：Docker、kubectl、kind、Go、systemd、CMake、Ninja。
- 生产主机如已安装系统 Boost/OpenSSL 头文件，应优先复用本机依赖，避免发布收束阶段继续依赖外网下载第三方源码。
- 第二步完成 2h soak 与 capacity 证据收束；8h soak 可复用同一脚本补跑。
- 第三步完成 Docker Compose、kind/control-plane、production evidence 聚合和快照归档。
- 如需形成最终发布记录，再追加 `python3 scripts/run_long_soak_capacity.py --build-dir build/release --configuration Release --run-8h-soak`。

## Redis 备份与恢复

Docker Compose 使用 `redis-data:` volume；Kubernetes 使用 `redis-data` PVC。生产至少需要：

- 发布前确认 Redis 持久化策略。
- 发布前记录 Redis volume/PVC 名称。
- 发布前执行 `redis-cli ping`。
- 回滚或迁移前备份 Redis 数据目录或 PVC 快照。

Redis 不可达时 leaderboard backend 会降级到内存路径；该降级适合可用性保护，不等价于持久化保证。

## N3 部署恢复、回滚与灾备演练

N3 的目标是把“能部署”推进为“能恢复、能回滚、能证明恢复成功”。默认静态门禁为：

```bash
python3 scripts/check_production_recovery_gate.py --summary-path runtime/validation/production-recovery-summary.json
```

固定 runner 或真实环境可在此基础上追加 Docker Compose / Kubernetes 实操演练和 SDK full-flow。
真实演练完成后，复制 `docs/production/production-recovery-drill-record-template.json`，将
`template` 改为 `false`，填入本次环境、故障注入、RTO/RPO、观测和验证 summary，并执行：

```bash
python3 scripts/check_recovery_drill_record.py --record runtime/validation/<drill-record>.json --summary-path runtime/validation/recovery-drill-record-check-summary.json
```

### RTO / RPO 目标

| 场景 | RTO 目标 | RPO 目标 | 恢复后验证 |
| --- | ---: | ---: | --- |
| gateway 重启 / rolling restart | 5 分钟内 | 0，gateway 无持久状态 | `/ready`、`/metrics`、SDK full-flow |
| 单个 backend 重启 | 5 分钟内 | 0，当前业务状态以 gateway/backend 内存态和 Redis/WriteBehind 边界为准 | backend RED counters 无持续 error，SDK full-flow |
| Redis 重启 | 10 分钟内 | 取决于 Redis 持久化与快照策略 | `redis-cli ping`、leaderboard submit/top/rank |
| Compose 镜像回滚 | 10 分钟内 | 0，前提是 Redis volume 未回滚 | Compose `ps`、gateway metrics、SDK full-flow |
| Kubernetes rollout rollback | 10 分钟内 | 0，前提是 PVC 未回滚 | `kubectl rollout status`、SDK full-flow |
| 网络抖动 / backend TCP 不可达 | 10 分钟内 | 0；可能出现短时 gateway backend errors/timeouts | gateway diagnostics、backend recovery checks |

### 恢复演练矩阵

| 演练 | Docker Compose 命令 | Kubernetes 命令 | 成功标准 |
| --- | --- | --- | --- |
| gateway 重启 | `docker compose -f env/docker/docker-compose.yml restart gateway` | `kubectl -n boost-gateway rollout restart deploy/gateway` | 管理端恢复，SDK full-flow 通过 |
| 后端重启 | `docker compose -f env/docker/docker-compose.yml restart room-backend` | `kubectl -n boost-gateway rollout restart deploy/room-backend` | gateway backend errors/timeouts 不持续增长 |
| Redis 恢复 | `docker compose -f env/docker/docker-compose.yml restart redis` | `kubectl -n boost-gateway rollout restart deploy/redis` | `redis-cli ping` 正常，leaderboard 路径恢复 |
| 镜像回滚 | `docker compose -f env/docker/docker-compose.yml up -d --no-build` | `kubectl -n boost-gateway rollout undo deploy/gateway` | 回滚后 SDK full-flow 通过 |
| 配置 reload / 重启生效 | 修改受控配置后按配置 runbook 操作 | 更新 ConfigMap 后 rollout restart | 配置生效方式与回滚方式被记录 |

### Redis 备份 / 恢复记录

每次生产演练至少记录：

- Redis volume / PVC 名称。
- 备份方式：云盘快照、PVC snapshot、RDB/AOF 文件备份或 Compose volume 备份。
- 备份时间、恢复时间、操作者和目标环境。
- 恢复前后的 `redis-cli ping`、leaderboard submit/top/rank 验证结果。
- 如果 Redis 降级期间 leaderboard 使用内存 fallback，必须记录该窗口内的数据一致性风险。

### 发布后验证顺序

1. 等待 Compose healthcheck 或 Kubernetes rollout status。
2. 检查 gateway `/ready`、`/metrics`、`/metrics/diagnostics/json`。
3. 检查 Prometheus targets 和 Grafana dashboard。
4. 运行 `python3 scripts/verify_sdk_full_flow_client.py --build-dir build/release`。
5. 运行 `python3 scripts/collect_docker_production_perf_snapshot.py` 或固定 runner 对应 snapshot。
6. 将 summary 路径写入发布或演练记录。
7. 用 `scripts/check_recovery_drill_record.py` 校验演练记录，确认记录字段和 summary 路径可复核。

## 发布前检查清单

```bash
python3 scripts/check_production_recovery_gate.py --summary-path runtime/validation/production-recovery-summary.json
python3 scripts/check_recovery_drill_record.py --record docs/production/production-recovery-drill-record-template.json --allow-template --summary-path runtime/validation/recovery-drill-record-template-check-summary.json
python3 -m py_compile scripts/deploy_k8s.py scripts/check_deploy_operability.py
python3 scripts/check_deploy_operability.py --build-dir build/release --summary-path runtime/validation/p0-deploy-operability-summary.json
python3 scripts/check_monitoring_operability.py --summary-path runtime/validation/monitoring-operability-summary.json
docker compose config
docker compose -f env/docker/docker-compose.yml config
python3 scripts/deploy_k8s.py --dry-run
python3 scripts/check_reliability_matrix.py
```

发布记录必须包含：

- git commit
- 镜像 tag
- 部署方式：Compose / systemd / Kubernetes
- `deploy-operability-summary.json`
- SDK full-flow 或生产证据 summary
- 已知限制和回滚命令

## P0 完成标准

- 文档、Compose、systemd、Kubernetes 和 Prometheus 的服务拓扑一致。
- Kubernetes 核心应用镜像不使用 `latest`。
- `scripts/check_deploy_operability.py` 能阻断版本标签、镜像 tag、监控 scrape 和健康检查语义漂移。
- 生产部署 runbook 明确区分 liveness、readiness、metrics 和业务冒烟。
