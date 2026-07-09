# 生产运维 Runbook

更新时间：2026-05-18（P0-P4）

本文档用于 P3 监控、告警与运维流程收束。当前生产监控只 scrape gateway `/metrics`；后端服务是自定义 TCP 协议，不暴露 HTTP `/metrics`。因此后端异常通过 gateway backend RED counters、Docker/systemd/Kubernetes 健康检查和业务 full-flow 共同判断。

## N2 SLI / SLO 口径

当前监控面采用“gateway 可 scrape 指标 + diagnostics JSON + 业务 full-flow 脚本”三层口径：

| SLI | 当前来源 | 说明 |
| --- | --- | --- |
| gateway 可用性 | `up{job="gateway"}`、`/ready` | 最基础的 scrape 与 readiness |
| backend 错误率 | `gateway_backend_*_errors_total`、`gateway_backend_*_timeouts_total` | 当前后端 RED 指标事实源 |
| backend route latency | `gateway_backend_*_p99_latency_us`、`gateway_backend_route_latency_us_bucket`、`gateway_backend_*_avg_latency_us` | P99 gauge 和 histogram bucket 是告警主口径，avg 保留作趋势辅助 |
| 业务闭环成功率 | `gateway_login_success_total`、`gateway_room_join_success_total`、`gateway_battle_start_success_total` | 覆盖 login/room/battle 核心链路 |
| Redis 相关异常 | `gateway_backend_leaderboard_*` + `redis_exporter` | 目前通过 leaderboard 路径代理 Redis 异常 |
| 资源风险 | `process_resident_memory_bytes`、`process_open_fds`、`container_memory_working_set_bytes` | 依赖 optional process exporter / cAdvisor |

当前 SLO 约束：

- `BoostGatewayScrapeDown`：gateway scrape 中断
- `BoostGatewayBackendErrors` / `BoostGatewayBackendTimeouts`：后端路由失败
- `BoostGatewayHighRouteLatency`：backend route P99 latency 超 200ms
- `BoostGatewayBusinessFlowFailure`：login / room / battle 成功计数 10 分钟不推进
- `BoostGatewayHighActiveSessions`：活跃连接逼近当前容量线

N2 起，gateway `/metrics` 默认导出 `gateway_backend_route_latency_us_bucket/_sum/_count` 和 `gateway_backend_<service>_p50/p90/p99_latency_us`。Prometheus 告警使用 P99 gauge；Grafana 同时展示 P99 gauge 和 `histogram_quantile()` 趋势。性能基线脚本仍是容量和长稳结论的事实源。

## 入口

当前 OrbStack / Docker Compose 本机部署暴露三类运维入口：

| 地址 | 类型 | 用途 | 正常现象 |
| --- | --- | --- | --- |
| `http://127.0.0.1:9080` | Gateway 只读管理 API | 给 curl、Prometheus、脚本读取健康与指标 | 访问根路径 `/` 返回 `Not Found` 是正常的；必须访问具体 path |
| `http://127.0.0.1:9090` | Prometheus Web UI | 查看 scrape target、PromQL、告警规则 | 打开后使用顶部菜单 `Status`、`Alerts`、`Graph` |
| `http://127.0.0.1:9093` | Alertmanager Web UI | 查看告警路由、silence、通知链 | 默认 receiver 为占位配置；生产需替换 |
| `http://127.0.0.1:3000` | Grafana Web UI | 查看仪表盘 | 默认账号来自 Compose：`admin` / `admin` |
| `127.0.0.1:9201` | Gateway TCP 业务入口 | SDK / 客户端连接，不是浏览器页面 | 浏览器访问无意义；用 SDK full-flow 或业务客户端验证 |

### Gateway 管理 API（9080）

`9080` 不是网页控制台。`src/net/http_manager.cpp` 只注册了以下 GET path，访问 `http://127.0.0.1:9080/` 返回 `Not Found` 符合预期：

```bash
curl -fsS http://127.0.0.1:9080/health
curl -fsS http://127.0.0.1:9080/ready
curl -fsS http://127.0.0.1:9080/metrics
curl -fsS http://127.0.0.1:9080/metrics/diagnostics/json
```

端点含义：

| Path | 格式 | 用途 |
| --- | --- | --- |
| `/health` | JSON | liveness / 基础健康信息。当前 Compose 静态路由模式下可能返回 `warn`，不等价于业务不可用 |
| `/ready` | JSON | readiness / 是否可服务 |
| `/metrics` | Prometheus text | 给 Prometheus scrape，也可人工 grep |
| `/metrics/json` | JSON | 机器读取的指标快照 |
| `/metrics/diagnostics` | text | 人工排查摘要 |
| `/metrics/diagnostics/json` | JSON | 推荐排查入口，包含 active sessions、accepted sessions、backend metrics 等 |

常用查询：

```bash
# 查看活跃连接、累计接入、后端 RED 指标
curl -fsS http://127.0.0.1:9080/metrics/diagnostics/json

# 查看 Prometheus 文本指标
curl -fsS http://127.0.0.1:9080/metrics

# 只看 gateway session 指标
curl -fsS http://127.0.0.1:9080/metrics | grep -E 'gateway_active_sessions|gateway_accepted_sessions_total|gateway_outbound_dispatches_total'

# 只看后端请求、错误、超时
curl -fsS http://127.0.0.1:9080/metrics | grep -E 'gateway_backend_.*_(requests|successes|errors|timeouts)_total'
```

注意：Compose 部署中 gateway 通过 `CONFIG_PATH=/app/config/environments/docker/gateway.json` 读取 Docker 环境的静态服务名路由。`/health` 里的 service registry 动态注册项为空时可能显示 `warn`，因此上线验收必须叠加 SDK full-flow。

### Prometheus（9090）

`9090` 是 Prometheus 自带的 Web UI，不是项目业务页面。它的作用是抓取 `gateway:9080/metrics`，保存时间序列，并提供查询和告警状态。

常用页面：

| 页面 | URL | 用途 |
| --- | --- | --- |
| Ready | `http://127.0.0.1:9090/-/ready` | Prometheus 自身是否 ready |
| Targets | `http://127.0.0.1:9090/targets` | 查看 `gateway`、`prometheus` scrape 是否 `UP` |
| Alerts | `http://127.0.0.1:9090/alerts` | 查看当前告警是否 firing |
| Graph | `http://127.0.0.1:9090/graph` | 执行 PromQL 查询 |
| Rules | `http://127.0.0.1:9090/rules` | 查看加载的告警规则 |

推荐 PromQL：

```promql
# Gateway 是否被 Prometheus 抓到，1 表示正常
up{job="gateway"}

# 当前活跃连接数
gateway_active_sessions

# 最近 5 分钟新连接速率
rate(gateway_accepted_sessions_total[5m])

# 后端请求速率
sum by (__name__) (rate({__name__=~"gateway_backend_.*_requests_total"}[5m]))

# 后端错误速率
sum by (__name__) (rate({__name__=~"gateway_backend_.*_errors_total"}[5m]))

# 后端超时速率
sum by (__name__) (rate({__name__=~"gateway_backend_.*_timeouts_total"}[5m]))

# leaderboard / Redis 相关错误
rate(gateway_backend_leaderboard_errors_total[5m]) + rate(gateway_backend_leaderboard_timeouts_total[5m])
```

当前 Prometheus 配置只 scrape gateway 和 Prometheus 自身。后端服务是 TCP 协议，不直接暴露 HTTP `/metrics`；后端健康通过 Docker healthcheck、gateway backend counters、日志和 SDK full-flow 共同判断。

Battle settlement 会由 gateway 自动提交到 leaderboard backend。一次战斗结束后，可以用 `gateway_backend_leaderboard_requests_total` 确认 settlement submit 与后续查询是否进入后端；如果 `gateway_backend_leaderboard_errors_total` 或 `gateway_backend_leaderboard_timeouts_total` 增长，优先检查 leaderboard backend、Redis 可用性和 gateway 日志中的 `leaderboard settlement submit failed`。

### Grafana（3000）

`3000` 是 Grafana 仪表盘。首次访问需要登录，Docker Compose 默认配置为：

```text
username: admin
password: 由 `GRAFANA_ADMIN_PASSWORD` 决定；当前默认值是 `boost-gateway-change-me`
```

登录后查看：

1. 左侧 `Dashboards`。
2. 进入文件夹 `Boost Gateway`。
3. 打开 `Boost Gateway Production`。

Grafana 已通过以下文件自动配置 Prometheus 数据源和 dashboard：

- `env/monitoring/grafana-datasource.yml`
- `env/monitoring/grafana-dashboard-provider.yml`
- `env/monitoring/grafana-dashboard.json`

主要面板含义：

| 面板 | 看什么 | 异常判断 |
| --- | --- | --- |
| `Gateway Up` | Prometheus 是否能抓到 gateway | `0` 表示 scrape down |
| `Active Sessions` | 当前连接数 | 突增要结合压测/攻击/客户端重连判断 |
| `Accepted Sessions` | 接入速率 | 长时间为 0 但有业务预期时排查入口 |
| `Backend Requests` | 后端请求与成功速率 | 某服务请求异常变化要看业务流 |
| `Backend Errors / Timeouts` | 后端错误和超时 | 非 0 持续增长要排查对应 backend |
| `Backend Avg Route Latency` | 各 backend 平均路由延迟 | 超过 200ms 持续 5 分钟要排查慢后端、网络或 Redis 依赖 |
| `Business Flow Success` | login / room join / battle start 成功速率 | 某项长期为 0 说明业务闭环断了，不只是 scrape 还活着 |
| `Leaderboard / Redis Dependent Errors` | 排行榜/Redis 相关失败 | 优先查 Redis 和 leaderboard backend |
| `Rate Limit / Blocked Packets` | 限流/丢弃 | 突增可能是重试风暴或异常客户端 |

生产环境不要继续使用默认密码，应改为强密码并限制 `3000`、`9090`、`9093`、`9080`、`9121`、`6380` 只允许内网、VPN 或堡垒机访问。

### Alertmanager（9093）

`9093` 是 Alertmanager，用来接收 Prometheus 告警并转发到通知通道。当前 Compose 已经把 Prometheus 指向 `alertmanager:9093`，但仓库内置的 receiver 仍是无副作用占位配置，目的是让本地和 CI 栈可以稳定启动。

常用页面：

| 页面 | URL | 用途 |
| --- | --- | --- |
| Ready | `http://127.0.0.1:9093/-/ready` | Alertmanager 是否 ready |
| Status | `http://127.0.0.1:9093/#/status` | 查看配置和集群状态 |
| Alerts | `http://127.0.0.1:9093/#/alerts` | 查看收到的告警 |
| Silences | `http://127.0.0.1:9093/#/silences` | 创建维护窗口静默 |

生产需要改的地方：

- 用 email / webhook / Slack / PagerDuty 替换 `env/monitoring/alertmanager.yml` 里的占位 receiver。
- 保证 `9093` 只在内网开放。
- 在发布记录里写清接收渠道、值班组和静默流程。

### 业务入口（9201）

`9201` 是 TCP 协议入口，浏览器无法直接使用。验证真实业务闭环用 SDK 示例：

```bash
build/default/sdk/examples/sdk_full_flow_client 127.0.0.1 9201
```

如果希望由脚本自动启动 gateway 与 login / room / battle / matchmaking / leaderboard 五个真实后端，并校验 `/metrics/diagnostics/json` 中的 backend request counters，使用：

```bash
python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default
```

成功输出应包含：

```text
=== ALL TESTS PASSED ===
```

关键产物：

- Prometheus 配置：`env/monitoring/prometheus.yml`
- Prometheus 告警：`env/monitoring/prometheus-alerts.yml`
- Grafana 面板：`env/monitoring/grafana-dashboard.json`
- Grafana 自动配置：`env/monitoring/grafana-datasource.yml`、`env/monitoring/grafana-dashboard-provider.yml`
- 监控静态 gate：`scripts/check_monitoring_operability.py`

### 生产性能快照

当本机 OrbStack / Docker Compose 生产栈已经启动后，用下面的有界采样命令确认“部署健康、观测链路可用、空载资源没有明显异常”：

```bash
python3 scripts/collect_docker_production_perf_snapshot.py
```

输出：

- `runtime/perf/docker-production-snapshot/summary.json`
- `runtime/perf/docker-production-snapshot/report.md`

脚本会从容器内读取 gateway `/ready` 与 `/metrics/diagnostics/json`，检查 Prometheus targets、Grafana health，并记录 `docker stats --no-stream` 资源快照。报告里的 `Business Backend Metrics` 会列出 login、room、battle、matchmaking、leaderboard 的 requests/successes/errors/timeouts/avg latency，适合判断 match/leaderboard/settlement 是否进入真实生产链路。它需要能访问 Docker API 的本机或固定 runner 权限；如果运行环境无法连接 Docker socket，应在有 Docker 权限的终端或已授权的自动化环境中执行。

该快照只回答当前生产栈的运行态健康和空载资源问题，不替代 2h/8h soak、5K/10K capacity 和 battle-500 容量专项。

如果要把 P3 业务闭环纳入性能 evidence，使用：

```bash
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset smoke \
  --include-business-flow
```

该命令会启动五后端拓扑，运行 echo/battle smoke，并额外跑 SDK full-flow，覆盖 match_join/status、battle settlement 自动写 leaderboard、manual submit、top/rank 和 reconnect。

### P5-P8 聚合验证

剩余高级 profile 的统一验证入口：

```bash
python3 scripts/verify_p5_p8_business_closure.py \
  --build-dir build/default \
  --skip-build
```

可选增强：

```bash
# OTel fake collector + runtime HTTP
python3 scripts/verify_p5_p8_business_closure.py \
  --build-dir build/default \
  --skip-build \
  --include-otel-collector \
  --include-runtime-http

# 已部署 K8s / kind 环境
python3 scripts/verify_p5_p8_business_closure.py \
  --build-dir build/default \
  --skip-build \
  --include-operator-kind \
  --include-k8s-full-flow
```

子产物：

- `runtime/validation/p5-observability-summary.json`
- `runtime/validation/p6-tls-profile-summary.json`
- `runtime/validation/p7-control-plane-summary.json`
- `runtime/validation/p7-k8s-full-flow-summary.json`
- `runtime/validation/p5-p8-business-closure-summary.json`

P5-P8 的详细边界以当前主线文档和归档计划文档为准；历史补充材料已归档到 `docs/archive/`，其中与观测、K8s 业务流和 proto/gRPC 取舍相关的旧文档位于 `docs/archive/runbooks/` 和 `docs/archive/process/`。

## 告警分级

| 告警 | 严重级别 | 首要动作 |
| --- | --- | --- |
| `BoostGatewayScrapeDown` | critical | 先确认 gateway 进程、9080 管理口和 Prometheus target |
| `BoostGatewayBackendTimeouts` | critical | 检查 backend down、网络、CPU/RSS/fd 和 gateway diagnostics |
| `BoostGatewayBackendErrors` | warning | 查看具体 backend counter、业务错误和后端日志 |
| `BoostGatewayHighRouteLatency` | warning | 查看 `Backend Avg Route Latency`、`/metrics/diagnostics/json`、对应 backend 日志和 Redis 依赖 |
| `BoostGatewayBusinessFlowFailure` | warning | 优先跑 SDK full-flow，确认 login/room/battle 是否还在推进 |
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
4. 如果 Redis 数据有损坏风险，按 `docs/deployment/production-deployment-runbook.md` 的备份/恢复流程处理。
5. 恢复后运行 `python3 scripts/verify_sdk_full_flow_client.py --build-dir build/default`，确认 `battle finish -> leaderboard settlement -> top/rank` 仍可查询。

Redis / Raft HA 细节见 `docs/redis-raft-ha-runbook.md`。默认生产配置不启用 Raft；matchmaking/leaderboard 三节点样例位于 `config/environments/ha/`，只在 `raft-ha` profile 或正式 HA 部署中显式启用。

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

## N3 部署恢复、回滚与灾备演练

N3 运维演练默认先跑静态门禁，确认恢复/回滚资产没有漂移：

```bash
python3 scripts/check_production_recovery_gate.py --summary-path runtime/validation/production-recovery-summary.json
```

固定 runner 或真实环境演练建议顺序：

1. 运行 `scripts/check_deploy_operability.py` 和 `scripts/check_production_recovery_gate.py`。
2. 对 Docker Compose 执行 gateway/backend/Redis restart，或对 Kubernetes 执行 rollout restart。
3. 观察 Prometheus targets、gateway diagnostics 和 backend errors/timeouts。
4. 执行 SDK full-flow，确认 login、room、battle、leaderboard 业务闭环恢复。
5. 如涉及 Redis，记录备份来源、恢复动作、RPO 风险和 leaderboard 查询结果。
6. 如涉及回滚，记录旧/新镜像 tag、回滚命令、rollout 状态和最终验证 summary。
7. 复制 `docs/production/production-recovery-drill-record-template.json` 作为本次演练记录，将 `template` 改为 `false` 后归档。
8. 运行记录校验：

```bash
python3 scripts/check_recovery_drill_record.py --record runtime/validation/<drill-record>.json --summary-path runtime/validation/recovery-drill-record-check-summary.json
```

恢复演练记录至少包含：

- git commit、镜像 tag、部署方式和目标环境。
- 故障注入方式：gateway restart、后端重启、Redis 恢复、镜像回滚、网络抖动或配置变更。
- 触发告警、异常指标、日志片段和恢复动作。
- RTO、RPO、是否有数据一致性风险。
- 最终验证 summary：`production-recovery-summary.json`、SDK full-flow、Docker snapshot 或 K8s full-flow。
- `scripts/check_recovery_drill_record.py` 通过后的 `recovery-drill-record-check-summary.json`。

## 当前边界

- P99 route latency 告警已默认启用，口径为 gateway `/metrics` 导出的 `gateway_backend_*_p99_latency_us` 和 `gateway_backend_route_latency_us_bucket/_sum/_count`；容量、长稳和回归判断仍以固定 runner 性能报告为事实源。
- RSS/fd 告警依赖 process exporter 或等价 agent；默认 Compose 只 scrape gateway `/metrics`，因此这些规则在未接入 exporter 时不会产生序列。
- Redis down 目前通过 leaderboard backend 的 gateway RED counters、日志和业务闭环恢复验证判断；Redis exporter 可作为后续增强，但不是当前默认依赖。
