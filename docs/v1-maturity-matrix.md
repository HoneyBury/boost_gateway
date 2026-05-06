# v1.x 能力成熟度矩阵

## 1. 文档定位

本文档是 `v1.x` 维护期的**单一事实源**，用于回答以下问题：

- 哪些能力是 `v1.0.0` 发布版（tag `v1.0.0`，commit `22defe8`）已稳定承诺的能力
- 哪些能力是 `develop` 分支在 `v1.0.0` 之后继续叠加、但尚未正式承诺的能力
- 哪些能力是接口/抽象/示例已经存在、但主链尚未闭环的预留能力

后续所有维护讨论、协议判断、配置说明，请以本文档为准。`README.md` / `CHANGELOG.md` / `runtime-playbook.md` / `development-priority.md` 等文档遇到与本表冲突的描述，应以本表为准并同步修正。

成熟度等级约定：

- `stable`：主链已闭环，列入 `v1.x` 维护回归面，行为变更必须发版本号
- `experimental`：实现存在但仅在示例/演示场景接线，行为可能在 `v1.x` 维护中调整
- `reserved`：仅有接口/类型/常量定义，主链未接入，**不应被视为可依赖能力**
- `demo-only`：仅在 `examples/*` 中演示拼装，未纳入核心运行时主链

---

## 2. 网络与协议层

### 2.1 包格式

| 项 | 状态 | 说明 |
|---|---|---|
| `[4B 长度][2B msgid][4B reqid][4B err][1B flags][body]` | `stable` | 当前唯一线上协议，由 `net::packet::encode` / `decode_payload` 落实 |
| `flags::kCompressed (0x01)` | `stable`（v1.1.2 起） | 仅当 `net::packet::is_compression_available()` 为真时（即 build 链接了 zlib）才允许设置；接收侧若 build 没有压缩后端，会直接拒绝带 `kCompressed` 的入包。语义跨 build 自洽 |
| `flags::kEncrypted (0x02)` | `reserved` | 仅常量定义，主链不产出也不消费 |
| 分片 (`packet_fragment`) | `reserved` | `fragment_packet` / `FragmentAssembler` 仅在测试与演示中存在，**`Session` 收发主链未接入**，对外不应宣称"已支持自动分片"。v1.x 维护期不计划解锁 |

### 2.2 消息体协议

| 项 | 状态 | 说明 |
|---|---|---|
| 字符串 body（如 `login_ok:user`、`room_state:...`、`battle_started:...`） | `stable` | 这是 `v1.x` 实际线上业务协议主路径，所有 service / 集成测试均以字符串 body 为契约 |
| `net::msg` 18 种结构化消息 + `message_serializer` | `experimental` | 类型与序列化函数已存在，但**业务主链未使用**，且部分定义已与真实 body 分叉（如 `LoginResponse` 缺 `room` 字段、`RoomJoinResponse` 缺 `player_count`、`BattleStartResponse` 含 `battle_id` 但实现仅用 `room_id`）。1.x 维护期内只作为草案保留，不应被视为已落地协议 |

### 2.3 协议增强能力组合顺序

| 项 | 状态 | 说明 |
|---|---|---|
| 入站：解码 → 解压 → 投递 | `stable`（v1.1.2 固定） | 仅在 `is_compression_available()` 为真时执行解压；伪造 `kCompressed` 的对端会被关闭（`invalid_argument`） |
| 出站：序列化 → 压缩 → 编码 | `stable`（v1.1.2 固定） | 仅在 `is_compression_available()` 为真时压缩并设置 `kCompressed`；否则不压缩、不打标志位 |
| 入站重组、出站分片 | `reserved` | v1.x 维护期不启用，主链不分片不组帧 |

### 2.4 消息分发与服务路由

| 项 | 状态 | 说明 |
|---|---|---|
| `MessageDispatcher` handler 注册 / 线程池范围路由 | `stable` | 主链使用 |
| `GatewayService`：`register_ingress_middleware`（登录前白名单 + 连接维基础限频） | `stable`（v1.1.3） | **在投递到业务线程池之前**，在调用线程（`Session::strand`）上同步运行；被拒包不占用 worker。见 `development-optimization.md` §8.4（T05） |
| `MessageDispatcher::register_middleware`（post-pool 链） | `stable` | 保留用于兼容与未来扩展；网关入口策略已全部迁到 ingress |
| `InternalBus` → `dispatch(nullptr, …)` | `experimental` | **不显式跑 ingress 中间件**，避免会话级策略作用于内部链路；仍会跑 post-pool `register_middleware` 链；尚无独立 envelope |
| `ServiceRouter` | `experimental` | 仅本地 `DispatchContext` 转发，不具备跨进程路由能力 |
| `ServiceRegistry` | `experimental` | 内存注册表，不含 TTL/订阅/健康联动 |
| `BackendRouter` | `reserved` | 号段映射 + 空 handler，未进入主链 |

## 3. 业务层

> **模块边界与「谁说了算」的叙述说明**见 `docs/v1-business-fact-source.md`（维护版 **`v1.1.5`**）。下表仍为本节**单项能力**的成熟度事实源。

### 3.1 登录

| 项 | 状态 | 说明 |
|---|---|---|
| `DevTokenValidator` | `stable` | 主链可用 |
| `JsonFileTokenValidator` | `stable` | 主链可用 |
| `HttpTokenValidator` | `experimental` | **同步阻塞**实现，登录 handler 会在业务线程池里做阻塞 IO，`timeout_` 字段未真正约束 socket，仅适合 demo / 内网联调 |
| Token 生命周期 (`expires_at` / `TokenExpired` 错误码) | `experimental` | 校验器内部计算 TTL，但 `SessionManager::LoginContext` 不存储过期时间，运行时不会基于 TTL 主动失效；`expired = true` 在 dev / json_file / http 三类校验器中目前均无主动产出路径 |
| 登录顶号 / 重复登录踢线 | `stable` | 主链闭环，`kSessionKickedPush` 通知旧连接 |
| 登录失败防爆破（`RateLimiter::check_login_attempt` 等） | `reserved` | `RateLimiter` 暴露了相关接口，但 `LoginService` 主链未调用 |
| 游客账号 / `max_guests` 限制 | `reserved` | 配置字段存在，主链未接入 |

### 3.2 房间

| 项 | 状态 | 说明 |
|---|---|---|
| 创建 / 加入 / 离开 / 准备 / 房主切换 | `stable` | 主链闭环 |
| `RoomManager::transfer_session` | `stable` | 顶号场景的会话迁移已闭环；定位为兼容性桥接，不是房间核心职责 |
| `RoomManager::broadcast_to_room()` (COW 快照接口) | `experimental` | 接口存在，但 `RoomService` 实际广播仍走 `room_snapshot()` + 循环 `send`，主链未统一 |
| 房间快照成员身份 | `experimental` | 广播 body 构造时仍需回查 `SessionManager::login_context_of()` 才能补齐 `user_id` / `display_name`，房间快照不自洽 |

### 3.3 战斗

| 项 | 状态 | 说明 |
|---|---|---|
| `BattleManager`：起战 / 输入收集 / `advance_frame` / `end_battle` | `stable` | 当前主链以 `room_id` 为 battle key，是按房间组织的输入收集器，**不是完整战斗领域引擎** |
| `BattleResult` 结算（`winner` / `scores`） | `experimental` | 当前结算策略基于"输入条数"，仅供演示 |
| 帧同步（`current_frame` + `advance_frame()`） | `experimental` | 适合短局、低输入量 demo，不适合长时间真实帧同步 |
| `BattleManager` 观战接口（`add_spectator` 等） | `reserved` | 接口存在，`BattleService` 未提供观战加入 / 退出 / 同步协议 |
| `ReplayPlayer` + `IBattleReplayStore` | `reserved` | 读取链与存储抽象存在，但 `end_battle()` 主链**不生成 replay**，回放生产链未闭环 |
| `battle_started` 单一事实源 | `stable`（v1.1.4） | **`BattleManager::active_battles_`（`battle_started(room_id)`）为唯一事实源**；`RoomManager` 不再维护独立 `battle_started` 字段。房间侧通过 `set_battle_active_query([&bm](const auto& id){ return bm.battle_started(id); })` 派生视图（join/ready/snapshot）。`BattleService` 成功起战后不再回写房间。未设置 query 的演示装配中房间「战斗中」视图恒为假 |
| `SubmitInputResult::kPlayerNotInBattle` → `ErrorCode::kPlayerNotInBattle` | `stable`（v1.1.6） | body 默认 `player_not_in_battle`（`to_string`）；不再使用 `kAuthRequired` |

### 3.4 匹配

| 项 | 状态 | 说明 |
|---|---|---|
| `MatchmakingService` (ELO 队列) | `experimental` | 单元测试覆盖，主链可用，但未与 1.x 默认网关入口绑定 |

### 3.5 推送

| 项 | 状态 | 说明 |
|---|---|---|
| `PushService::send_ok / send_error / send_push / broadcast` | `stable` | 主链使用 |
| 统一响应协议（统一 body 结构 / 统一可观测字段） | `reserved` | 当前 `PushService` 只统一发送动作，body 仍由各 service 自由拼接 |

---

## 4. 治理与控制面

### 4.1 HTTP 管理端点（`net::HttpManager`）

| 项 | 状态 | 说明 |
|---|---|---|
| `GET /health` | `experimental` | 当前固定返回 `{"status":"ok"}`，**不依赖运行时真实健康状态**；`HttpManager::HealthProvider` 仅有声明，主链未使用 |
| `GET /metrics` (Prometheus 文本) | `stable` | 主链可用，可被 Prometheus scrape |
| `GET /metrics/json` | `stable` | 主链可用 |
| 来源限制 / 鉴权 / 仅监听内网接口 | `reserved` | 当前 HTTP 管理端点**无任何鉴权**，监听全网卡，仅适合内网 / 受信网络 |

### 4.2 二进制管理命令（`AdminService`，消息号 5001-5005）

| 项 | 状态 | 说明 |
|---|---|---|
| `kAdminServerStatus / kAdminReloadConfig / kAdminKickPlayer / kAdminBanIp / kAdminResponse` | `demo-only` | 仅在 `examples/admin_demo` / `examples/login_demo` 中手工接线，**默认 `GatewayServer` 不注册这组 handler**；当前**没有任何权限校验**——任何已建立连接的客户端只要构造对应消息号即可触发管理动作 |
| 权限模型 / 审计字段规范 | `reserved` | 当前 admin 调用只产出自由文本审计，无 `actor` / `target` / `outcome` 字段约定 |

### 4.3 限频与连接控制

| 项 | 状态 | 说明 |
|---|---|---|
| `RateLimiter` 多维度限频（连接 / 消息类型 / 登录） | `experimental` | 接口完整，但仅"连接维度"在 `GatewayService` 中接入；消息类型限频与登录限频未接入主 `LoginService` |
| `max_connections` / `per_ip_connection_limit` | `stable` | 主链已接入 |
| `RateLimiter` 状态以 `const Session*` 裸指针为 key | `experimental` | 关闭路径不一定回收 `rate_limits_` 项，存在脏 entry 累积可能（v1.1.3 / T05 候选范围） |
| `Session` 关闭收口（`stop()` / 异常关闭统一走 `handle_close()`，触发 `close_handler_` 仅一次） | `stable`（v1.1.2 起） | 主动关闭与心跳超时 / 网络异常 / 写队列溢出 / 包非法等异常关闭走同一条路径，`SessionManager` / `GatewayMetrics` / `active_connection_count_` 计数自洽。回归覆盖见 `tests/unit/session_close_test.cpp` |

### 4.4 审计日志（`app::audit_log`）

| 项 | 状态 | 说明 |
|---|---|---|
| `AUDIT_LOG(event, details)` 写入 `logs/audit.log` | `experimental` | 主链已接入登录成功 / 失败、限频、连接拒绝、配置重载等节点；**输出格式为"近似 JSON 行"**：`details` 字段未做 JSON 转义，包含引号 / 反斜杠 / 换行时不再是合法 JSON，**不应被视为稳定结构化日志** |
| 统一审计字段（`actor` / `target` / `source_ip` / `request_id` / `outcome` / `reason_code`） | `reserved` | 当前审计模型仅 `ts` / `event` / `details`，无统一字段规范 |

### 4.5 TLS

| 项 | 状态 | 说明 |
|---|---|---|
| `net::TlsConfig` 配置结构 | `experimental` | 配置可解析，`tls.cert_chain_path` 存在即 `tls.enabled = true` |
| TLS 接入 `GatewayServer` 主链 | `reserved` | 当前 `GatewayServer` 实例化的是普通 `Session`，**未启用 SSL stream**；`tls` 字段当前**不会自动生效** |

---

## 5. 配置与运行时装配

### 5.1 `GatewayAppConfig` 字段成熟度

| 字段 | 启动生效 | 热更新生效 | 主链接入 | 备注 |
|---|---|---|---|---|
| `port` | ✅ | ❌（重启） | `stable` | 主监听端口 |
| `http_management_port` | ✅ | ❌（重启） | `stable` | 0 表示禁用 |
| `io_threads` | ✅ | ❌（重启） | `stable` | |
| `business_threads` | ✅ | ❌（重启） | `stable` | |
| `metrics_log_interval` | ✅ | ❌（重启） | `stable` | |
| `metrics_prometheus_path` | ✅ | ❌（重启） | `stable` | |
| `metrics_json_path` | ✅ | ❌（重启） | `stable` | |
| `auth_provider` | ✅ | ❌（重启） | `stable` | dev / json_file / http |
| `auth_users_path` | ✅ | ❌（重启） | `stable` | json_file 模式必填 |
| `auth_http_endpoint` | ✅ | ❌（重启） | `experimental` | http 模式同步阻塞，见 3.1 |
| `auth_http_timeout` | ✅ | ❌（重启） | `experimental` | 字段被读取，但 http 校验器实际不约束 socket 超时 |
| `max_connections` | ✅ | ✅ | `stable` | `examples/echo` 等入口 reload 时调用 `set_connection_limits()` |
| `per_ip_connection_limit` | ✅ | ✅ | `stable` | 同上 |
| `max_guests` | ✅（解析） | ❌ | `reserved` | 字段被解析但**主链未引用** |
| `session_max_packet_size` | ✅ | ❌（重启） | `stable` | |
| `session_max_pending_write_bytes` | ✅ | ❌（重启） | `stable` | |
| `session_heartbeat_check_interval` | ✅ | ❌（重启） | `stable` | |
| `session_heartbeat_timeout` | ✅ | ❌（重启） | `stable` | |
| `tls.*` | ✅（解析） | ❌ | `reserved` | 字段被解析，主链未启用 SSL stream，见 4.5 |

> 说明：`✅(解析)` 表示配置层能读出该字段，但运行时主链未对该字段做出行为响应；`reserved` 字段**不应被运维当作可生效配置**。

### 5.2 `ConfigWatcher`

| 项 | 状态 | 说明 |
|---|---|---|
| 文件 `last_write_time` 轮询 + 触发 reload callback | `stable` | 主链可用 |
| 配置校验失败回滚 / reload 状态传播 / 去抖 | `reserved` | 当前实现仅是文件变更触发器，不是完整热更新框架 |

### 5.3 `GracefulShutdown`

| 项 | 状态 | 说明 |
|---|---|---|
| `SIGINT` / `SIGTERM` 触发 `on_shutdown` 回调 | `stable` | 主链可用 |
| 统一 shutdown sequence（监听停 → 会话排干 → metrics flush → persistence flush） | `reserved` | 当前由各 `examples/*main.cpp` 各自手工编排，不同入口语义不一致 |

### 5.4 `examples/` 入口分类

| 目录 | 类型 | 说明 |
|---|---|---|
| `examples/echo/` | 集成样例 / 主展示入口 | 完整网关装配，是当前推荐的运行参考 |
| `examples/login_demo/` `examples/room_demo/` `examples/battle_demo/` `examples/admin_demo/` | showcase app | 各自为完整服务器拼装的演示版本，不是单点能力示例 |
| `examples/login/` `examples/room/` `examples/battle/` | 独立入口实验版 | 各自启动一份 `GatewayServer + SessionManager + Dispatcher`，**不是完整拆服架构**，更准确的描述是"按模块拆出的独立 demo 入口" |
| `examples/pressure/` | 压测工具 | 8 种场景的客户端压测程序 |

---

## 6. 持久化与回放

| 项 | 状态 | 说明 |
|---|---|---|
| `IPlayerStore` / `JsonFilePlayerStore` 读写 | `experimental` | 接口与 JSON 后端可用，**保存时机由各 example 主程序在停服回调中手工触发**，不是稳定的领域持久化策略 |
| `SqlitePlayerStore` | `experimental` | 备选实现，**`auth_provider` 不含 store 选择字段**，运行时未提供统一切换策略 |
| `PlayerRecord` 字段（`user_id` / `display_name` / `score` / `last_login_ts`） | `experimental` | 与登录态 / 房间态 / 战斗态没有稳定映射，更接近"演示型停服归档"，不是完整玩家档案 |
| `IBattleReplayStore` / `JsonFileBattleReplayStore` | `reserved` | 存储抽象存在；`BattleManager::end_battle()` 主链**不生成 replay**，回放生产链未闭环 |
| `ReplayPlayer` 读取链 | `reserved` | 可独立从已有 JSON 回放文件回放，但与战斗主链未对接 |

---

## 7. 可观测性

| 项 | 状态 | 说明 |
|---|---|---|
| `GatewayMetrics` 10 类累计 counter + 6 类 _per_sec rate | `stable` | 主链可用 |
| `GatewayMetricsExporter` Prometheus 文本 / JSON 快照 | `stable` | 主链可用 |
| 字节量指标（`bytes_received` / `bytes_sent`） | `experimental` | 当前统计的是**业务 body 大小**，不是 socket 真实传输量；与 wire bytes 在压缩 / 批量发送时会偏离 |
| 请求链路 `trace_id` | `stable` | 贯穿 Session → Dispatcher → Handler |
| Grafana dashboard / Prometheus alerts | `stable` | 模板可用 |
| 崩溃转储（Windows SEH + POSIX signal） | `stable` | 输出至 `runtime/crashes/` |

---

## 8. 工程能力

| 项 | 状态 | 说明 |
|---|---|---|
| CMake + FetchContent + 本地 third_party 内网构建 | `stable` | 主链可用 |
| Docker / docker-compose / GitHub Actions CI | `stable` | 主链可用 |
| 测试规模：14 个单元测试源文件 + 2 个集成测试源文件 + `packet_fuzz_test.cpp` 模糊测试 | `stable` | `ctest --preset windows-msvc-debug -N` 当前枚举共 **54 个用例**（GoogleTest `gtest_discover_tests` 展开后），其中：1 hello + 5 config + 2 metrics_exporter + 2 packet_codec + 3 dispatcher + 1 room_manager + 1 battle_manager + 2 session_manager + 1 service_registration + 3 token_validator + 9 packet_fuzz + 4 matchmaking + 5 packet_compressor + 2 admin_service + 8 gateway_integration + 4 http_management |
| 9 种压测场景（echo / invalid_token / slow_echo / broadcast_storm / malicious_packet / battle_broadcast / chaos / stability / benchmark） | `stable` | `PressureScenario` 枚举共 9 个值，README/CHANGELOG 早期"6 种 / 8 种"表述需以 `PressureScenario` 枚举为准 |
| `BufferPool` / `ObjectPool` | `stable` | 主链可用 |
| `Session::send_batch` | `stable` | 主链可用，但批量发送的逐消息观测语义未拆，见 development-optimization §6.A.2 |

---

## 9. 不应被宣称为完成态的能力（汇总）

为避免后续文档误读，以下能力在 `v1.x` 维护期内**不应在 README / CHANGELOG / runtime-playbook 中作为完成态能力宣传**，相应表述应改为"预留"或"演示级"：

1. **自动分片传输** — `packet_fragment` 主链未接入
2. **TLS 加密接入** — 配置可解析，但 `GatewayServer` 主链未启用 SSL stream
3. **Token 生命周期失效** — 校验器计算 TTL，但 `SessionManager` 不存储，运行时不主动失效
4. **登录防爆破** — `RateLimiter` 接口存在，`LoginService` 未调用
5. **游客账号** — `max_guests` 配置存在，主链未引用
6. **完整热更新** — `ConfigWatcher` 仅是文件变更触发器，实际生效字段仅 `max_connections` / `per_ip_connection_limit`
7. **完整管理面 / 二进制管理命令** — `AdminService` demo-only，无权限校验
8. **多进程拆服架构** — `login_server` / `room_server` / `battle_server` 是各自带接入层的独立 demo 入口
9. **服务发现** — `ServiceRegistry` 是内存注册表，无 TTL / 健康联动
10. **战斗回放** — 读取链与存储抽象存在，生产链未闭环
11. **观战模式** — `BattleManager` 接口存在，`BattleService` 未提供协议
12. **结构化消息接入主链** — `net::msg` / `message_serializer` 仍是协议草案
13. **内部消息总线** — `InternalBus` 与客户端共用编解码与分发链，没有独立 envelope

---

## 10. 维护版本节奏

`v1.x` 维护期版本号严格按照 `docs/development-optimization.md` §8.0 / §11 给出的批次推进：

| 版本 | 主题 | 范围（任务编号见 development-optimization §11.2） |
|---|---|---|
| `v1.1.1` | 基线校准 | T01 / T02 / T10 / T12 / T14 |
| `v1.1.2` | 会话与协议收口 | T03 / T04 |
| `v1.1.3` | 入口治理前置 | T05 |
| `v1.1.4` | `battle_started` 单一事实源（T06 第一阶段） | T06 |
| `v1.1.5` | 业务事实源校准（文档） | （`v1-business-fact-source.md`） |
| `v1.1.6` | 业务协议冻结 | T02 后半：`docs/v1-string-protocol.md` + **`kPlayerNotInBattle`** |
| `v1.1.7` | 跨域编排收口 | T07 / T08：`login_recovery` + `room_battle_lifecycle`、`docs/v1-cross-domain-flows.md` — **当前版本** |
| `v1.1.8` | 房间/战斗边界收紧 | T09 |
| `v1.1.9` | 治理入口分层 | T10 后半 |
| `v1.1.10` | 治理成熟度冻结 | （文档） |
| `v1.1.11` | admin 权限与审计 | T11 |
| `v1.1.12` | 配置成熟度表 | T12 后半 |
| `v1.1.13` | 标准启动 / reload / shutdown 顺序 | T13 |
| `v1.1.14` | 受控生命周期流程 | T13 后半 |
| `v1.1.15` | 横切能力定位 | T14 后半 |
| `v1.1.16` | 横切动作按生命周期收口 | T15 |
| `v1.1.17` | 数据格式冻结 | T16 |
| `v1.2.0` | 结构升级决策点 | T21（仅在前面收口完成后） |
| `v1.2.1 - v1.2.4` | 各主线回归面加固 | T17 / T18 / T19 / T20 |

**严格约束**：在 `v1.2.0` 决策点之前，**不进入 v2.0.0 范畴的开发**（Actor / ECS / 集群路由 / 状态生命周期系统 / 控制面，详见 `docs/v2-roadmap.md` 与 `docs/v2-design.md`）。
