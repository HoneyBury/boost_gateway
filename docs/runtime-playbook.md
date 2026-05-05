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

## 8. 当前完成的优先级任务

当前已经完成：

1. `SessionManager / RoomManager / BattleManager` 状态拆分
2. `request_id + error_code` 协议升级
3. 网关限频中间层
`GatewayServer` 会定时把这些指标和当前在线状态打印到日志里。

## 9. 下一步建议

当前这套骨架可以继续往下演进的方向：

1. 增加请求序号、错误码枚举和统一响应包结构。
2. 把房间和战斗状态从 `SessionManager` 中继续拆分成独立管理器。
3. 接入配置系统，替代当前硬编码阈值和时间参数。
4. 增加广播、踢线、重连恢复和限频控制。
5. 把指标导出到 Prometheus 或类似监控系统。
## 10. 2026-05-05 补充更新

当前协议消息号已经扩展为：

- `3001/3002`：创建房间请求/响应
- `3003/3004`：加入房间请求/响应
- `3005/3006`：离开房间请求/响应
- `3007/3008`：准备状态请求/响应
- `3009`：房间状态广播
- `4001/4002`：开始战斗请求/响应
- `4003/4004`：战斗输入请求/响应
- `4005`：战斗输入广播
- `4006`：战斗状态广播

当前新增能力：

- `LoginService` 已接入 token 校验与登录上下文
- `RoomService` 已支持创建、加入、离开、准备和房间广播
- `BattleService` 已支持房主起战斗、输入路由和战斗广播
- 顶号登录现在会给旧连接发送 `kSessionKickedPush`，并把房间状态恢复到新连接
- 服务端启动已支持从 `config/gateway.conf` 或 `config/gateway.json` 加载关键参数
- 当前默认 JSON 配置使用 `dev` 鉴权提供方，也可以切换到 `json_file`
- `config/auth_users.json` 可作为本地外部鉴权数据源
- `gateway_pressure` 已支持 `echo`、`invalid_token`、`slow_echo` 三种压测场景

## 11. 2026-05-05 HTTP 管理端点补充

- 新增 `net::HttpManager`，基于 Boost.Beast 实现独立 HTTP 管理端口
- 端点：`GET /health`（健康检查）、`GET /metrics`（Prometheus）、`GET /metrics/json`
- 配置项 `gateway.http_management_port` 默认 9080，设为 0 禁用
- 测试覆盖：`HttpManagementTest`（4 个集成测试用例）
