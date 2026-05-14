# 开发日志模板与阶段记录

## 使用说明

本文件用于记录阶段目标、完成内容、问题、风险和下一步计划。

更新建议：

- 每完成一个较大功能阶段更新一次
- 每次主分支合并前补一条阶段摘要
- 出现较大设计变更时记录设计原因

---

## 阶段记录

### 2026-05-05 阶段一：基础网络脚手架

完成内容：

- 初始化 C++ + CMake 项目脚手架
- 接入 `fmt`、`spdlog`、`GoogleTest`
- 增加 GitHub CI 配置
- 完成 Hello World 示例

产出：

- 基础构建链路可用
- 日志、测试、CI 基础设施具备

问题：

- 初期目录结构较平铺
- 示例与核心源码耦合较重

下一步：

- 引入 Boost.Asio
- 增加异步 echo client/server

### 2026-05-05 阶段二：Boost.Asio 网络能力

完成内容：

- 接入 Boost.Asio
- 完成异步 echo server/client
- 引入 Session 抽象
- 完成长度头协议
- 加入消息号、消息分发器、心跳、超时断开、发包限流、业务线程池

产出：

- 初步具备游戏服务器网络层骨架

问题：

- 示例、业务骨架和核心网络层仍未完全分层
- 测试覆盖不够系统

下一步：

- 结构重构
- 拆 examples
- 补 unit/integration tests

### 2026-05-05 阶段三：工程结构规范化

完成内容：

- 将 echo 示例迁移到 `examples/`
- `src/` 按 `app/net/game` 分层
- 增加 `gateway/login/room/battle` 业务骨架
- 拆分子 CMake
- 补齐 unit/integration tests

产出：

- 工程结构更适合后续长期迭代
- 当前测试已经覆盖协议、分发器、业务注册和基础集成链路

问题：

- 业务逻辑仍是占位实现
- 尚未接入配置系统、监控、压测工具

下一步：

- 增加 SessionManager
- 增加认证中间件
- 完成登录/房间/战斗最小闭环

---

## 开发日志模板

### 日期

`YYYY-MM-DD`

### 阶段名称

例如：

- 登录模块最小闭环
- 房间模块一期
- 压测工具接入

### 目标

- 目标 1
- 目标 2

### 完成内容

- 完成项 1
- 完成项 2

### 影响范围

- 网络层
- 协议层
- 登录模块

### 风险与问题

- 风险 1
- 问题 2

### 测试结果

- 单元测试结果
- 集成测试结果
- 压测结果

### 下一步

- 下一步任务 1
- 下一步任务 2
---

## 2026-05-05 阶段四：网关最小业务闭环

### 目标

- 补齐在线会话管理和登录态管理
- 增加登录前白名单和未登录业务拦截
- 完成登录、房间、战斗最小闭环
- 增加基础观测和压测工具

### 完成内容

- 新增 `SessionManager`，统一管理在线连接、登录态、房间态和战斗态
- 升级 `MessageDispatcher`，支持中间层和线程安全消息注册
- 增加登录前白名单和未登录业务拦截
- 完成登录、加入房间、双人起战斗最小闭环
- 新增 `GatewayMetrics`，统计连接数、包量、字节量和业务成功量
- 新增 `gateway_pressure` 压测工具
- 补齐 `SessionManager` 单元测试和新的网关集成测试

### 影响范围

- `net`
- `game/gateway`
- `game/login`
- `game/room`
- `game/battle`
- `examples/pressure`
- `tests/unit`
- `tests/integration`

### 问题与风险

- 当前房间态和战斗态仍然集中在 `SessionManager`，后续需要拆成独立管理器
- 协议层还没有请求序号、统一错误码和版本号
- 指标目前只做日志打印，还没有接 Prometheus 等外部监控

### 测试结果

- `ctest --preset windows-msvc-debug`
- `15/15` 测试通过
- `gateway_pressure.exe 127.0.0.1 9100 10 3` 本地冒烟通过

### 下一步

- 拆分 `RoomManager` / `BattleManager`
- 增加请求序号和统一响应结构
- 增加广播、踢线、重连恢复和限频中间层

---

## 2026-05-05 阶段五：协议与状态层继续收敛

### 目标

- 将房间态和战斗态从 `SessionManager` 中拆出
- 增加统一 `request_id + error_code` 协议结构
- 增加网关层限频中间层

### 完成内容

- 新增 `RoomManager` 和 `BattleManager`
- `SessionManager` 收敛为连接和认证状态管理
- 网络协议升级为 `[长度][消息号][请求序号][错误码][消息体]`
- 登录、房间、战斗、Echo 链路全部切到统一响应结构
- 新增网关限频中间层
- 新增对应单元测试和限频集成测试

### 影响范围

- `net`
- `game/gateway`
- `game/room`
- `game/battle`
- `examples/echo`
- `examples/pressure`
- `tests/unit`
- `tests/integration`
- `docs`

### 问题与风险

- 当前错误码仍是最小集合，后续需要扩展到完整业务错误码体系
- 房间和战斗管理器已经拆出，但还没有广播、退出和结算能力
- 限频目前按连接做固定窗口统计，后续需要扩展为按消息号和按用户维度限频

### 测试结果

- `ctest --preset windows-msvc-debug`
- `18/18` 测试通过
- `gateway_pressure.exe 127.0.0.1 9101 10 3` 本地冒烟通过

### 下一步

- 完整登录链路
- 房间系统独立化
- 战斗系统独立化

---

## 2026-05-05 阶段六：P1 业务骨架扩展

### 目标

- 从最小登录闭环升级到 token 登录和登录上下文
- 补齐房间创建、离开、准备、房间状态广播
- 补齐战斗输入路由和战斗状态广播

### 完成内容

- 新增 `TokenValidator` 接口和 `DevTokenValidator` 实现
- `SessionManager` 增加登录上下文存储
- `RoomManager` 升级为支持房主、准备状态和房间快照
- `BattleManager` 升级为带 battle context 和输入事件历史
- `RoomService` 支持创建房间、加入房间、离开房间、准备状态和房间广播
- `BattleService` 支持房主起战斗、战斗输入响应和输入广播
- 补齐 token、房间、战斗的单元测试和集成测试

### 影响范围

- `game/login`
- `game/room`
- `game/battle`
- `tests/unit`
- `tests/integration`
- `examples/pressure`
- `docs`

### 问题与风险

- 当前 token 校验仍然是开发态实现，后续需要接真实鉴权服务
- 房间和战斗目前仍是内存态，没有持久化和重连恢复
- 房间广播和战斗广播当前只做最小字符串协议，后续应升级为结构化协议体

### 测试结果

- `ctest --preset windows-msvc-debug`
- `21/21` 测试通过
- `gateway_pressure.exe 127.0.0.1 9102 10 3` 本地冒烟通过

### 下一步

- 广播与推送链路增强
- 重连恢复与踢线
- 配置系统

---

## 2026-05-05 阶段七：P2 基础工程增强

### 目标

- 抽象统一推送组件
- 增强顶号踢线和房间状态恢复
- 把服务端关键参数迁移到配置系统

### 完成内容

- 新增 `PushService`，统一处理成功响应、错误响应和广播推送
- 登录顶号时旧连接会收到 `kSessionKickedPush`
- 新连接会恢复原房间归属，并收到 `kSessionResumedPush`
- `RoomManager` 新增会话迁移能力
- 新增轻量配置模块 `app::config`
- `echo_server` 已支持从 `config/gateway.conf` 加载线程数、端口、心跳和限流相关参数
- 补齐配置测试和重复登录恢复集成测试

### 影响范围

- `app`
- `game/gateway`
- `game/login`
- `game/room`
- `game/battle`
- `examples/echo`
- `tests/unit`
- `tests/integration`
- `docs`

### 问题与风险

- 当前配置格式仍是简单 `key=value`，后续可升级到更强的结构化配置
- 会话恢复目前只恢复房间归属和战斗状态标记，没有恢复更深层业务上下文
- 广播推送目前仍是字符串体，后续建议改为结构化协议体

### 测试结果

- `ctest --preset windows-msvc-debug`
- `23/23` 测试通过
- `echo_server.exe config/gateway.conf 9103` + `gateway_pressure.exe 127.0.0.1 9103 10 3` 冒烟通过

### 下一步

- 可观测性升级
- 压测体系扩展
- 真实鉴权服务与后端集成

---

## 2026-05-05 阶段八：P3 基础观测与外部鉴权接入

### 目标

- 为网关补齐可落地的 Prometheus / JSON 指标导出
- 把压测工具升级为可配置、多场景的版本
- 把鉴权从纯开发态实现推进到可切换的外部数据后端

### 完成内容

- 接入 `nlohmann/json`
- `app::config` 支持同时解析 `key=value` 和 JSON 配置文件
- 新增 `config/gateway.json`、`config/pressure.json` 和 `config/auth_users.json`
- 新增 `GatewayMetricsExporter`，支持 Prometheus 文本和 JSON 快照导出
- `GatewayServer` 已支持按周期把指标写入文件
- 新增 `JsonFileTokenValidator`，可从外部 JSON 用户数据加载 token 和显示名
- `echo_server` 已支持按配置切换 `dev` / `json_file` 鉴权提供方
- `gateway_pressure` 已支持 JSON 压测配置，以及 `echo`、`invalid_token`、`slow_echo` 三种场景
- 补齐 JSON 配置、JSON 鉴权和 metrics 导出的单元测试

### 影响范围

- `cmake`
- `app`
- `game/gateway`
- `game/login`
- `examples/echo`
- `examples/pressure`
- `config`
- `tests/unit`
- `docs`

### 风险与问题

- 当前指标导出仍是文件方式，尚未暴露 HTTP `/metrics`
- `JsonFileTokenValidator` 适合开发和本地集成，不等同于真实远程账号服务
- 压测场景已经支持慢客户端和非法 token，但广播风暴场景仍需结合更完整的房间/战斗广播继续扩展

### 测试结果

- `cmake --build --preset windows-msvc-debug --parallel`
- `ctest --preset windows-msvc-debug --output-on-failure`
- `28/28` 测试通过
- `echo_server.exe config/gateway.json 9104` + `gateway_pressure.exe config/pressure.json 9104` 冒烟通过
- `echo_server.exe config/gateway.json 9105` + `gateway_pressure.exe 127.0.0.1 9105 10 1 invalid_token` 冒烟通过

### 下一步

- 增加 HTTP `/metrics` 或管理端口
- 增加广播风暴、战斗广播和恶意包压测
- 把 `JsonFileTokenValidator` 替换为真实远程账号服务客户端

---

## 2026-05-05 阶段九：HTTP 管理端点

### 目标

- 为网关增加 HTTP 管理端口，暴露健康检查和指标查询接口

### 完成内容

- 新增 `net::HttpManager`，基于 Boost.Beast 实现轻量 HTTP 服务
- 提供 `GET /health`、`GET /metrics`（Prometheus 文本）、`GET /metrics/json` 三个端点
- `GatewayServer` 集成 `HttpManager` 生命周期管理
- 新增 `gateway.http_management_port` 配置项（默认 9080，设为 0 禁用）
- 新增 `HttpManagementTest` 集成测试（4 个用例）

### 影响范围

- `net`
- `game/gateway`
- `examples/echo`
- `config`
- `tests/integration`
- `docs`

### 测试结果

- `cmake --build --preset windows-msvc-debug --parallel`
- `ctest --preset windows-msvc-debug --output-on-failure`
- `32/32` 测试通过
- `curl http://127.0.0.1:9080/health` → `{"status":"ok"}`
- `curl http://127.0.0.1:9080/metrics` → Prometheus 格式指标正常

### 下一步

- 增加广播风暴、战斗广播和恶意包压测
- 把 `JsonFileTokenValidator` 替换为真实远程账号服务客户端
- 增加 Docker 部署与 CI 扩展

---

## 2026-05-05 阶段十：P4 压测场景 + 远程鉴权 + Docker

### 目标

- 补齐压测场景矩阵（广播风暴、恶意包、战斗广播）
- 实现 HTTP 远程鉴权客户端
- 完成 Docker 镜像构建与 CI 集成

### 完成内容

- 新增 `broadcast_storm`、`malicious_packet`、`battle_broadcast` 三种压测场景
- `gateway_pressure` 支持 6 种场景，基于共享计数器协调房间型场景退出
- 新增 `HttpTokenValidator`，通过 HTTP POST 调用外部鉴权服务
- `echo_server` 支持 `dev` / `json_file` / `http` 三种鉴权提供方
- 新增 `Dockerfile`（多阶段构建）、`docker-compose.yml`、`.dockerignore`
- GitHub Actions CI 扩展 Docker 构建 + 容器冒烟测试 Job

### 测试结果

- `34/34` 测试通过

### 下一步

- 结构化序列化、链路追踪、协议标记位
- Buffer 池、广播批量发包、慢连接检测

---

## 2026-05-05 阶段十一：P5 基础设施深化

### 目标

- 实现轻量二进制序列化替代裸字符串协议体
- 补齐请求链路追踪 ID
- 协议增加 flags 标记位
- 引入 Buffer 池和批量发送
- 增加慢连接检测

### 完成内容

- 新增 `net::msg` 命名空间，18 种结构化消息类型 + 二进制序列化
- 新增 `net::message_serializer.h`，LE 编码的轻量序列化反序列化
- 协议格式升级为 `[4B长度][2B msg][4B req][4B err][1B flags][body]`
- `DispatchContext` 增加 `trace_id` → Session → Dispatcher → 日志贯穿
- 新增 `ObjectPool<T>` + `BufferPool`，Session 读包路径集成复用
- `Session::send_batch()` 一次 `async_write` 发送多包
- 写队列积压 > 50% 上限时 WARN 日志

### 测试结果

- `34/34` 测试通过

### 下一步

- per-second 速率指标、零拷贝、崩溃转储
- 服务拆分路由、战斗帧同步

---

## 2026-05-05 阶段十二：P6 生产加固 + 战斗深化

### 目标

- 补齐 per-second 速率指标
- 实现零拷贝读包路径
- 增加崩溃转储处理
- 建立服务拆分路由基础
- 实现战斗帧同步

### 完成内容

- `GatewayMetricsRateSnapshot` + `compute_rates()` 计算每秒速率
- Prometheus 增加 6 种 `_rate` gauge，JSON 增加 `_per_sec` 字段
- `GatewayServer` 追踪上一快照时间戳，日志输出 `pkts_recv/s`
- `BufferPool` 集成到 Session 读包路径，`acquire_vector/release_vector`
- `app::crash::install_crash_handler()` — Windows SEH + POSIX signal
- 崩溃报告写入 `runtime/crashes/crash_*.txt`
- `ServiceRouter` + `ServiceId`（Gateway/Login/Room/Battle）
- `BattleManager::advance_frame()` + 输入按帧分桶

### 测试结果

- `34/34` 测试通过

### 下一步

- 连接限制、线程池拆分、战斗结算、Handler 自动注册

---

## 2026-05-05 阶段十三：P7 生产安全 + 架构收敛

### 目标

- 实现连接数限制和 per-IP 限制
- 按消息 ID 范围拆分业务线程池
- 完成战斗结算逻辑
- 提供 Handler 注册辅助工具

### 完成内容

- `GatewayAppConfig` 增加 `max_connections` / `per_ip_connection_limit`
- `GatewayServer::set_connection_limits()` + 超限拒绝
- `MessageDispatcher::set_thread_pool(min, max, pool)` 按 ID 范围路由
- `BattleManager::end_battle()` → `BattleResult{winner, scores, frames}`
- `HandlerRegistry` 链式 `.add(id, handler).register_all(dispatcher)`

### 测试结果

- `34/34` 测试通过

### 下一步

- 多进程拆分实战（独立 login/room/battle 可执行文件）
- 内部消息总线 / RPC 传输层
- 服务发现与健康检查
- protobuf/flatbuffers 跨语言协作评估
- 持久化层（战斗回放落盘、玩家数据）

---

## 2026-05-06 阶段 v1.1.1：基线校准

### 目标

将仓库从"v1.0.0 发布版 + develop 持续叠加能力"的混合状态，校准为一份"单一事实源、成熟度表述准确"的维护期基线。**对应 `docs/development-optimization.md` §11 任务表的 T01 / T02 / T10 / T12 / T14**。本阶段为**纯文档基线校准**，不修改任何主链协议 / 业务 / 运行时行为。

### 完成内容

- 新增 `docs/v1-maturity-matrix.md` — `v1.x` 维护期能力成熟度**单一事实源**：
  - 网络与协议层、业务层、治理与控制面、配置与运行时装配、持久化与回放、可观测性、工程能力，每项标注 `stable` / `experimental` / `reserved` / `demo-only`
  - `GatewayAppConfig` 字段表（启动生效 / 热更新生效 / 主链接入）
  - "不应被宣称为完成态的能力（汇总）"清单
  - `v1.x` 维护版本节奏表
- `README.md`：
  - 新增"版本基线说明"区分 `v1.0.0` 发布版（tag `v1.0.0`，commit `22defe8`）与 `develop` 维护期
  - 全量补成熟度标记，纠正自动分片、TLS 接入主链、Token 生命周期失效、登录防爆破、游客账号、完整热更新、完整管理面、多进程拆服等过度承诺
- `CHANGELOG.md`：新增 `v1.1.1` 条目；为 `v1.0.0` 段补"维护期补注"
- `docs/runtime-playbook.md`：
  - 第 1 节按成熟度分级重写
  - 新增第 2 节"当前协议事实源"，明确字符串 body 是 `v1.x` 唯一线上协议主契约，`net::msg` / `message_serializer` 为草案
  - 第 3 节注明中间层当前在业务线程池**之后**执行（`v1.1.3` 前置）
  - 第 6 节列出已知测试覆盖盲点
  - 第 7 节注明字节量指标统计的是业务 body，不是 wire bytes；`/health` 当前为固定字符串
- `docs/development-priority.md`：从"扩展/优化/测试方向"散列优先级，重写为按 `development-optimization.md` §11 任务表 + 版本批次（A 基线 / B 主链 / C 测试 / D 升级决策）组织
- `docs/README.md`：重组文档导航，按"v1.x 维护期 / v2.0.0 路线"分组

### 影响范围

- `README.md`
- `CHANGELOG.md`
- `docs/v1-maturity-matrix.md`（新）
- `docs/runtime-playbook.md`
- `docs/development-priority.md`
- `docs/development-log.md`
- `docs/README.md`

### 风险与问题

- 本版本不修改任何代码或测试，所有现有测试用例保持通过
- 部分历史文档（`docs/architecture-roadmap.md`）仍保留过往措辞，作为历史路线参考；与维护期实际能力冲突时以矩阵为准

### 测试结果

- 不涉及代码改动，沿用 v1.0.0 测试基线（实际 ctest 用例数以 `ctest -N` 为准）

### 下一步

- `v1.1.2`：T03（统一 `Session::stop()` 与 `handle_close()` 收口）+ T04（固定协议增强顺序、明确分片当前未启用、修正无 zlib 时 `kCompressed` 标记位语义）
- `v1.1.3`：T05（ingress 鉴权白名单 / 限频前置）
- `v1.1.4`：T06（`battle_started` 单一事实源第一阶段）

> **强约束**：在 `v1.2.0` 决策点之前不进入 v2.0.0 范畴（Actor / ECS / 集群路由 / 状态生命周期系统 / 控制面）。

---

## 2026-05-06 阶段 v1.1.2：主链生命周期与协议增强收口

### 目标

收敛主链生命周期与协议增强路径，让 `Session` 的关闭路径只有一条收口，让协议增强标志位的语义跨 build 自洽。**对应 `docs/development-optimization.md` §11 任务表的 T03 / T04**。本阶段允许动主链代码，但不引入新功能，不修改业务协议，不动配置/治理结构。

### 完成内容

- T03：统一 `Session` 关闭路径
  - `src/net/session.cpp`：`Session::stop()` 不再裸调 `socket_.shutdown/close`，改为 `asio::post(strand_, [self]{ self->handle_close(asio::error::operation_aborted); })`，让主动关闭与心跳超时 / 网络异常 / 写队列溢出 / 包非法等异常关闭走同一条 `handle_close()` 路径。
  - 后果：`close_handler_` 在主动 `stop()` 时也会被触发，`SessionManager / RoomManager / GatewayMetrics` 的引用计数与登记不再随关闭来源不同而漂移。
  - 反复 `stop()` 仍幂等：`handle_close()` 内部的 `if (stopped_) return;` 仍是单一事实源。

- T04：固定协议增强顺序 + 修正压缩标志位语义
  - `include/net/packet_compressor.h`：新增 `inline constexpr bool is_compression_available() noexcept`，根据 `HAS_ZLIB` 静态返回真实压缩后端的存在性。
  - `src/net/session.cpp`：
    - 出站（`send_batch` / `enqueue_write`）固定为 `serialize body -> compress (only when zlib available) -> encode`；只在 `is_compression_available()` 为真时才设置 `packet::flags::kCompressed`。
    - 入站（`do_read_body`）固定为 `decode -> decompress (only when zlib available) -> dispatch`；当对端把 `kCompressed` 发给一个 *没有压缩后端* 的 build 时，直接以 `invalid_argument` 关闭连接，避免 fallback `decompress_body`（长度前缀透传）把上层语义弄错。
    - 分片（`packet::flags::kFragment*`）在 v1.x 维护期内仍为 `reserved`：`Session` 主链既不分片也不组帧，相关注释指向 `docs/v1-maturity-matrix.md` §2.3 与 `development-optimization.md` §6.A / §8.3。

### 测试

- 单元（新）`tests/unit/session_close_test.cpp`：
  - `StopFiresCloseHandlerExactlyOnce`
  - `MultipleStopCallsAreIdempotent`
  - `StopReceivesOperationAbortedReason`
  - `StopWithoutCloseHandlerIsSafe`
- 单元（新增用例）`tests/unit/compressor_test.cpp::IsCompressionAvailableMatchesBuildBackend`
- 集成（新）`tests/integration/gateway_integration_test.cpp::CompressedFlagWithoutBackendIsRejected` — 在没有压缩后端的 build 下，发送伪造 `kCompressed` 包必须导致服务端关闭连接；有压缩后端的 build 自动 `GTEST_SKIP`。
- 全量测试：`ctest`：60/60 通过（v1.1.1 基线 54 + v1.1.2 新增 6）。

### 影响范围

- `include/net/packet_compressor.h`
- `src/net/session.cpp`
- `tests/unit/CMakeLists.txt`
- `tests/unit/session_close_test.cpp`（新）
- `tests/unit/compressor_test.cpp`
- `tests/integration/gateway_integration_test.cpp`

### 风险与问题

- 现有调用 `replaced_session->stop()`（`src/game/login/login_service.cpp`）的代码路径，在本版本生效后会触发 `close_handler_`，进而经过 `gateway_server.cpp` 的 close 闭环执行 `session_manager_.remove_session` / `metrics_.on_session_closed()` / `active_connection_count_-=1`。这条链路本来就是“被踢号在断链时该走的清理”，v1.0.0 因为 `stop()` 绕开了 close_handler 而漏减——本版本顺带修复了这条隐性引用计数漂移。集成测试 `DuplicateLoginKicksOldSessionAndResumesRoomState` 通过，行为符合预期。
- 没有压缩后端的 build 上，`kCompressed` 现在是“严格非法”信号；任何在 `v1.0.0` 主链外私接的旧客户端如果在没有 zlib 的 build 上误设了 `kCompressed`，将会被立即断开。这与 `docs/v1-maturity-matrix.md` 的现行约束一致——v1.x 主链就不依赖压缩。
- 分片仍未启用，`v1.1.2` 不动它；v1.x 维护期不计划解锁。

### 下一步（阶段结束时原计划）

- `v1.1.3`：T05 — ingress 鉴权白名单 / 限频前置（已实现，见下文 v1.1.3 段）
- `v1.1.4`：T06 — `battle_started` 单一事实源第一阶段

---

## 2026-05-06 阶段 v1.1.3：入口治理前置（T05）

### 目标

把网关入口策略（登录前消息白名单、未鉴权拦截、连接维基础限频）从 **`asio::post` 到业务线程池之后的中间件执行**前移为：**在投递到业务池之前**，于 `Session::packet_handler` → `MessageDispatcher::dispatch()` 的同一线程上下文（实际是 `strand`/`io_context` 上的同步调用栈）先行判定。

**对应 `docs/development-optimization.md` §11 任务表的 T05 / §8.4**。本阶段不改变业务协议与白名单所列消息号集合，只改变执行位置与资源占用特征。

### 完成内容

- `include/net/message_dispatcher.h`：
  - 新增 `register_ingress_middleware` + `ingress_middleware_count()`。
  - `dispatch()`：若 `session != nullptr`，在 `boost::asio::post(*target_pool, …)` **之前**顺序执行 ingress 链；若任一层返回 `false`，立即 `return true`（与旧版“被中间件拦下”一致），**不向业务池投递**。
  - `session == nullptr` 时**跳过** ingress（为实验性 `InternalBus` 预留：避免把“客户端会话级策略”误用到 `dispatch(nullptr, …)` 内部链路上；post-pool 的 `register_middleware` 链仍保留）。

- `src/game/gateway/gateway_service.cpp`：`auth_whitelist` 与 `rate_limit` 从 `register_middleware` 迁到 `register_ingress_middleware`。

### 测试

- `tests/unit/message_dispatcher_test.cpp`：新增 `IngressMiddlewareRunsSynchronouslyBeforeBusinessPool`、`IngressSkippedWhenSessionIsNull_InternalBusStyle`；保留原 `MiddlewareCanBlockMessageBeforeHandlerRuns` 覆盖 **post-pool** 链行为。
- `tests/unit/service_registration_test.cpp`：断言 `ingress_middleware_count()==2`、`middleware_count()==0`。
- `ctest`：62/62 通过。

### 影响范围

- `include/net/message_dispatcher.h`
- `src/game/gateway/gateway_service.cpp`
- `tests/unit/message_dispatcher_test.cpp`
- `tests/unit/service_registration_test.cpp`
- `docs/runtime-playbook.md` §3、`docs/v1-maturity-matrix.md` §2.4、`docs/development-priority.md`、`CHANGELOG.md`

### 下一步（阶段结束时原计划）

- `v1.1.5` — 业务事实源校准（文档）

> **强约束**：v1.x 维护期；未进入 v2.0.0 范畴。


---

## 2026-05-06 阶段 v1.1.4：`battle_started` 单一事实源（T06 第一阶段）

### 目标

消除 `RoomManager::RoomState::battle_started` 与 `BattleManager::active_battles_` 的并行双写。**以 `BattleManager` 为准**（`start_battle` 成功即有战斗上下文），房间层仅保留通过查询函数注入的派生视图。

对应 `development-optimization.md` §11 **T06**（第一阶段）。

### 完成内容

- `RoomManager`：移除 `battle_started` 成员、`mark_battle_started`；新增 `set_battle_active_query`；`snapshot` / `join_room` / `set_ready` 的 `kRoomInBattle` / `battle_started` 字段均委派 query。
- `BattleService`：删除成功后对 `room_manager_.mark_battle_started` 的调用。
- 同时具备 `RoomManager` 与 `BattleManager` 的入口：`set_battle_active_query([&battle](const std::string& id){ return battle.battle_started(id); });`
- `LoginService`/`session_migration` 依赖的 `RoomSnapshot::battle_started` **语义不变**，数据源已与 `BattleManager` 对齐。

### 测试

- `tests/unit/room_manager_test.cpp::JoinAndReadyRejectedWhenBattleManagerMarksRoomInBattle`
- `ctest`：63/63

### T06 后续（v1.1.8 / T09 候选）

- 战斗中会话迁移、`transfer_session`、空房移除与战斗清理的更严顺序。

> **强约束**：v1.x。

---

## 2026-05-06 阶段 v1.1.5：业务事实源校准（叙事文档）

### 目标

落实 `development-optimization.md`「第一步」中文档层面的验收：**能清楚回答**登录成功是否等于恢复完成、房间成员是按 session 还是身份、battle 是独立实体还是附属、`battle_started` 以何处为准。**不修改代码**，与 **`v1.1.4`**（`battle_started` SSOT）配套成文。

### 完成内容

- 新增 **`docs/v1-business-fact-source.md`**。
- **`docs/README.md`**、`docs/v1-maturity-matrix.md` §3、`development-priority.md`、`runtime-playbook.md`、`CHANGELOG.md` 同步指针。

### 测试结果

- 纯文档校准，沿用 **`v1.1.4` 代码基线与 ctest（63）**。

### 下一步

- **`v1.1.6`**：`development-optimization.md` 第二步——业务协议表冻结（含错误码语义失真项）。

> **强约束**：未进入 v2；未改 `docs/v2-roadmap.md`。

---

## 2026-05-06 阶段 v1.1.6：业务协议冻结（T02 后半）

### 目标

落实 **`development-optimization.md` §11 T02** 在 **`v1.1.6`** 的收口：login / room / battle **真实字符串协议**可单点查阅；**错误码与语义一致**（`player_not_in_battle` 不再伪装成 `auth_required`）。

### 完成内容

- **`docs/v1-string-protocol.md`**（冻结表 + `net::msg` 分叉）。
- `include/net/protocol.h`：`kPlayerNotInBattle`；`src/game/battle/battle_service.cpp` 映射修正。
- `tests/unit/battle_manager_test.cpp`：`SubmitInputUnknownPlayerReturnsNotInBattle`。
- `docs/runtime-playbook.md`、`v1-maturity-matrix.md`、`development-priority.md`、`docs/README.md`、`CHANGELOG.md` 同步。

### 测试结果

- `ctest`：**64/64**。

### 下一步

- **`v1.1.7`**：跨域编排收口（T07 / T08）。

> **强约束**：v1.x。

---

## 2026-05-06 阶段 v1.1.7：跨域编排收口（T07 / T08）

### 目标

落实 **`development-optimization.md` §11** 的 **T07 / T08** 与§「第三步」叙述：顶号恢复链、空房 battle 清理链有**统一策略函数**与 **`docs/v1-cross-domain-flows.md`**，避免 `GatewayServer` 与 `RoomService` 各维护一份「空房删战斗」逻辑。

### 完成内容

- `include/game/login/login_recovery.h`、`src/game/login/login_recovery.cpp`；`include/game/room/room_battle_lifecycle.h`、`src/game/room/room_battle_lifecycle.cpp`。
- `LoginService`、`GatewayServer`、`RoomService` 接线；`src/game/login/CMakeLists.txt`、`src/game/room/CMakeLists.txt`。
- **`docs/v1-cross-domain-flows.md`**；`docs/README.md`、`v1-string-protocol.md` 尾部、`development-priority.md`、`v1-maturity-matrix.md`、`runtime-playbook.md`、`CHANGELOG.md`。
- **`tests/unit/room_battle_lifecycle_test.cpp`**。

### 测试结果

- `ctest`：**65/65**。

### 下一步

- **`v1.1.8`**：房间态与战斗态边界收紧（T09 等）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.8：房间/战斗边界收紧（T09 + T06②）

### 目标

落实 **`development-optimization.md`** §「第四步」与 §11 **T06**（第二阶段）/ **T09**：缩小房战耦合的模糊地带；`room_state` / 开战 **`player_ids`** 尽量**不依赖**广播时回查 `SessionManager`；**明确 `transfer_session` 在战斗中的合法性**（以 `user_id` 为战斗键）。

### 完成内容

- `RoomMember.member_user_id`、`RoomManager::set_member_user_id`；`RoomService` create/join 后回填；`transfer_session` 随成员迁缓存。
- `RoomService::build_room_state_body`、`BattleService` 开战收集 **优先** `member_user_id`。
- **`docs/v1-room-battle-boundary.md`**；`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`README.md`、矩阵 §3.2、`development-priority.md`、`runtime-playbook.md`、`CHANGELOG.md`。
- **`RoomManagerTest.TransferSessionPreservesMemberUserId`**。

### 测试结果

- `ctest`：**66/66**（在 `v1.1.7` 基线 +1）。

### 下一步

- **`v1.1.9`**：治理入口分层（T10）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.9：治理入口分层（T10）

### 目标

落实 **`development-optimization.md` §11 T10** 在 **`v1.1.9`** 的收口：把 **TCP ingress**、**业务 handler**、**HTTP 管理面**、**二进制 admin** 与 **`GatewayServer` 装配**的职责写清，避免「健康检查 / 指标 / 管理命令」混为一谈；**权限与审计仍留给 v1.1.11（T11）**。

### 完成内容

- **`docs/v1-governance-layers.md`**（L0–L3、路径归类、Admin demo-only 定位、版本边界）。
- `docs/README.md`、`v1-maturity-matrix.md` §4 引言与 `GET /health` 行、`development-priority.md`、`runtime-playbook.md` §10、`v1-string-protocol.md` / `v1-cross-domain-flows.md` 后续版本指针、`CHANGELOG.md`。

### 测试结果

- `ctest`：**66/66**（无代码行为变更）。

### 下一步

- **`v1.1.10`**：治理成熟度冻结（文档与示例用语，不暗示未收口能力已可依赖）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.10：治理成熟度冻结

### 目标

落实 **`development-optimization.md`** 路线图**第二步**：停止「协议号已存在、example 已演示、就被默认为正式能力」的扩散；与 **`v1-maturity-matrix.md` §4** 一致的表述写进 **`docs/v1-governance-layers.md` §6**，并校准 **README / playbook / showcase 示例**。

### 完成内容

- **`docs/v1-governance-layers.md` §6**；矩阵 §4 引言、§10 版本表 **`v1.1.10` 当前**；`development-priority.md`（新增维护行 + 节奏）；`docs/README.md`；`v1-string-protocol.md` / `v1-cross-domain-flows.md`；`runtime-playbook.md`；根 `README.md`（含 ctest **66** 与 HTTP 用词）；`examples/admin_demo`、`examples/login_demo`；`admin_service.h` 注释；`CHANGELOG.md`。

### 测试结果

- `ctest`：**66/66**。

### 下一步

- **`v1.1.11`**：T11 — admin **调用前提与最小审计**。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.11：二进制 Admin 最小规则（T11）

### 目标

落实 **`development-optimization.md`** 路线图**第三步**：为 L3 admin 补齐 **调用前提**、**动作语义与副作用**、**审计最少记什么** 的单一事实源；**不**在本版引入令牌/角色运行时 ACL，**不**拆分 `kAdminResponse` 失败码。

### 完成内容

- **`docs/v1-admin-audit-rules.md`**。
- **`src/game/gateway/admin_service.cpp`**：`register_handlers` 实现；handler 入口 **`AUDIT_LOG("admin_invoke", …)`**（必备键见文档 §4）。
- **`include/game/gateway/admin_service.h`**：声明迁移；`project_game` CMake 纳入 `admin_service.cpp`。
- **`examples/admin_demo`**：去除与边界重复的 `kick`/`ban` 行内 `AUDIT_LOG`。
- 矩阵 §4.2 / §4.4、§9 第 7 条、§10；`development-priority.md`；`docs/README.md`；根 `README.md`；`runtime-playbook.md`；`v1-governance-layers.md`；`v1-string-protocol.md`；`v1-cross-domain-flows.md`；`CHANGELOG.md`。

### 测试结果

- `ctest`：**66/66**（条目不增；行为：admin handler 多写审计行）。

### 下一步

- **`v1.1.12`**：配置字段成熟度单列文档（T12）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.12：配置字段成熟度（T12）

### 目标

落实 **T12**：把 **`GatewayAppConfig`** 的启动/热更新/预留口径从矩阵 **抽成可读运维文档**，矩阵 §5.1 保留锚点并指向 **`docs/v1-config-maturity.md`**；明确 **`ConfigWatcher`** 当前**不是**完整热更新框架。

### 完成内容

- **`docs/v1-config-maturity.md`**；`docs/v1-maturity-matrix.md` §5.1 / §10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`CHANGELOG.md`。
- **`docs/v2-design.md`**：**入库**（v2 草案，决策点前不实施）。

### 测试结果

- `ctest`：**66/66**（文档与仓库整理；无逻辑变更）。

### 下一步

- **`v1.1.13`**：T13 — 标准启动 / reload / shutdown 顺序（见后续阶段记录）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.13：标准装配与 shutdown 退出（T13）

### 目标

落实 **T13** 路线图第二步：**统一描述** showcase 入口的 bootstrap / reload / shutdown，并修正此前信号触发后 **`io_context.run()`** 可能**永不返回**的问题（缺 **`io_context.stop()`**）。

### 完成内容

- **`docs/v1-runtime-lifecycle.md`**；矩阵 §5 引言、§5.3、§10；`docs/README.md`、`docs/v1-config-maturity.md` §6、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`CHANGELOG.md`。
- **`examples/echo/server_main.cpp`**、`login_demo`、`admin_demo`：`GracefulShutdown` 回调内 **`watcher.stop()`**、**`server.stop()`**、**`io_context.stop()`**（或 `io.stop()`）；`login_demo` / `admin_demo` 主线程末尾 **`watcher.stop()`** 幂等收尾。

### 测试结果

- `ctest`：**66/66**。

### 下一步

- **`v1.1.14`**：T13 后半 — 受控 reload / shutdown **语义**（见后续阶段记录）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.14：受控 reload 与 shutdown 语义分界（T13 后半）

### 目标

**reload**：配置文件变更时，**仅当**磁盘可读且 JSON 解析成功才触发 **`on_reload_`**，避免失败路径误用默认 **`GatewayAppConfig`**。**shutdown**：在文档中固定 showcase **最小保证**与仍为 **reserved** 的能力分界。

### 完成内容

- **代码**：`try_load_gateway_config`、`fill_gateway_from_store`；**`ConfigWatcher::check_and_reload`** 用 **`try_load_gateway_config`**；`load_gateway_config` 失败日志；**`config_test`** 用例。
- **文档**：**`docs/v1-runtime-lifecycle.md`**（§5–§8 顺序；**§6** reload 表；**§7** shutdown 表）；矩阵 §5.2 / §5.3 / §10；`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §6、`docs/README.md`、`CHANGELOG.md`。

### 测试结果

- `ctest`：**68/68**。

### 下一步

- **`v1.1.15`**：T14 — 横切能力定位（见后续阶段记录）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.15：横切能力定位文档（T14）

### 目标

在矩阵 **§6** / **§4.4** 之外，提供单一可读叙事：**player store**、**battle replay**、**`AUDIT_LOG`** 各在哪些生命周期节点 **实际接线**，避免与 **v1.1.16（T15）**「按节点收口动作」混淆。

### 完成内容

- **新增** **`docs/v1-cross-cutting-capabilities.md`**；矩阵 §6 引言、§4.4 指针、§10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、`CHANGELOG.md`。

### 测试结果

- **无代码变更**；`ctest` **68/68**（冒烟）。

### 下一步

- **`v1.1.16`**：**T15** — 横切动作生命周期绑定规范（见后续阶段记录）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.16：横切动作生命周期绑定规范（T15）

### 目标

在 **T14 事实矩阵** 之上，给出维护期 **应收口**：具名节点 **N1–N7** 与 **审计 / player store / replay 产出** 的规范对照，并列出 showcase **收敛自检**，避免「能力与节点」长期仅用示例口口相传。

### 完成内容

- **新增** **`docs/v1-cross-cutting-lifecycle-binding.md`**；更新 **`docs/v1-cross-cutting-capabilities.md`** 与 roadmap 指针；**`docs/v1-runtime-lifecycle.md`** §1；矩阵 §6 引言、§10；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、`CHANGELOG.md`。

### 测试结果

- **无代码变更**；`ctest` **68/68**（冒烟）。

### 下一步

- **`v1.1.17`**：**T16** — 横切数据格式文档（见后续阶段记录）。

> **强约束**：未进入 v2。

---

## 2026-05-06 阶段 v1.1.17：横切数据格式与支持级别（T16）

### 目标

把 **player JSON / 条件 SQLite / replay 文件与 `ReplayPlayer` JSON 契约 / `AUDIT_LOG` 行格式** 写成 **叙述层冻结**，与 **`player_store.h`**、**`audit_log.h`**、**`replay_player.h`** 对齐，降低「头文件即协议」误读。

### 完成内容

- **新增** **`docs/v1-cross-cutting-data-formats.md`**；矩阵 §6 引言、§4.4、§10；**`docs/v1-runtime-lifecycle.md`** §1；`docs/README.md`、`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`v1-cross-domain-flows.md`、`v1-config-maturity.md` §5、横切 **§5 / §6** 指针、`CHANGELOG.md`。

### 测试结果

- **无代码变更**；`ctest` **68/68**（冒烟）。

### 下一步

- **`v1.2.0`**：**T21** — 结构升级 **决策点**（是否推进 typed protocol / internal bus / battle replay 闭环等）；**非默认实施**。
- **`v1.2.1`–`v1.2.4`**：**T17–T20** — 各主线 **边界测试加固**（业务 / 治理 / 生命周期装配 / 持久化·审计·回放均已完成，见后续阶段记录）。

> **强约束**：未进入 v2。

---

## 2026-05-07 阶段 v1.2.1：业务边界测试加固（T17）

### 目标

为 **login / room / battle** 主链补齐 **错误路径与状态边界** 的自动化回归，贴近 **`development-optimization.md` §11 T17**（依赖 T06–T09 已收口）。

### 完成内容

- **单元**：**`BattleManager`** — 未开战输入、`start_battle` 人数 / 空 `room_id`、`end_battle` 清理；**`RoomManager`** — 加入不存在房间、空 room id、重复创建。
- **集成**：非房主 **`kBattleStartRequest`**、未全员 ready、单人 **`kNotEnoughPlayers`**、未开战 **`kBattleInputRequest`**；**`read_until_message`** 处理 **`kRoomStatePush`** 与 **`kRoomReadyResponse`** 异步交错。
- **文档**：矩阵 §8 / §10；`development-priority.md`、`runtime-playbook.md`、`v1-string-protocol.md`、`CHANGELOG.md`。

### 测试结果

- `ctest`：**81/81**。

### 下一步

- **`v1.2.2`**：**T18** — 治理边界测试加固。

> **强约束**：未进入 v2。

---

## 2026-05-07 阶段 v1.2.4：治理/生命周期/横切测试收口 + T21 决策记录

### 目标

在 `T17` 之后继续补齐 **治理边界**、**生命周期与装配**、**持久化 / 审计 / 回放** 的回归护栏，并完成 `T21` 决策记录；要求严格停留在 `v1.x` 维护边界内，不把 `typed protocol` / `internal bus` / `battle replay` 提前推进成正式能力。

### 完成内容

- **T18 / 治理边界**
  - `admin_service_test.cpp`：dispatch callback、`admin_invoke` 最小审计键、payload excerpt 清洗。
  - `http_management_test.cpp`：`/health` 固定 JSON、未知路径 `404`、非 `GET` 请求 `405`。
  - `gateway_integration_test.cpp`：默认 runtime 不注册 admin handler。
- **T19 / 生命周期与装配**
  - **新增** `lifecycle_assembly_test.cpp`：`ConfigWatcher` 对坏配置不回调、好配置才回调；`watcher.stop()` 后不再 reload；`GatewayServer::stop()` 关闭连接并清空房间态。
- **T20 / 持久化·审计·回放**
  - **新增** `persistence_replay_audit_test.cpp`：`JsonFilePlayerStore`、条件 `SqlitePlayerStore`、`JsonFileBattleReplayStore`、`ReplayPlayer`、`AUDIT_LOG` 行。
- **T21 / 决策记录**
  - **新增** `docs/v1-structure-upgrade-decision.md`：结论是**当前维护分支不推进** typed protocol / internal bus / battle replay 转正，继续维持 `v1.x` 维护收束边界。
- 同步 `docs/README.md`、`docs/development-priority.md`、`CHANGELOG.md`。

### 测试结果

- 已完成本地构建与验证：
  - `cmake --preset default`
  - `cmake --build --preset default -j 4`
  - `ctest --preset default`
- `ctest -N --preset default` 当前枚举 **93** 项。
- 依赖真实本地监听端口的集成测试在当前受限环境下会 **skip**，其余回归面已通过。

### 下一步

- 若后续继续推进，仅允许在新的明确批次中重新评估结构升级，不在当前维护收束分支内直接落代码。

> **强约束**：未进入 v2。

---

## 2026-05-08 阶段 v1.2.5：CI / Docker / 发布链路稳定性修复

### 目标

统一当前版本口径，补齐 `v1.2.4` 收束后的发布稳定性问题，避免把基础设施修复误写成新的业务版本阶段。

### 完成内容

- 将仓库当前版本口径统一到 `v1.2.5`
- 补充 `docs/releases/v1.2.5.md`，明确本版是稳定性补丁版
- 梳理 `develop` 作为 `v2.0` 启动基线的前置清单

### 测试结果

- 本阶段以 CI / Docker / 发布链路稳定性修复为主，业务回归面沿用 `v1.2.4` 基线

### 下一步

- 如进入 `v2.0`，需以新的明确批次启动，不直接复用 `v1.x` 维护任务表

> **强约束**：未进入 v2。

---

## 2026-05-12 阶段 v2.0.0：七大模块架构升级 (M1-M7)

### 目标

将项目从 v1.x 单进程原型演进为多进程、Actor 模型、企业级游戏服务器框架。参考明星开源游戏服务器设计，按 v2.0.0 七大模块系统性落地。

### 完成内容

- **M1 — Actor 模型基础设施**：`ActorSystem`、`ActorRef`、`Actor` 基类、`tell()` 消息传递、Actor 生命周期管理、快照序列化
- **M2 — 多进程服务拓扑**：`login_backend` / `room_backend` / `battle_backend` 独立可执行文件、gateway 作为 TCP ingress、服务间通过 `BackendConnection` TCP 通信、`GatewayServiceBridge` 路由层
- **M3 — 网关会话与协议适配**：`SessionAdapter` 连接 actor 系统与网络层、`Runtime` 编排 gateway/battle/room actor、`ClientEnvelope` → `actor::tell()` 转换、`DownstreamSessionWriteSink` 异步写回
- **M4 — 房间与战斗生命周期**：`RoomActor`（创建/加入/离开/准备/广播）、`BattleActor`（ECS 帧同步/输入处理/结算）、`BattleRuntimeWorld`（ECS 世界管理）
- **M5 — WriteBehind 持久化**：`CachedDataStore` + `WriteBehindStore`、`JsonFileBattleDataStore`、战斗回放持久化、`ReplayPlayer`
- **M6 — 服务发现与注册**：`ServiceRegistry` TTL 心跳/健康检查、`BackendMetrics` 四维度（success/timeout/unavailable/error）、`CircuitBreaker` 熔断器
- **M7 — IO 引擎抽象**：`IoEngine` 接口、`AsioIoEngine` 实现、`IoAcceptor` / `IoSession` / `IoListener`、per-core 事件循环

### 测试结果

- 473 tests 通过

### 下一步

- v2.0.1 生产加固（配置热加载、断路器完善、Builder 模式、优雅关闭、SessionManager 隔离、连接生命周期归档）

---

## 2026-05-12 阶段 v2.0.1：生产加固 (H1-H6)

### 目标

在 v2.0.0 基础上补齐生产环境必需的安全性和可靠性能力。

### 完成内容

- **H1 — 配置热加载**：`ConfigWatcher` 基于 `std::filesystem::file_time_type` 监听配置文件变更，自动触发 `load_gateway_config()` 回调；`update_backend_config()` 自动断开旧连接重建
- **H2 — Per-service 断路器**：`CircuitBreaker` 集成到每个 `BackendSlot`，连续失败 ≥ 3 次熔断 30s，半开状态探测
- **H3 — DemoServer Builder 模式**：简化构造参数，`DemoServerOptions` 结构体封装可选配置
- **H4 — 优雅关闭增强**：`terminationGracePeriodSeconds`、session 排空、`shutdown()` 关闭全部后端连接
- **H5 — SessionManager 隔离**：网关不再直接持有 `SessionManager`，通过 `IoEngine` 间接管理
- **H6 — 连接生命周期归档**：`session_close_test.cpp` 覆盖关闭幂等、回调触发、错误码链路

### 测试结果

- 556 tests 通过

### 下一步

- v2.0.2 性能基线（延迟直方图、吞吐量跟踪、benchmark harness、容量规划文档）

---

## 2026-05-12 阶段 v2.0.2：性能基线与负载测试 (B1-B6)

### 目标

建立性能数字基线，指导容量规划和优化方向。

### 完成内容

- **B1 — 性能基准测试套件**：`v2_gateway_pressure` 工具支持 9 种场景（echo/battle/stability/concurrent/large_payload/rate_limit/connect_storm/disconnect_storm/latency_profile）
- **B2 — 延迟直方图**：`LatencyHistogram` 14 个指数分桶（1ms → 30s），P50/P90/P99 延迟统计
- **B3 — 吞吐量跟踪**：`ThroughputTracker` 滑动窗口计数器（5s 窗口，10 个子桶）
- **B4 — Backend 延迟测量**：`GatewayServiceBridge::route()` 中记录 backend 往返延迟到 `BackendMetrics`
- **B5 — SLO/SLI 定义**：`docs/performance-baseline.md` 含可用性目标（99.9%）、延迟目标（P99 ≤ 50ms）
- **B6 — 容量规划文档**：`docs/performance-baseline.md` 含扩容公式和硬件推荐

### 测试结果

- 556 tests 通过

### 下一步

- v2.1.0 多进程集成验证（E2E 业务流程测试、故障注入、长时间浸泡）

---

## 2026-05-12 阶段 v2.1.0：多进程集成验证 (E1-E6)

### 目标

4 个服务以真实 TCP 连接协作，覆盖完整生命周期和异常场景。

### 完成内容

- **E1 — 多进程集成测试框架**：`multi_process_test.cpp` 通过 fork + exec 启动 gateway/login/room/battle 4 个进程，通过 socket 通信
- **E2 — 完整业务流程测试**：`business_flow_test.cpp` 覆盖 login → create_room → join → ready → start_battle → frame_sync → settlement → replay
- **E3 — 故障注入**：`FaultInjector` 支持丢包、延迟注入、连接重置；`backend_routing_test.cpp` 覆盖后端宕机、超时、不可用
- **E4 — 服务发现演练**：ServiceRegistry TTL 过期自动摘除、心跳恢复自动上线
- **E5 — 重连与结算测试**：`settlement_replay_test.cpp` 覆盖战斗结算数据一致性
- **E6 — 客户端协议规范**：输出 `docs/v2-protocol-spec.md`

### 测试结果

- 576 tests 通过

### 下一步

- v2.2.0 安全加固（JWT 认证、消息级授权、多级速率限制、OpenTelemetry trace、结构化审计日志）

---

## 2026-05-12 阶段 v2.2.0：安全与可观测性 (S1-S7)

### 目标

达到企业级安全基线，可观测性满足生产运维需求。

### 完成内容

- **S1 — JWT 认证**：`JwtValidator` 支持 HS256/RS256，token 过期和刷新，`DevTokenValidator` 向后兼容
- **S2 — 消息级授权**：基于用户角色的消息类型访问控制，`SchemaValidator` 请求/响应格式校验
- **S3 — 多级速率限制**：`RateLimiter` 支持全局 + 每 IP + 每用户 + 每消息类型，令牌桶算法
- **S4 — 分布式追踪**：`TraceContext` 跨服务 trace_id/span_id 传播，W3C TraceContext 兼容格式
- **S5 — 结构化审计日志**：`AUDIT_LOG` JSON 格式输出，含必备键（action/user_id/session_id/timestamp）
- **S6 — 标准化健康检查**：`HealthCheck` 组件化，`/health`（存活）/ `/ready`（就绪）/ `/metrics`（Prometheus）
- **S7 — 服务总线完整性测试**：`service_bus_integrity_test.cpp` 跨服务消息完整性验证

### 测试结果

- 全部测试通过

### 下一步

- v2.3.0 高级游戏特性（MMR 匹配、排行榜、反外挂、帧同步优化、消息 Schema 校验）

---

## 2026-05-12 阶段 v2.3.0：高级游戏特性 (G1-G5)

### 目标

补充游戏服务器核心业务特性，达到生产可用水平。

### 完成内容

- **G1 — MMR 匹配系统**：`MatchmakingService` 基于 MMR 的匹配队列，支持 1v1/2v2/4v4，可配置匹配超时，`MatchmakingQueue` 评分分桶
- **G2 — 排行榜服务**：`LeaderboardService` 支持 Redis 风格 Sorted Set 操作（ZADD/ZRANGE/ZRANK），全局/好友/赛季排行榜
- **G3 — 反外挂基础**：`AntiCheatService` 服务端输入校验（移动距离/速度/冷却时间），异常行为检测和日志
- **G4 — 战斗系统增强**：`BattleSystems`（移动/技能/伤害/Buff）、`AOISpatialGrid` 空间兴趣管理、`BroadcastService` 广播优化
- **G5 — 消息 Schema 校验**：`SchemaValidator` 基于 JSON Schema 的请求/响应格式校验，拒绝畸形消息

### 测试结果

- 全部测试通过

### 下一步

- v2.4.0 客户端 SDK 封装

---

## 2026-05-13 阶段 v2.4.0：客户端 SDK 封装

### 目标

为游戏客户端提供开箱即用的 C++ SDK，降低接入门槛。

### 完成内容

- `boost_gateway_sdk` 共享库：`BoostClient`（connect/login/echo/room_ops/battle_ops）、异步回调 API
- 基准示例程序：`sdk_echo_client`、`sdk_battle_client`
- SDK 全流集成测试：`sdk_protocol_test.cpp` 覆盖 V4 协议编解码、请求/响应匹配
- 共享库符号导出（`BOOST_GATEWAY_SDK_EXPORT`）

### 测试结果

- 556 tests 通过（SDK 集成测试 + 原有测试）

### 下一步

- v2.5.0 全方位客户端集成测试

---

## 2026-05-13 阶段 v2.5.0：全方位集成测试

### 目标

补齐 v2 架构下的全方位测试覆盖。

### 完成内容

- `tests/v2/unit/` 扩展到 57 个测试文件（actor_runtime / battle_archive / lru_cache / battle_actor / ecs_world / cluster_router / remote_actor / raft / otel_persistence / k8s_operator 等）
- `tests/v2/integration/` 扩展（backend_routing / demo_server_smoke / backend_health / data_layer / service_bus / multi_process / business_flow / settlement_replay）
- 多进程 E2E 测试框架完善（进程生命周期管理、端口分配、超时控制）
- SDK Python 轻量封装验证

### 测试结果

- 全部测试通过

### 下一步

- v2.6.0 文档整合 + 环境基础设施

---

## 2026-05-13 阶段 v2.6.0：文档整合与环境基础设施

### 目标

整合所有文档产出，搭建环境基础设施（Docker Compose、K8s 骨架、Prometheus/Grafana、Redis 配置）。

### 完成内容

- 文档整合：`docs/v2-design.md`、`docs/v3-environment-roadmap.md`、`docs/v2-enterprise-roadmap.md` 等
- 环境基础设施：`env/docker/Dockerfile.gateway` + `Dockerfile.backend`、`env/docker/docker-compose.yml`（9 服务栈，初始版）
- 监控配置：`env/monitoring/prometheus.yml`（17 条告警规则）、`env/monitoring/grafana-dashboard.json`
- K8s 骨架：`env/k8s/backend-deployment.yaml`、`env/k8s/gateway-deployment.yaml`、`env/k8s/gameserver-crd.yaml`
- Helm Chart 骨架：`env/helm/boost-gateway/Chart.yaml` + values
- `env/redis/redis.conf`（maxmemory 256mb、allkeys-lru、appendonly yes）
- `scripts/build_docker.sh`

### 测试结果

- 576 tests 通过

### 下一步

- v3.0.0 分布式运行时（Cluster Router、Remote Actor、Consistent Hash、Raft、OTel、K8s Operator）

---

## 2026-05-13 阶段 v3.0.0：分布式运行时 (D1-D8)

### 目标

将项目从单机多进程架构升级为分布式运行时，支持跨节点服务发现、远程 Actor 调用、一致性哈希分片、Raft 领导者选举。

### 完成内容

- **D1 — Cluster Router**：`ClusterRouter` 服务发现 + 健康检查 + mark_unhealthy，`ServiceInstance` 注册/发现，支持静态配置和动态注册
- **D2 — Remote Actor Transport**：`RemoteActor` 跨进程 `actor::tell()`，`RemoteActorProxy` 透明序列化/反序列化，`MessageWireSerializer` 二进制编解码
- **D3 — 一致性哈希分片**：`ConsistentHashRing` 虚拟节点实现，`ShardRouter` 基于 room_id/battle_id 路由到固定节点，会话亲和性
- **D4 — Raft 领导者选举**：`RaftNode`（Follower/Candidate/Leader 状态机）、`RaftLog` 持久化、`RaftRpcClient` 节点间通信
- **D5 — gRPC Proto 定义**：`proto/gateway.proto`、`proto/battle.proto`、`proto/match.proto`、`proto/leaderboard.proto`
- **D6 — K8s Operator**：`k8s_operator_test.cpp` 基础框架，`GameServer` CRD controller 逻辑（创建/更新 Deployment + Service）
- **D7 — TLS 配置类型**：`TlsSessionConfig`、`TlsCertificateConfig`、`SecurityPolicy`（per-service TLS/mTLS 策略）、`TlsVerifyMode` 枚举
- **D8 — OpenTelemetry 集成**：`OtlpExporter` OTLP/gRPC span 导出、`TraceContext` W3C 格式传播、`GatewayServiceBridge::route()` 中创建/导出 span
- **D9 — 跨模块集成验证**：`v3_integration_test.cpp`（Cluster Router + Remote Actor + Consistent Hash 联合验证）、错误路径测试
- **D10 — Event Persistence**：`IEventStore` 接口定义（append/read/latest_sequence/total_events）、`FileEventStore` 实现

### 测试结果

- 655 tests 通过（含 v2 单元测试 57 文件 + v2 集成测试 + v2 多进程测试 + v3 集成 + Redis 测试）

### 下一步

- v3.1.0 生产基础设施（Redis 集成、Docker 生产构建、K8s 部署验证、TLS/mTLS 安全传输 + FeatureFlag 灰度控制）

---

## 2026-05-14 阶段 v3.1.0：生产基础设施 (E1-E4)

### 目标

将 v3.0.0 分布式运行时的基础设施提升到生产就绪水平：Redis 持久化、Docker 生产构建、K8s 部署验证、TLS/mTLS 安全传输与 FeatureFlag 灰度控制。

### 完成内容

#### E1 — Redis 集成

- **hiredis 接入**：通过 CMake `FetchContent` 拉取 hiredis C 客户端，添加 `CMAKE_POLICY_VERSION_MINIMUM 3.5` 兼容 CMake ≥ 4.0
- **`RedisClient`**（PIMPL/RAII C++ 包装）：`connect`/`reconnect`/`get`/`set`/`del`/`exists`/`incr`/`lpush`/`lrange`/`llen`/`zadd`/`zrange_with_scores`/`zcard`，自动重连模式
- **`RedisEventStore`**：实现 `IEventStore` 接口，事件以 JSON 字符串 LPUSH 到 `{prefix}:{aggregate_id}` 和 `{prefix}:by_type:{type}`，`{prefix}:next_seq` 单调序列号
- **16 项测试**：`RedisClientTest`（连接失败优雅降级、断开操作返回空、SetGetDel、Exists、Incr、List、SortedSet、MoveSemantics）+ `RedisEventStoreTest`（AppendAndRead、LatestSequence、ReadByType、TotalEvents、FromSequenceFilter），Redis 不可用时 GTEST_SKIP
- **`project_v3` 链接 hiredis**：`target_include_directories(project_v3 PRIVATE "${hiredis_SOURCE_DIR}")` + `target_link_libraries(project_v3 PRIVATE hiredis)`

#### E2 — Docker 生产构建

- **Dockerfile 修复**：`Dockerfile.backend` 修复致命 bug — ENTRYPOINT 曾硬编码 `/app/bin/v2_login_backend` 导致所有 5 个后端服务都运行 login 二进制；通过 `SERVICE_BINARY` build-arg + `ln -s /app/bin/${SERVICE_BINARY} /app/bin/backend` + `ENTRYPOINT ["/app/bin/backend"]` 修复
- **Ubuntu 升级**：`Dockerfile.gateway` 和 `Dockerfile.backend` 升级 ubuntu:22.04 → ubuntu:24.04
- **docker-compose 完整栈**：`env/docker/docker-compose.yml` 9 服务编排（gateway、login、room、battle、matchmaking、leaderboard、redis、prometheus、grafana），所有服务 healthcheck + depends_on service_healthy
- **`scripts/build_docker.sh`**：支持全量构建、per-service 构建、`--no-cache` 选项

#### E3 — K8s 部署验证

- **5 个独立 Deployment 文件**：`login-backend-deployment.yaml` / `room-backend-deployment.yaml` / `battle-backend-deployment.yaml` / `matchmaking-backend-deployment.yaml` / `leaderboard-backend-deployment.yaml`，替代旧 `backend-deployment.yaml`
- **Production 级别配置**：每个 Deployment 含 ConfigMap + RollingUpdate（maxUnavailable: 0, maxSurge: 1）+ podAntiAffinity + HPA（autoscaling/v2, CPU 70%, memory 80%）+ PDB（minAvailable: 1）
- **Gateway K8s 完善**：增加 matchmaking/leaderboard 后端 host/port args、livenessProbe + readinessProbe（/health:9080）
- **Battle 特殊配置**：更高资源配额（500m/128Mi requests, 2/512Mi limits）、archive volume（emptyDir）
- **`scripts/deploy_k8s.sh`**：一键部署全部 6 服务

#### E4 — TLS/mTLS 安全传输 + FeatureFlag 灰度控制

- **Phase 1 — FeatureFlags 可运维化**：新增 `include/v2/config/env_util.h`（首个 `std::getenv` 使用）；`FeatureFlags` 新增 `load_from_json()` 和 `apply_env_overrides()`；9 项新测试
- **Phase 2 — GatewayServiceBridge 安全策略接入**：`set_security_policy()` / `set_feature_flags()` setters；`make_options()` 签名升级接受 `SecurityPolicy` + `ServiceId`；TLS FeatureFlag 门控
- **Phase 3 — 证书生成与配置**：`scripts/gen_certs.sh`；`.gitignore` 新增 `/certs/`；`config/gateway.json` 新增 `feature_flags` / `tls` / `security_policy` 三段配置
- **Phase 4 — DemoServer 装配**：`DemoServer` 新增 `feature_flags_` + `security_policy_` 成员；`load_gateway_config()` 解析新配置段

### 安全默认值

所有默认值设计为**不改变现有行为**：
- `v3_tls_enabled: false` — 需显式开启
- `security_policy.require_tls: false` — 全局关闭
- 各 service `tls_required: true` 但受 FeatureFlag 门控保护

### 测试结果

- **751 tests 通过，0 failures**（v3.0.0 基线 655 + 新增 Redis 测试 16 + feature_flags 9 项 + 其他）

### 影响范围

- `cmake/Dependencies.cmake`（hiredis FetchContent）
- `include/v2/config/env_util.h`（新）、`include/v2/config/feature_flags.h`
- `include/v2/gateway/gateway_service_bridge.h`、`include/v2/gateway/demo_server.h`
- `src/v2/gateway/gateway_service_bridge.cpp`、`src/v2/gateway/demo_server.cpp`
- `src/v3/persistence/redis_client.cpp`（新）、`redis_event_store.cpp`（新）
- `include/v3/persistence/redis_client.h`（新）、`redis_event_store.h`（新）
- `env/docker/Dockerfile.gateway`、`env/docker/Dockerfile.backend`、`env/docker/docker-compose.yml`
- `env/k8s/gateway-deployment.yaml`、`env/k8s/*-backend-deployment.yaml`（5 个）
- `scripts/gen_certs.sh`（新）、`scripts/build_docker.sh`（新）、`scripts/deploy_k8s.sh`（新）
- `config/gateway.json`、`.gitignore`
- `tests/v2/unit/feature_flags_test.cpp`、`tests/unit/redis_event_store_test.cpp`

### 下一步

- **E5**：K8s Operator 实现（Go + controller-runtime 或 Python + kopf）
- **RedisLeaderboard**：基于 `RedisEventStore` 实现排行榜存储
- **Raft 集成测试**：多节点集群验证
- **gRPC 服务端**：基于 proto 定义实现
- **生产环境部署与压测**
