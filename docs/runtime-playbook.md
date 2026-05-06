# 当前网关骨架运行说明

> **本文档为 v1.x 维护期运行手册**。能力成熟度（`stable` / `experimental` / `reserved` / `demo-only`）以 `docs/v1-maturity-matrix.md` 为准。两份文档冲突时以矩阵为准。

## 1. 当前能力概览

当前项目在 `develop` 分支已经具备一个可运行的游戏网关骨架，包含以下模块：

### 1.1 核心运行时主链（stable）

- `net::Session` — 单连接生命周期、长度头协议拆包、异步收发、心跳检测、超时断开、发包限流、最大包长校验
- `net::MessageDispatcher` — 消息号注册、业务线程池投递、中间层执行
- `game::gateway::GatewayServer` — TCP accept、连接限制、HTTP 管理端点装配、metrics 导出
- `game::gateway::SessionManager` — 在线连接和登录态管理
- `game::room::RoomManager` — 房间生命周期、房主、成员、ready 状态
- `game::battle::BattleManager` — battle 上下文、输入序号、输入历史（按 `room_id` 组织）
- `game::gateway::GatewayMetrics` / `GatewayMetricsExporter` — 累计计数器 + 每秒速率 + Prometheus / JSON 导出
- `game::gateway::GatewayService` — 心跳和登录前白名单
- `game::login::LoginService` — 最小登录闭环（dev / json_file / http 三种校验器）
- `game::room::RoomService` — 房间加入 / 离开 / 准备 / 广播
- `game::battle::BattleService` — 同房间起战斗、输入路由、广播
- `game::gateway::PushService` — 统一发送动作（success / error / push / broadcast）

### 1.2 实验性能力（experimental）

不应作为生产可依赖能力使用，详细成熟度见 `docs/v1-maturity-matrix.md`：

- 大包压缩（仅在编译期带 zlib 时为真正压缩；无 zlib 时仅为长度前缀透传，标记位语义不稳定）
- HTTP Token 校验器（同步阻塞实现，会在业务线程池里阻塞 IO）
- 字节量指标（统计的是业务 body 大小，不是 socket 真实传输量）
- 配置热更新（`ConfigWatcher` 仅是文件触发器，实际只重应用 `max_connections` / `per_ip_connection_limit`）
- 审计日志（`logs/audit.log`，"近似 JSON 行" 格式，`details` 未做 JSON 转义）
- `MatchmakingService`（未与 1.x 默认网关入口绑定）
- `BattleManager` 帧同步（仅适合短局 demo）
- `RoomManager::broadcast_to_room()` COW 接口（与 `RoomService` 主广播路径未统一）
- 多进程入口 `login_server` / `room_server` / `battle_server`（按模块拆出的独立 demo 入口，**不是拆服架构**）

### 1.3 预留能力（reserved，不应宣称为完成态）

- `packet_fragment` 自动分片（**Session 收发主链未接入**）
- TLS 接入 `GatewayServer` 主链（配置可解析，主链未启用 SSL stream）
- Token 生命周期主动失效（`SessionManager` 不存储过期时间）
- 登录防爆破（`RateLimiter` 接口存在，`LoginService` 未调用）
- 游客账号 / `max_guests` 限制（主链未引用）
- 战斗回放生产链（读取链与存储抽象存在，`end_battle()` 不生成 replay）
- 观战协议（`BattleManager` 接口存在，`BattleService` 未提供）
- 结构化消息接入主链（`net::msg` / `message_serializer` 仍是协议草案，与真实字符串 body 已分叉）
- `InternalBus` / `ServiceRouter` / `ServiceRegistry` / `BackendRouter`（无独立 envelope，无 TTL/订阅，跨进程能力不闭环）

### 1.4 演示级能力（demo-only）

- `AdminService` 二进制管理命令（消息号 5001-5005）— 默认 `GatewayServer` 不注册，仅在 `examples/admin_demo` / `examples/login_demo` 中手工接线，**无任何权限校验**

## 2. 当前协议事实源

> 本节是 v1.x 维护期的**协议主契约**。所有 service / 集成测试基于本节描述的字符串 body 协议，**不基于** `net::msg` 结构化定义。

### 2.1 包格式（stable）

```text
[4字节总长度（不含本头）]
[2字节消息号]
[4字节请求序号]
[4字节错误码]
[1字节标记位]
[消息体]
```

### 2.2 标记位（`flags`）当前定义

| 位 | 名称 | 状态 | 语义 |
|---|---|---|---|
| `0x01` | `kCompressed` | stable（v1.1.2 起） | 包体经过压缩。**仅当 build 链接 zlib（`net::packet::is_compression_available()` 为真）时**才允许被设置；无 zlib build 上发送 `kCompressed` 包会被服务端直接断链 |
| `0x02` | `kEncrypted` | reserved | 仅常量定义，主链不产出也不消费 |
| 其他位 | — | reserved | 不应被任何代码 / 文档使用 |

`packet_fragment.h` 中曾出现的 `kFragment = 0x10` / `kLastFragment = 0x20` / `total_fragments << 4` **不属于当前主链协议**，v1.1.2 之后明确：v1.x 维护期不解锁主链分片能力。

### 2.3 当前消息号

| 消息号 | 名称 | 类型 | 说明 |
|---|---|---|---|
| 1 | `kHeartbeatRequest` | 心跳 | client → server |
| 2 | `kHeartbeatResponse` | 心跳 | server → client |
| 1001 | `kEchoRequest` | echo | |
| 1002 | `kEchoResponse` | echo | |
| 1003 | `kSessionKickedPush` | push | 顶号通知 |
| 1004 | `kSessionResumedPush` | push | 重连恢复通知 |
| 2001 | `kLoginRequest` | 登录 | body：`user_id\|token\|display_name` |
| 2002 | `kLoginResponse` | 登录 | body：`login_ok:user_id:display_name[:room=...]` |
| 3001 | `kRoomCreateRequest` / 3002 `kRoomCreateResponse` | 房间 | |
| 3003 | `kRoomJoinRequest` / 3004 `kRoomJoinResponse` | 房间 | response body：`room_joined:{room_id}:{player_count}` |
| 3005 | `kRoomLeaveRequest` / 3006 `kRoomLeaveResponse` | 房间 | |
| 3007 | `kRoomReadyRequest` / 3008 `kRoomReadyResponse` | 房间 | request 解析约定见 `docs/v1-string-protocol.md` §6.4 |
| 3009 | `kRoomStatePush` | 房间 | body：`room_state:...`（构造时回查 `SessionManager` 补全 user_id） |
| 4001 | `kBattleStartRequest` / 4002 `kBattleStartResponse` | 战斗 | response body：`battle_started:{room_id}:{player_count}` |
| 4003 | `kBattleInputRequest` / 4004 `kBattleInputResponse` | 战斗 | response body：`battle_input_accepted:{room_id}:{sequence}` |
| 4005 | `kBattleInputPush` | 战斗 | body：`battle_input:{room_id}:{user_id}:{sequence}:{payload}` |
| 4006 | `kBattleStatePush` | 战斗 | body：`battle_state:started:{room_id}:{player_count}` 等 |
| 5001-5005 | `kAdmin*` | demo-only | 无权限校验，默认主链不注册，详见 §1.4 |
| 9001 | `kErrorResponse` | 通用错误 | |

### 2.4 错误码（`net::protocol::ErrorCode`）

数值与默认 body 以 `include/net/protocol.h` 与 **`docs/v1-string-protocol.md` §3** 为准。**`v1.1.6`** 起：`SubmitInputResult::kPlayerNotInBattle` 使用 **`kPlayerNotInBattle (3004)`**，body **`player_not_in_battle`**，不再复用 `kAuthRequired`。

### 2.5 协议事实源约束

- `v1.x` 维护期内，**字符串 body 是唯一线上协议**
- **login / room / battle** 的请求响应与 push body **冻结明细**：`docs/v1-string-protocol.md`
- `net::msg` / `message_serializer` 仅作为协议草案保留，不应被业务 service 使用
- 任何对线上协议的描述（README / runtime-playbook / 集成测试）必须与 **`v1-string-protocol.md`** 一致
- 协议变更必须先更新 **`v1-string-protocol.md`** 与本节，再改实现

## 3. 消息处理链路

```text
客户端 -> Session 读包 -> [入站：解码 -> （flags::kCompressed 时）解压]
       -> MessageDispatcher
          -> [ingress：登录前白名单 + 基础限频]   （v1.1.3 起在 Session 调用线程同步执行，早于业务池）
       -> asio::post -> 业务线程池
          -> [可选 post-pool middleware] -> Login/Room/Battle Handler -> Session 回包
       -> [出站：序列化 -> （需要时）压缩 -> 编码]
```

中间层（ingress，`GatewayService::register_handlers`）：

- 登录前白名单：允许 `kHeartbeatRequest` / `kLoginRequest` / `kEchoRequest`
- 未登录业务拦截：未登录时访问房间或战斗接口返回 `kErrorResponse: auth_required`
- 基础限频：单连接每秒最多通过 32 条非心跳消息，超限返回 `kErrorResponse: rate_limited`

> **v1.1.3 / T05**：上述策略从“业务线程池内执行”前移到“投递到线程池之前”。无有效 `Session` 的 `dispatch(nullptr, …)`（实验性内部总线）**不**跑 ingress，详见 `include/net/message_dispatcher.h` 与 `docs/v1-maturity-matrix.md` §2.4。

## 4. 目录对应关系

- `include/net` / `src/net` — 网络基础设施
- `include/game/gateway` / `src/game/gateway` — 网关层、会话管理、指标
- `include/game/login` / `src/game/login` — 登录业务
- `include/game/room` / `src/game/room` — 房间业务
- `include/game/battle` / `src/game/battle` — 战斗业务
- `examples/echo` — **当前推荐运行参考**（集成样例 / 主展示入口）
- `examples/{login,room,battle,admin}_demo` — showcase app（完整服务器拼装演示）
- `examples/{login,room,battle}` — 独立入口实验版（不是拆服架构）
- `examples/pressure` — 压测客户端

## 5. 运行方式

### 5.1 常规构建（可访问 GitHub）

```powershell
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
D:\Program\boost\build\windows-msvc-debug\examples\echo\Debug\echo_server.exe config/gateway.json
```

### 5.2 内网构建（无法访问 GitHub）

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

### 5.3 客户端 / 压测

```powershell
D:\Program\boost\build\windows-msvc-debug\examples\echo\Debug\echo_client.exe 127.0.0.1 9000 hello
D:\Program\boost\build\windows-msvc-debug\examples\pressure\Debug\gateway_pressure.exe 127.0.0.1 9000 100 10
D:\Program\boost\build\windows-msvc-debug\examples\pressure\Debug\gateway_pressure.exe config/pressure.json
```

`gateway_pressure` 当前支持 9 种 `PressureScenario`：`echo` / `invalid_token` / `slow_echo` / `broadcast_storm` / `malicious_packet` / `battle_broadcast` / `chaos` / `stability` / `benchmark`。

## 6. 当前测试覆盖

`tests/` 目录下：

- `tests/unit/`（14 个 `.cpp`）：协议编解码、消息分发、`SessionManager` / `RoomManager` / `BattleManager` 状态、token 校验器、压缩、metrics 导出、admin handler 注册、配置解析、匹配、`packet_fuzz`
- `tests/integration/`（2 个 `.cpp`）：`gateway_integration_test`（登录 + 房间 + 战斗 + 顶号恢复 + 心跳超时）、`http_management_test`

实际 ctest 用例数以 `ctest -N` 实际枚举为准（GoogleTest `gtest_discover_tests` 展开后）。

> **测试覆盖盲点**（详见 `docs/development-optimization.md` §9 各模块"测试"小节）：
>
> - 登录边界：token 过期 / HTTP 鉴权超时 / 畸形 body / 登录失败频控
> - 房间边界：`transfer_session` / 房间快照成员身份 / 空房 battle 清理 / 多成员广播排除自身
> - 战斗边界：非房主开战 / 未 ready 开战 / `player_not_in_battle` 错误语义 / `room_manager` 与 `battle_manager` 状态分叉 / 长局输入累积
> - 治理边界：admin 权限模型 / `/health` 真实运行联动 / reload / kick / ban 行为
> - 装配/生命周期：watcher 生效范围 / shutdown sequence 顺序

## 7. 当前指标

`GatewayMetrics` 暴露的累计 counter（10 类）+ 每秒 rate（6 类）。完整列表见 `include/game/gateway/gateway_metrics.h`。

> **观测口径警示**：
>
> - 字节量指标当前统计的是**业务 body 大小**，不是 socket 真实传输量；与 wire bytes 在压缩 / 批量发送时会偏离
> - 活跃 gauge（active_sessions / active_rooms / active_battles）的可信度依赖 `Session` 关闭路径正确清理状态。`v1.1.2` 起 `Session::stop()` 与异常关闭统一经由 `handle_close()` 触发 `close_handler_`，活跃 gauge / 引用计数清理已自洽

若在 `config/gateway.json` 里配置了 `gateway.metrics_prometheus_path` / `gateway.metrics_json_path`，服务端会按 `gateway.metrics_log_interval_ms` 周期把指标导出到对应文件。

### HTTP 管理端点

若在 `config/gateway.json` 里配置了 `gateway.http_management_port`（默认 9080），服务端启动 HTTP 管理端点：

| 端点 | 方法 | Content-Type | 状态 | 说明 |
|---|---|---|---|---|
| `/health` | GET | application/json | experimental | **当前固定返回 `{"status":"ok"}`，不依赖运行时真实健康状态** |
| `/metrics` | GET | text/plain | stable | Prometheus 格式指标 |
| `/metrics/json` | GET | application/json | stable | JSON 格式指标快照 |

> **安全提示**：HTTP 管理端点**当前无任何鉴权**，监听全网卡，**仅适合内网 / 受信网络**。

设置 `http_management_port = 0` 则禁用 HTTP 管理端点。

## 8. 当前主链已闭环的能力清单

仅列 `stable` 项。`experimental` / `reserved` / `demo-only` 详见 `docs/v1-maturity-matrix.md`。

### 网络层
- `Session`：长度头协议、异步收发、心跳超时、发包限流、最大包长校验
- 协议：`[4B长度][2B msg][4B req][4B err][1B flags][body]`
- `MessageDispatcher`：消息号注册、业务线程池投递、中间层链
- `SessionManager`：在线连接管理、登录态跟踪、顶号踢线
- 链路追踪：`trace_id` 贯穿 Session → Dispatcher → Handler → 日志
- 连接限制：`max_connections` + `per_ip_connection_limit`
- 线程池拆分：按消息 ID 范围路由
- BufferPool / ObjectPool 复用分配
- `Session::send_batch()`

### 业务层
- `LoginService`：dev / json_file 鉴权
- `RoomService`：创建 / 加入 / 离开 / 准备、房主机制
- `BattleService`：起战斗、输入路由、输入广播、`advance_frame`、`end_battle`（基础结算）
- `PushService`：统一发送动作

### 可观测性
- `GatewayMetrics` 10 类累计 counter
- `GatewayMetricsExporter` Prometheus 文本 + JSON 快照
- HTTP 管理端点 `/metrics` / `/metrics/json`
- 慢连接检测：写队列 > 50% 上限 WARN
- 崩溃转储：`runtime/crashes/crash_*.txt`

### 工程化
- CMake + FetchContent + 本地 third_party 内网构建
- `gateway_pressure` 压测工具（9 种场景）
- `Dockerfile` + `docker-compose.yml` + CI Docker 构建
- `HandlerRegistry` 批量注册

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

> **配置字段成熟度**（哪些启动生效 / 哪些热更新生效 / 哪些仅预留）见 `docs/v1-maturity-matrix.md` §5.1。

## 10. v1.x 维护下一阶段方向

按 `docs/development-optimization.md` §11 任务表推进，**不进入 v2.0.0 范畴**：

- `v1.1.2` — `Session` 主动 / 异常关闭路径统一；固定协议增强顺序；明确分片当前未启用；修正无 zlib 时压缩标记位语义
- `v1.1.3` — 入口治理前置（白名单 / 限频从业务线程池后挪到 ingress 前置）
- `v1.1.4` — **`battle_started` 单一事实源（第一阶段，`BattleManager` 为准，`RoomManager` 经 `set_battle_active_query` 查询）已完成**
- `v1.1.5` — **业务事实源叙事校准**（见 `docs/v1-business-fact-source.md`：**无代码变更**）
- `v1.1.6` — **业务字符串协议冻结**（`docs/v1-string-protocol.md`）+ **`kPlayerNotInBattle` 错误码语义修正（T02 后半）已完成**
- `v1.1.7` — **跨域编排收口（T07/T08）**：`docs/v1-cross-domain-flows.md`；`login_recovery`、`clear_battle_if_room_empty` **已完成**
- `v1.1.8` — **房/战边界**：`RoomMember.member_user_id`、`transfer_session`/战斗中语义、**`docs/v1-room-battle-boundary.md`** **已完成**
- `v1.1.9` — **治理入口分层（T10）**：**`docs/v1-governance-layers.md`** **已完成**
- `v1.1.10` — 治理成熟度冻结（文档）
- `v1.2.0` — 决策点：是否正式推进 typed protocol / internal bus / battle replay 闭环
