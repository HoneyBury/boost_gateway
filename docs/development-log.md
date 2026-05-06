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

- **`v1.1.14`**：T13 后半 — 受控 reload / shutdown **语义**（矩阵 §5.2–§5.3）。

> **强约束**：未进入 v2。
