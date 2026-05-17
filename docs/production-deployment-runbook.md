# 生产部署运行手册

更新时间：2026-05-17

本文档是 v3.3.2 生产稳定化 P0 的部署事实源，覆盖云服务器、Docker Compose、systemd、Kubernetes、监控、备份、回滚和发布后验证。当前目标是稳定现有 6 服务拓扑，不扩展新的业务模块。

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
prometheus:9090 -> scrapes gateway /metrics only
grafana:3000 -> dashboard backed by Prometheus
```

生产暴露边界：

- 对公网只开放 gateway TCP `9201`。
- gateway HTTP management `9080`、Prometheus `9090`、Grafana `3000` 只允许内网、堡垒机或 VPN 访问。
- 后端服务是自定义 TCP 协议，不暴露 HTTP `/health` 或 `/metrics`。
- Redis 不应暴露到公网。

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
cd BoostAsioDemo
docker compose up -d --build
docker compose ps
curl -fsS http://localhost:9080/health
curl -fsS http://localhost:9080/metrics
```

业务冒烟：

```bash
cmake --preset default
cmake --build --preset default --target v2_gateway_demo sdk_full_flow_client
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default
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
cmake --preset default
cmake --build --preset default --parallel
ctest --test-dir build/default --output-on-failure
cmake --install build/default --prefix /usr/local

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

当前 Prometheus 只 scrape gateway HTTP `/metrics`：

```bash
docker compose up -d prometheus grafana
curl -fsS http://localhost:9090/-/ready
python3 scripts/check_monitoring_operability.py --summary-path runtime/validation/monitoring-operability-summary.json
```

Grafana 导入：

- `env/monitoring/grafana-dashboard.json`

Prometheus 告警：

- `env/monitoring/prometheus-alerts.yml`

运维排障：

- `docs/production-operations-runbook.md`

当前限制：

- 后端服务没有 HTTP `/metrics`，不得配置 Prometheus 直接 scrape 后端 TCP 端口。
- gateway `/health` 不是业务 ready，生产发布后必须叠加 SDK full-flow、P6 production evidence 或固定 runner 证据。
- P99 告警需要 latency histogram/summary 或外部性能采集；当前默认告警不伪造不存在的 P99 指标。
- RSS/fd 告警需要 process exporter 或等价 agent；默认 Compose 只内置 gateway `/metrics` scrape。

## Redis 备份与恢复

Docker Compose 使用 `redis-data` volume；Kubernetes 使用 `redis-data` PVC。生产至少需要：

- 发布前确认 Redis 持久化策略。
- 发布前记录 Redis volume/PVC 名称。
- 发布前执行 `redis-cli ping`。
- 回滚或迁移前备份 Redis 数据目录或 PVC 快照。

Redis 不可达时 leaderboard backend 会降级到内存路径；该降级适合可用性保护，不等价于持久化保证。

## 发布前检查清单

```bash
python3 -m py_compile scripts/deploy_k8s.py scripts/check_deploy_operability.py
python3 scripts/check_deploy_operability.py --build-dir build/default --summary-path runtime/validation/p0-deploy-operability-summary.json
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
