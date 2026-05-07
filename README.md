# Boost 游戏服务器框架 v1.2.4

基于 Boost.Asio 构建的高性能 C++20 游戏服务器框架。

> **版本基线说明**
>
> - `v1.0.0` 对应 git tag `v1.0.0`（commit `22defe8`），是稳定承诺的最小发布面。
> - 当前发布版为 **`v1.2.4`**，已完成 `v1.x` 在 `2.0` 前的维护收束：事实源校准、主链收口、边界测试加固、结构升级决策归档。
> - **本 README 中的能力描述与 `docs/v1-maturity-matrix.md` 冲突时以矩阵为准**。矩阵是维护期的单一事实源。
> - `v2.0.0`（Actor + ECS 混合架构）当前**仍未进入开发**；`v1.2.0 / T21` 的决策记录见 `docs/v1-structure-upgrade-decision.md`，结论是**当前维护分支不提前推进结构升级**。

## 功能概览

成熟度等级见 `docs/v1-maturity-matrix.md`：`stable` / `experimental` / `reserved` / `demo-only`。

### 网络传输层

- **协议格式（stable）**：`[4字节长度][2字节消息号][4字节请求序号][4字节错误码][1字节标记位][消息体]`
- **批量发包（stable）**：`Session::send_batch()` 用于广播优化
- **零拷贝读包（stable）**：BufferPool 集成
- **写队列反压（stable）**：防止 OOM
- **大包压缩（experimental）**：`flags::kCompressed` 仅在编译期带 zlib 时生效；无 zlib 时为长度前缀透传，标记位语义不稳定（v1.1.2 修正）
- **超大包分片（reserved）**：`packet_fragment` 模块存在但 **`Session` 收发主链未接入**，对外不应宣称"已支持自动分片"
- **TLS 加密（reserved）**：`net::TlsConfig` 配置可解析，但 `GatewayServer` 主链未启用 SSL stream

### 业务服务

- **登录（stable / experimental）**：dev / json_file 两种鉴权 stable；http 鉴权 experimental（同步阻塞实现，不适合高并发生产）；Token TTL 由校验器计算但**不进入 SessionManager**，运行时不主动失效
- **房间（stable）**：创建/加入/离开/准备，房主机制，房间状态广播
- **战斗（stable / experimental）**：起战斗/输入/帧同步/结束/结算 stable；当前 `BattleManager` 是按房间组织的输入收集器，不是完整战斗领域引擎；观战、战斗回放生产链 reserved
- **匹配（experimental）**：`MatchmakingService` 队列匹配，ELO 分差控制
- **管理命令 5001-5005（demo-only）**：`AdminService` 仅在 `examples/admin_demo` / `examples/login_demo` 中手工接线，**默认 `GatewayServer` 不注册这组 handler**；**无令牌/角色 ACL**，不应在公网暴露。**调用前提与最小审计键**：`docs/v1-admin-audit-rules.md`（**v1.1.11**；handler 边界写 **`admin_invoke`**）

### 可观测性

- **Prometheus / JSON 指标导出（stable）**：累计计数器 + 每秒速率仪表盘
- **HTTP 观测端点（`/metrics*` stable；`/health` experimental 存活桩）**：`/metrics` `/metrics/json` 为 **只读**导出；`GET /health` 固定 `{"status":"ok"}`，**不依赖**运行时真实健康；**无任何鉴权**，仅适合内网/受信网络（详见 `docs/v1-governance-layers.md` **§6** — **≠**完整 HTTP 控制面）
- **请求链路追踪 ID（stable）**：Session → Dispatcher → Handler 贯穿
- **审计日志（experimental）**：`logs/audit.log`，**输出格式为"近似 JSON 行"**，`details` 字段未做 JSON 转义，**不应被视为稳定结构化日志**；L3 admin 调用在 **`v1.1.11`** 起于 handler 入口写 **`admin_invoke`**（键约定见 **`docs/v1-admin-audit-rules.md`** §4）
- **崩溃转储（stable）**：Windows SEH + POSIX 信号
- **Grafana 仪表板 / Prometheus 告警规则（stable）**

### 运维能力

- **优雅关闭（stable / reserved）**：`SIGINT`/`SIGTERM` 触发回调 stable；统一 shutdown sequence reserved（由各 example 入口手工编排）
- **配置热加载（experimental）**：`ConfigWatcher` 仅是文件变更触发器，实际生效字段仅 `max_connections` / `per_ip_connection_limit`
- **连接数限制（stable）**：总量 + 单 IP
- **速率限制（stable / reserved）**：连接维度限频接入；消息类型限频与登录防爆破 reserved
- **游客账号（reserved）**：`max_guests` 配置存在，主链未引用

### 工程化

- **CMake + FetchContent + 本地 third_party 内网构建（stable）**
- **Docker + docker-compose + GitHub Actions CI/CD（stable）**
- **测试体系（stable）**：当前 `ctest --preset default -N` 枚举共 **93** 个用例（GoogleTest `gtest_discover_tests` 展开后；其中新增治理 / 生命周期装配 / 持久化·审计·回放回归面。请始终以 `ctest -N` 为准）
- **压测场景（stable）**：`PressureScenario` 枚举共 9 个值（echo / invalid_token / slow_echo / broadcast_storm / malicious_packet / battle_broadcast / chaos / stability / benchmark）
- **多进程入口（experimental）**：`login_server` / `room_server` / `battle_server` 各自启动一份 `GatewayServer + SessionManager + Dispatcher`，**不是完整拆服架构**，更准确的描述是"按模块拆出的独立 demo 入口"

## 快速开始

```powershell
# 构建
cmake --preset windows-msvc-debug
cmake --build --preset windows-msvc-debug
ctest --preset windows-msvc-debug

# 启动网关
./build/windows-msvc-debug/examples/echo/Debug/echo_server.exe config/gateway.json

# `/health` 存活探测桩（固定 ok；不反映业务/就绪健康；见 docs/v1-governance-layers.md §6）
curl http://localhost:9080/health

# 压力测试
./build/windows-msvc-debug/examples/pressure/Debug/gateway_pressure.exe 127.0.0.1 9000 100 10 echo
```

## 发布

- `develop -> main` 通过合并提交保留阶段边界
- `CI`：Linux / macOS 走 `default` preset，Windows 走 `windows-msvc-debug`
- `Release`：tag `v*` 后，GitHub Actions 会在 Linux / macOS / Windows 上构建、测试、安装、打包并发布 release asset
- 发布与归档细则见 `docs/release-process.md`

## 示例程序

| 示例 | 路径 | 类型 | 说明 |
|---|---|---|---|
| echo_server | `examples/echo/` | 集成样例 / 主展示入口 | 完整网关装配，**当前推荐的运行参考** |
| echo_client | `examples/echo/` | 工具 | 基础 Echo 客户端，演示协议收发 |
| login_demo | `examples/login_demo/` | showcase app | 登录流程演示：三种鉴权模式、Token 生命周期、重复登录处理（**`AdminService` 在此手工接线，仅 demo 用途**） |
| room_demo | `examples/room_demo/` | showcase app | 房间系统演示：创建/加入/准备/广播 |
| battle_demo | `examples/battle_demo/` | showcase app | 战斗系统演示：帧同步、输入路由、结算（声明 `JsonFileBattleReplayStore` 但战斗主链不生成 replay） |
| admin_demo | `examples/admin_demo/` | showcase app | 管理工具演示（**`AdminService` 在此手工接线，无权限校验，仅 demo 用途**） |
| gateway_pressure | `examples/pressure/` | 压测工具 | 9 种场景，支持 JSON 配置 |
| login_server | `examples/login/` | 独立入口实验版 | 各自启动一份 `GatewayServer`，**不是拆服架构** |
| room_server | `examples/room/` | 独立入口实验版 | 同上 |
| battle_server | `examples/battle/` | 独立入口实验版 | 同上 |

## 模块架构

```
include/
├── app/          配置、日志、崩溃处理、审计日志、优雅关闭、热加载
├── net/          会话、协议编解码、消息分发、缓冲池、HTTP管理、
│                 速率限制、服务路由、内部总线、TLS、WebSocket
├── game/
│   ├── gateway/  网关服务器、会话管理、推送服务、管理指令
│   ├── login/    登录服务、Token校验、HTTP远程鉴权
│   ├── room/     房间管理、房间服务
│   ├── battle/   战斗管理、战斗服务、回放播放器
│   ├── match/    匹配服务
│   └── persistence/ 玩家数据存储、SQLite后端
src/              各模块实现文件
examples/         示例程序（echo、login_demo、room_demo 等）
tests/            单元 + 集成 + 模糊测试
config/           配置文件（gateway.json、pressure.json 等）
docs/             项目文档（架构规划、开发规范、维护成熟度矩阵等）
```

## 配置文件

完整配置项参见 `config/gateway.json`。**配置字段成熟度（启动生效 / 热更新生效 / 仅预留）见 `docs/v1-maturity-matrix.md` §5.1**。

## 第三方依赖

通过 CMake `FetchContent` 或本地 `third_party/` 目录管理：

- Boost 1.90.0（Asio、Beast）
- fmt 11.2.0
- spdlog 1.15.3
- nlohmann/json 3.12.0
- GoogleTest 1.17.0

内网构建说明参见 `third_party/README.md`。

## 文档导航

- **维护能力可依赖性判断**：`docs/v1-maturity-matrix.md`
- **维护任务与版本批次**：`docs/development-optimization.md` §11
- **运行手册**：`docs/runtime-playbook.md`
- **开发优先级看板**：`docs/development-priority.md`
- **结构升级决策记录（T21）**：`docs/v1-structure-upgrade-decision.md`
- **发布流程与归档说明**：`docs/release-process.md`
- **当前发布说明**：`docs/releases/v1.2.4.md`
- **v2.0.0 路线（v1.2.0 决策点之后）**：`docs/v2-roadmap.md`、`docs/v2-design.md`

## 许可证

MIT
