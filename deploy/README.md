# BoostGateway v3.5.0 部署手册

## 1. 拓扑

当前部署面覆盖 6 个核心进程、Redis、Prometheus、Grafana：

```
client ──TCP 9201──▶ gateway
                       ├─TCP 9202──▶ login-backend
                       ├─TCP 9302──▶ room-backend
                       ├─TCP 9303──▶ battle-backend
                       ├─TCP 9304──▶ matchmaking-backend
                       └─TCP 9305──▶ leaderboard-backend

gateway ──HTTP 9080──▶ /health /metrics /metrics/json /metrics/diagnostics
```

| 服务 | 端口 | 二进制 | 运维语义 |
|---|---:|---|---|
| gateway | 9201, 9080 | `v2_gateway_demo` | 客户端入口；仅 gateway 暴露 HTTP 管理面 |
| login-backend | 9202 | `v2_login_backend` | 登录鉴权、会话管理 |
| room-backend | 9302 | `v2_room_backend` | 房间生命周期 |
| battle-backend | 9303 | `v2_battle_backend` | 战斗输入、tick、归档 |
| matchmaking-backend | 9304 | `v2_match_backend` | MMR 匹配 |
| leaderboard-backend | 9305 | `v2_leaderboard_backend` | 排行榜；可接 Redis |

后端服务当前是自定义 TCP 协议，不暴露 HTTP `/health`。容器健康检查使用 TCP 端口探测；gateway 的 `/health` 是管理面 liveness stub，不等价于业务 ready。

## 2. 构建安装

```bash
bash third_party/download_deps.sh
cmake --preset release
cmake --build --preset release --parallel
ctest --preset release
cmake --install build/release --prefix /usr/local
```

安装后主要产物：

```
/usr/local/bin/
  v2_gateway_demo
  v2_login_backend
  v2_room_backend
  v2_battle_backend
  v2_match_backend
  v2_leaderboard_backend
  v2_gateway_pressure
/usr/local/share/boost_gateway/
  config/
  deploy/systemd/
  docs/
```

## 3. Docker Compose

统一使用 `env/docker/docker-compose.yml` 作为当前 Docker Compose 入口。

```bash
docker compose -f env/docker/docker-compose.yml up -d --build
docker compose -f env/docker/docker-compose.yml ps
curl -fsS http://localhost:9080/health
docker compose -f env/docker/docker-compose.yml logs -f gateway
docker compose -f env/docker/docker-compose.yml down
```

Compose 会显式把 gateway 后端地址设置为服务名，例如 `login-backend:9202`。不要在容器内使用默认 `127.0.0.1` 连接其他服务。
生产建议仅对公网开放 `9201`；`9080`、`9090`、`9093`、`3000`、`6380` 和 `9121` 应维持 localhost / 内网绑定。

后端健康检查示例：

```bash
docker compose exec login-backend nc -z 127.0.0.1 9202
docker compose exec room-backend nc -z 127.0.0.1 9302
docker compose exec battle-backend nc -z 127.0.0.1 9303
```

## 4. systemd

```bash
cmake --install build/default --prefix /usr/local
useradd -r -s /bin/false -d /var/lib/boost-gateway boost-gateway
mkdir -p /var/lib/boost-gateway/{login,room,battle,match,leaderboard}
mkdir -p /var/log/boost-gateway
chown -R boost-gateway:boost-gateway /var/lib/boost-gateway /var/log/boost-gateway
cp deploy/systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable \
  boost-login-backend \
  boost-room-backend \
  boost-battle-backend \
  boost-match-backend \
  boost-leaderboard-backend \
  boost-gateway
systemctl start \
  boost-login-backend \
  boost-room-backend \
  boost-battle-backend \
  boost-match-backend \
  boost-leaderboard-backend
systemctl start boost-gateway
```

常用操作：

```bash
systemctl status 'boost-*'
journalctl -u boost-gateway -f
systemctl restart boost-room-backend
systemctl stop boost-gateway boost-leaderboard-backend boost-match-backend boost-battle-backend boost-room-backend boost-login-backend
```

`boost-leaderboard-backend.service` 默认尝试连接本机 Redis：`REDIS_HOST=127.0.0.1`、`REDIS_PORT=6379`。Redis 不可用时进程会回退到内存排行榜。

## 5. 监控

- Gateway HTTP 管理面：`:9080/health`、`:9080/metrics`、`:9080/metrics/json`、`:9080/metrics/diagnostics`
- Prometheus 配置：`env/monitoring/prometheus.yml`，当前 scrape gateway HTTP `/metrics`、Prometheus 自身、Redis exporter，以及可选 profile 下的 cAdvisor
- Alertmanager 配置：`env/monitoring/alertmanager.yml`
- Grafana 仪表盘：`env/monitoring/grafana-dashboard.json`
- Redis exporter：默认启用，提供 Redis 运行时指标
- cAdvisor：`host-observability` profile 下可选启用，提供容器 CPU / memory 指标

管理面不应暴露到公网。生产入口建议放在反向代理或负载均衡之后，并在边界层处理 TLS、限流和访问控制。gateway `/health` 当前是 liveness stub，不等价于完整业务 ready；发布后必须叠加 SDK full-flow 或生产证据 gate。Grafana 默认密码必须通过 `GRAFANA_ADMIN_PASSWORD` 覆盖，Alertmanager 默认 receiver 需要替换成真实通知通道。

## 6. 部署预检

提交或发布前运行：

```bash
python3 scripts/check_deploy_operability.py --build-dir build/default
```

脚本会检查 Dockerfile、两套 Compose、systemd unit、CMake 安装清单、Kubernetes 探针、Prometheus scrape 目标、入口参数、非交互运行语义以及可选构建产物，并生成：

```
runtime/validation/deploy-operability-summary.json
```

完整生产部署、回滚、监控和发布后验证流程见 `docs/production-deployment-runbook.md`。
