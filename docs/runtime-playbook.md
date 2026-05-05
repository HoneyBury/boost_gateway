# 当前网关骨架运行说明

## 1. 当前能力概览

当前项目已经具备一个最小可运行的游戏网关骨架，包含以下模块：

- `net::Session`
  负责单连接生命周期、长度头协议拆包、异步收发、心跳检测、超时断开、发包限流、最大包长校验。
- `net::MessageDispatcher`
  负责消息号注册、业务线程池投递和中间层执行。
- `game::gateway::SessionManager`
  负责在线连接、登录态、房间态、战斗态的最小状态管理。
- `game::gateway::GatewayMetrics`
  负责会话、包量、字节量、拦截量和业务成功量统计。
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

启动网关示例：

```powershell
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
D:\Program\boost\build\windows-msvc-debug\examples\echo\Debug\echo_server.exe 9000
```

启动 Echo 客户端：

```powershell
D:\Program\boost\build\windows-msvc-debug\examples\echo\Debug\echo_client.exe 127.0.0.1 9000 hello
```

启动压测客户端：

```powershell
D:\Program\boost\build\windows-msvc-debug\examples\pressure\Debug\gateway_pressure.exe 127.0.0.1 9000 100 10
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
