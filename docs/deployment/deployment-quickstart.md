# 服务端部署快速说明

本文档用于快速启动 BoostGateway 服务端，并给 Qt 坦克客户端提供可连接的
gateway 地址。当前服务端主链包含：

- `gateway`
- `login backend`
- `room backend`
- `battle backend`
- `matchmaking backend`
- `leaderboard backend`
- Redis 与监控栈

客户端只需要连接 gateway TCP 入口，默认是 `127.0.0.1:9201`。

## 方式一：OrbStack / Docker Compose

适合本机演示、客户端联调和接近生产拓扑的验证。macOS 上使用 OrbStack 时，
命令与 Docker Compose 一致。

```bash
cd /Users/honeybury/workspace/boost_gateway

python3 scripts/tools/prepare_docker_runtime_context.py --build-dir build/release
docker compose -f env/docker/docker-compose.yml build
docker compose -f env/docker/docker-compose.yml up -d
```

常用检查：

```bash
docker compose -f env/docker/docker-compose.yml ps
curl http://127.0.0.1:9080/health
curl http://127.0.0.1:9080/ready
curl http://127.0.0.1:9080/metrics
```

端口：

| 用途 | 地址 |
|------|------|
| 客户端 TCP gateway | `127.0.0.1:9201` |
| Gateway HTTP 管理口 | `http://127.0.0.1:9080` |
| Prometheus | `http://127.0.0.1:9090` |
| Grafana | `http://127.0.0.1:3000` |

停止：

```bash
docker compose -f env/docker/docker-compose.yml down
```

清理数据卷：

```bash
docker compose -f env/docker/docker-compose.yml down -v
```

## 方式二：本机二进制开发联调

适合修改服务端代码、客户端 live gate 和快速定位问题。

```bash
cd /Users/honeybury/workspace/boost_gateway

cmake -S . -B build/release -G Ninja -DBOOST_DEPENDENCY_PROVIDER=conan \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_TOOLCHAIN_FILE=build/conan-release/build/Release/generators/conan_toolchain.cmake
cmake --build build/release --target \
  boost_gateway_sdk \
  v2_gateway_demo \
  v2_login_backend \
  v2_room_backend \
  v2_battle_backend \
  v2_match_backend \
  v2_leaderboard_backend
```

最省心的启动方式是从客户端仓库运行 live gate，让脚本自动分配动态端口并启动
全部后端：

```bash
cd /Users/honeybury/workspace/BoostGatewayTankClient

./scripts/run-live-gate.sh \
  --server-root /Users/honeybury/workspace/boost_gateway \
  --server-build-dir /Users/honeybury/workspace/boost_gateway/build
```

如果要手动启动固定端口，请确保 gateway 指向五个 backend：

```bash
build/examples/v2_login_backend/v2_login_backend 9202
build/examples/v2_room_backend/v2_room_backend 9302
build/examples/v2_battle_backend/v2_battle_backend 9303
build/examples/v2_match_backend/v2_match_backend 9304
build/examples/v2_leaderboard_backend/v2_leaderboard_backend 9305

build/examples/v2_gateway_demo/v2_gateway_demo \
  --port 9201 \
  --http-port 9080 \
  --login-port 9202 \
  --room-port 9302 \
  --battle-port 9303 \
  --matchmaking-port 9304 \
  --leaderboard-port 9305
```

然后客户端使用：

```bash
BGTC_GATEWAY_HOST=127.0.0.1 \
BGTC_GATEWAY_PORT=9201 \
/Users/honeybury/workspace/BoostGatewayTankClient/build/local/boost_gateway_tank_client
```

## 部署后验证

最低验证：

```bash
curl http://127.0.0.1:9080/health
curl http://127.0.0.1:9080/ready
```

业务闭环验证：

```bash
cd /Users/honeybury/workspace/BoostGatewayTankClient
BGTC_GATEWAY_HOST=127.0.0.1 BGTC_GATEWAY_PORT=9201 ./scripts/run-headless-gate.sh
```

如果希望脚本自动启动服务端和客户端 headless gate：

```bash
./scripts/run-live-gate.sh \
  --server-root /Users/honeybury/workspace/boost_gateway \
  --server-build-dir /Users/honeybury/workspace/boost_gateway/build
```

当前 headless/live gate 覆盖：

- 注册 / 登录
- 创建房间 / 加入房间 / 离开房间
- 房间列表 / 房间详情
- 踢人 / 转让房主
- 准备 / 开始战斗
- 战斗输入 / authoritative snapshot
- 道具生成 / 拾取 / buff
- 断线重连后 battle state 恢复
- 结算 / 排行榜
- 回放加载

## 客户端连接说明

Qt 客户端只需要知道 gateway 地址：

```text
host = 127.0.0.1
port = 9201
```

登录窗口可以直接注册新账号。当前开发链路使用 `token:<user_id>` 形式作为凭证。
真实生产认证策略由 login backend 和部署配置治理。

## 常见问题

- 浏览器访问 `http://127.0.0.1:9201` 没有意义：`9201` 是 TCP 游戏协议，不是 HTTP。
- Gateway `/health` 只代表管理面存活；业务可用性请以 SDK/headless/live gate 为准。
- Docker Compose 中 gateway 使用服务名访问后端；本机二进制运行时需要显式传入 backend 端口。
- `env/` 是当前生产配置事实源，旧的根目录 Docker/K8s/monitoring 路径只作为历史参考。
