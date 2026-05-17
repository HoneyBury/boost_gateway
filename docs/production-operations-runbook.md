# 生产运维 Runbook

更新时间：2026-05-17

本文档用于 P3 监控、告警与运维流程收束。当前生产监控只 scrape gateway `/metrics`；后端服务是自定义 TCP 协议，不暴露 HTTP `/metrics`。因此后端异常通过 gateway backend RED counters、Docker/systemd/Kubernetes 健康检查和业务 full-flow 共同判断。

## 入口

常用入口：

```bash
curl -fsS http://127.0.0.1:9080/health
curl -fsS http://127.0.0.1:9080/ready
curl -fsS http://127.0.0.1:9080/metrics
curl -fsS http://127.0.0.1:9080/metrics/diagnostics/json
curl -fsS http://127.0.0.1:9090/-/ready
```

关键产物：

- Prometheus 配置：`env/monitoring/prometheus.yml`
- Prometheus 告警：`env/monitoring/prometheus-alerts.yml`
- Grafana 面板：`env/monitoring/grafana-dashboard.json`
- 监控静态 gate：`scripts/check_monitoring_operability.py`

## 告警分级

| 告警 | 严重级别 | 首要动作 |
| --- | --- | --- |
| `BoostGatewayScrapeDown` | critical | 先确认 gateway 进程、9080 管理口和 Prometheus target |
| `BoostGatewayBackendTimeouts` | critical | 检查 backend down、网络、CPU/RSS/fd 和 gateway diagnostics |
| `BoostGatewayBackendErrors` | warning | 查看具体 backend counter、业务错误和后端日志 |
| `BoostGatewayLeaderboardBackendErrors` | warning | 优先检查 Redis down 或 leaderboard backend 降级 |
| `BoostGatewayRateLimitSpike` | warning | 检查客户端流量、单 IP、login 专项限流和攻击模式 |
| `BoostGatewayHighActiveSessions` | warning | 对照容量基线，确认连接 spike 是否符合预期 |
| `BoostGatewayHighRSS` | warning | 需要 process exporter；检查 RSS 增长和内存泄漏迹象 |
| `BoostGatewayHighFileDescriptors` | critical | 需要 process exporter；检查 fd limit、连接泄漏和慢客户端 |

## backend down

症状：

- `BoostGatewayBackendTimeouts` 或 `BoostGatewayBackendErrors` 触发。
- `/metrics` 中 `gateway_backend_<service>_timeouts_total` 或 `gateway_backend_<service>_errors_total` 增长。
- SDK full-flow 在对应业务步骤失败。

Docker Compose 排查：

```bash
docker compose -f env/docker/docker-compose.yml ps
docker compose -f env/docker/docker-compose.yml logs --tail=200 gateway
docker compose -f env/docker/docker-compose.yml logs --tail=200 <backend-service>
docker compose -f env/docker/docker-compose.yml exec <backend-service> nc -z 127.0.0.1 <port>
```

Kubernetes 排查：

```bash
kubectl -n boost-gateway get pods
kubectl -n boost-gateway describe pod -l app=<backend-service>
kubectl -n boost-gateway logs deploy/<backend-service> --tail=200
kubectl -n boost-gateway rollout status deploy/<backend-service>
```

恢复：

1. 如果进程退出，重启对应 backend。
2. 如果 readiness/TCP probe 失败，先检查端口、配置和最近发布版本。
3. 如果只有 gateway counter 增长但 backend 健康，检查网络、CPU 饱和、连接池和超时配置。
4. 恢复后运行 SDK full-flow 或 P6 production evidence 的有界入口。

## Redis down

症状：

- `BoostGatewayLeaderboardBackendErrors` 触发。
- leaderboard backend 日志出现 Redis 连接失败或降级。
- 排行榜相关业务返回空、降级结果或 backend error。

排查：

```bash
docker compose -f env/docker/docker-compose.yml exec redis redis-cli ping
docker compose -f env/docker/docker-compose.yml logs --tail=200 redis
docker compose -f env/docker/docker-compose.yml logs --tail=200 leaderboard-backend
```

Kubernetes：

```bash
kubectl -n boost-gateway get pod,pvc,svc redis
kubectl -n boost-gateway logs deploy/leaderboard-backend --tail=200
kubectl -n boost-gateway exec deploy/redis -- redis-cli ping
```

恢复：

1. 先恢复 Redis 服务和数据卷/PVC。
2. 再重启 leaderboard backend，确认 Redis 连接重新建立。
3. 检查 `gateway_backend_leaderboard_errors_total` 和 `gateway_backend_leaderboard_timeouts_total` 不再增长。
4. 如果 Redis 数据有损坏风险，按 `docs/production-deployment-runbook.md` 的备份/恢复流程处理。

## gateway error rate

症状：

- `BoostGatewayBackendErrors`、`BoostGatewayBackendTimeouts` 或 `BoostGatewayRateLimitSpike` 触发。
- `/metrics/diagnostics/json` 中 backend counters 增长。
- 客户端出现超时、业务错误或被限流。

排查：

```bash
curl -fsS http://127.0.0.1:9080/metrics | grep -E 'gateway_backend_|gateway_packets_blocked'
curl -fsS http://127.0.0.1:9080/metrics/diagnostics/json
docker compose -f env/docker/docker-compose.yml logs --tail=300 gateway
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default
```

判断：

- backend timeout 增长：优先按 backend down 流程排查。
- blocked packets 增长：检查 `V2_RATE_LIMIT_*` 配置、客户端重试风暴和异常 IP。
- errors 增长但 timeout 不增长：检查协议、业务错误码、trace_id 和最近发布。

## connection spike

症状：

- `BoostGatewayHighActiveSessions` 触发。
- `gateway_active_sessions` 快速升高。
- fd/RSS 告警同步触发时风险更高。

排查：

```bash
curl -fsS http://127.0.0.1:9080/metrics | grep gateway_active_sessions
docker compose -f env/docker/docker-compose.yml logs --tail=200 gateway
lsof -nP -iTCP:9201 -sTCP:ESTABLISHED | wc -l
```

恢复：

1. 确认是否为预期压测或流量峰值。
2. 如果不是预期流量，调整边缘防火墙、上游限流或 `V2_RATE_LIMIT_*`。
3. 如果接近容量基线，准备水平扩容或回滚到低风险配置。

## 发布失败

症状：

- 部署后 gateway scrape down、SDK full-flow 失败或 backend counters 异常增长。
- Kubernetes rollout 无法完成。

排查：

```bash
python3 scripts/check_deploy_operability.py --summary-path runtime/validation/deploy-operability-summary.json
python3 scripts/check_monitoring_operability.py --summary-path runtime/validation/monitoring-operability-summary.json
python3 scripts/check_reliability_matrix.py
```

Kubernetes：

```bash
kubectl -n boost-gateway rollout status deploy/gateway
kubectl -n boost-gateway get events --sort-by=.lastTimestamp
kubectl -n boost-gateway logs deploy/gateway --tail=300
```

## rollback

Docker Compose：

```bash
docker compose -f env/docker/docker-compose.yml pull
docker compose -f env/docker/docker-compose.yml up -d --no-build
docker compose -f env/docker/docker-compose.yml logs --tail=200 gateway
```

Kubernetes：

```bash
kubectl -n boost-gateway rollout undo deploy/gateway
kubectl -n boost-gateway rollout status deploy/gateway
kubectl -n boost-gateway logs deploy/gateway --tail=200
```

回滚后必须验证：

```bash
curl -fsS http://127.0.0.1:9080/health
curl -fsS http://127.0.0.1:9080/metrics
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default
```

## logs

Docker Compose：

```bash
docker compose -f env/docker/docker-compose.yml logs --tail=300 gateway
docker compose -f env/docker/docker-compose.yml logs --tail=300 login-backend
docker compose -f env/docker/docker-compose.yml logs --tail=300 room-backend
docker compose -f env/docker/docker-compose.yml logs --tail=300 battle-backend
docker compose -f env/docker/docker-compose.yml logs --tail=300 matchmaking-backend
docker compose -f env/docker/docker-compose.yml logs --tail=300 leaderboard-backend
docker compose -f env/docker/docker-compose.yml logs --tail=300 redis
```

systemd：

```bash
journalctl -u boost-gateway --since "30 min ago"
journalctl -u boost-login-backend --since "30 min ago"
journalctl -u boost-room-backend --since "30 min ago"
journalctl -u boost-battle-backend --since "30 min ago"
journalctl -u boost-match-backend --since "30 min ago"
journalctl -u boost-leaderboard-backend --since "30 min ago"
```

Kubernetes：

```bash
kubectl -n boost-gateway logs deploy/gateway --tail=300
kubectl -n boost-gateway logs deploy/login-backend --tail=300
kubectl -n boost-gateway logs deploy/room-backend --tail=300
kubectl -n boost-gateway logs deploy/battle-backend --tail=300
kubectl -n boost-gateway logs deploy/matchmaking-backend --tail=300
kubectl -n boost-gateway logs deploy/leaderboard-backend --tail=300
```

日志采集要求：

- 保留 gateway、五个 backend、Redis、Prometheus 和 Grafana 的发布窗口日志。
- 记录 git commit、镜像 tag、部署方式、触发告警、恢复动作和最终验证 summary。
- 业务错误需要同时记录 trace_id、user_id、message_id、request_id 和 backend service。

## 当前边界

- P99 告警暂不在默认 Prometheus rules 中启用，因为当前 `/metrics` 没有 route latency histogram/summary；只能通过 `/metrics/diagnostics/json` 的 backend latency 累计值和性能基线脚本离线判断。
- RSS/fd 告警依赖 process exporter 或等价 agent；默认 Compose 只 scrape gateway `/metrics`，因此这些规则在未接入 exporter 时不会产生序列。
- Redis down 目前通过 leaderboard backend 的 gateway RED counters 和日志判断；Redis exporter 可作为后续增强，但不是当前 P3 的默认依赖。
