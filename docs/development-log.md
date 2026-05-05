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
