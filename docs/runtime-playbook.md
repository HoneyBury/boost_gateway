# 当前网关骨架运行说明

## 1. 当前能力概览

当前项目已经具备一个最小可运行的游戏网关骨架，包含以下模块：

- `net::Session`
  负责单连接生命周期、长度头协议拆包、异步收发、心跳检测、超时断开、发包限流、最大包长校验。
- `net::MessageDispatcher`
  负责消息号注册、业务线程池投递和中间层执行。
- `game::gateway::SessionManager`
  负责在线连接和登录态管理。
- `game::room::RoomManager`
  负责房间生命周期、房主、成员和 ready 状态管理。
- `game::battle::BattleManager`
  负责战斗上下文、输入序号和输入历史管理。
- `game::gateway::GatewayMetrics`
  负责会话、包量、字节量、拦截量和业务成功量统计。
- `game::gateway::GatewayMetricsExporter`
  负责将当前指标快照导出为 Prometheus 文本和 JSON 文件。
- `game::gateway::GatewayService`
  负责心跳和登录前白名单拦截。
- `game::login::LoginService`
  负责最小登录闭环和重复登录顶号处理。
- `game::room::RoomService`
  负责最小房间加入闭环。
- `game::battle::BattleService`
  负责同房间双人起战斗的最小闭环。

## 2. 当前协议约定

当前网络层协议格式：

```text
[4字节长度][2字节消息号][4字节请求序号][4字节错误码][消息体]
```

当前关键消息号：

- `1`：心跳请求
- `2`：心跳响应
- `1001`：Echo 请求
- `1002`：Echo 响应
- `2001`：登录请求
- `2002`：登录响应
- `3001`：加入房间请求
- `3002`：加入房间响应
- `4001`：开始战斗请求
- `4002`：开始战斗响应
- `9001`：通用错误响应

## 3. 消息处理链路

当前完整链路如下：

```text
客户端 -> Session 读包 -> MessageDispatcher -> 中间层 -> 业务线程池
       -> Login/Room/Battle Handler -> Session 回包
```

中间层当前包含：

- 登录前白名单
  允许 `HeartbeatRequest`、`LoginRequest`、`EchoRequest`
- 未登录业务拦截
  未登录时访问房间或战斗接口，会返回 `kErrorResponse: auth_required`
- 基础限频
  单连接每秒最多通过 32 条非心跳消息，超限会返回 `kErrorResponse: rate_limited`

## 4. 目录对应关系

- `include/net` / `src/net`
  网络基础设施
- `include/game/gateway` / `src/game/gateway`
  网关层、会话管理、指标
- `include/game/login` / `src/game/login`
  登录业务
- `include/game/room` / `src/game/room`
  房间业务
- `include/game/battle` / `src/game/battle`
  战斗业务
- `examples/echo`
  最小手工联调用例
- `examples/pressure`
  压测客户端

## 5. 运行方式

### 5.1 常规构建（可访问 GitHub）

启动网关示例：

```powershell
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
D:\Program\boost\build\windows-msvc-debug\examples\echo\Debug\echo_server.exe config/gateway.json
```

### 5.2 内网构建（无法访问 GitHub）

先准备第三方依赖（只需一人执行一次，或从公司内部仓库直接下载 `third_party.zip` 解压到项目根目录）：

```powershell
# 在外网机器上：
.\third_party\download_deps.bat
.\third_party\package.bat
# 将生成的 third_party.zip 上传到公司内部仓库

# 在内网开发机器上：
# 1. 下载 third_party.zip 并解压到项目根目录
# 2. 正常构建：
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
```

CMake configure 阶段会输出 `Using local archive: xxx` 表明正在使用本地依赖包。

启动 Echo 客户端：

```powershell
D:\Program\boost\build\windows-msvc-debug\examples\echo\Debug\echo_client.exe 127.0.0.1 9000 hello
```

启动压测客户端：

```powershell
D:\Program\boost\build\windows-msvc-debug\examples\pressure\Debug\gateway_pressure.exe 127.0.0.1 9000 100 10
```

或使用 JSON 场景配置：

```powershell
D:\Program\boost\build\windows-msvc-debug\examples\pressure\Debug\gateway_pressure.exe config/pressure.json
```

含义：

- 第 1 个参数：目标 IP
- 第 2 个参数：目标端口
- 第 3 个参数：客户端连接数
- 第 4 个参数：每个客户端发送的 Echo 数

## 6. 当前测试覆盖

单元测试覆盖：

- 协议编解码
- 消息分发器和中间层
- `SessionManager` 状态管理
- 服务注册

集成测试覆盖：

- Echo 请求响应
- 未登录拦截
- 登录 + 加入房间闭环
- 双人同房间起战斗闭环
- 心跳超时断开

## 7. 当前指标

当前已经可统计：

- 接入连接数
- 关闭连接数
- 收包数 / 发包数
- 收发字节量
- 被中间层拦截的包数
- 登录成功数
- 房间加入成功数
- 战斗启动成功数
- 在线会话数
- 认证会话数
- 活跃房间数
- 活跃战斗数

若在 `config/gateway.json` 里配置了：

- `gateway.metrics_prometheus_path`
- `gateway.metrics_json_path`

服务端会按 `gateway.metrics_log_interval_ms` 周期把指标导出到对应文件。

### HTTP 管理端点

若在 `config/gateway.json` 里配置了 `gateway.http_management_port`（默认 9080），
服务端还会启动 HTTP 管理端点，提供以下接口：

| 端点 | 方法 | Content-Type | 说明 |
|---|---|---|---|
| `/health` | GET | `application/json` | 健康检查，返回 `{"status":"ok"}` |
| `/metrics` | GET | `text/plain` | Prometheus 格式指标，可直接被 Prometheus scrape |
| `/metrics/json` | GET | `application/json` | JSON 格式指标快照 |

验证方式：

```powershell
curl http://127.0.0.1:9080/health
curl http://127.0.0.1:9080/metrics
curl http://127.0.0.1:9080/metrics/json
```

设置为 `0` 则禁用 HTTP 管理端点。

## 8. 已完成能力清单 (P0–P7)

### 网络层
- `Session`：长度头协议、异步收发、心跳超时、发包限流、最大包长校验
- `MessageDispatcher`：消息号注册、业务线程池投递、中间层链
- `SessionManager`：在线连接管理、登录态跟踪、顶号踢线
- 协议格式：`[4B长度][2B msg][4B req][4B err][1B flags][body]`
- 结构化序列化：`net::msg` 18 种消息类型 + binary serializer
- 链路追踪：`trace_id` 贯穿 Session → Dispatcher → Handler → 日志

### 业务层
- `LoginService`：token 校验、登录上下文、重复登录顶号、dev/json_file/http 三种鉴权
- `RoomService`：创建/加入/离开/准备、房主机制、房间广播
- `BattleService`：起战斗、输入路由、输入广播、帧同步 (advance_frame)、战斗结算 (end_battle)
- `PushService`：统一推送、错误响应、广播推送

### 可观测性
- `GatewayMetrics`：10 种累计 counter
- `GatewayMetricsExporter`：Prometheus 文本 + JSON 快照导出，含 6 种 `/sec` rate gauge
- HTTP 管理端点：`GET /health` `GET /metrics` `GET /metrics/json`
- 慢连接检测：写队列 > 50% 上限 WARN
- 崩溃转储：`runtime/crashes/crash_*.txt`

### 工程化
- CMake + FetchContent + 本地 third_party 内网构建
- `gateway_pressure` 压测工具：6 种场景 (echo/invalid_token/slow_echo/broadcast_storm/malicious/battle)
- `Dockerfile` + `docker-compose.yml` + CI Docker 构建
- `BufferPool` + `ObjectPool` 复用分配
- `Session::send_batch()` 批量发包
- `HandlerRegistry` 批量注册
- `ServiceRouter` 内部服务路由
- 连接限制：`max_connections` + `per_ip_connection_limit`
- 线程池拆分：按消息 ID 范围路由到专用池
- `app::crash::install_crash_handler()` 崩溃处理

## 9. 配置参考

```json
{
  "gateway": {
    "port": 9000,
    "http_management_port": 9080,
    "io_threads": 2,
    "business_threads": 2,
    "max_connections": 10000,
    "per_ip_connection_limit": 0,
    "metrics_log_interval_ms": 5000,
    "metrics_prometheus_path": "runtime/metrics/gateway.prom",
    "metrics_json_path": "runtime/metrics/gateway.json",
    "auth": {
      "provider": "dev",
      "users_path": "config/auth_users.json",
      "http_endpoint": "http://127.0.0.1:8080/auth/validate",
      "http_timeout_ms": 3000
    }
  },
  "session": {
    "max_packet_size": 1048576,
    "max_pending_write_bytes": 262144,
    "heartbeat_check_interval_ms": 5000,
    "heartbeat_timeout_ms": 30000
  }
}
```

## 10. 下一阶段方向

- 多进程拆分：独立 login_server / room_server / battle_server
- 内部消息总线：Gateway ↔ Backend 高性能 RPC
- 服务发现：进程注册 + 健康检查 + 故障转移
- 广播锁优化：RCU / COW 快照减少竞争
- 协议压缩：zlib/zstd 大包压缩
- 持久化层：战斗回放落盘 + 玩家数据存储
