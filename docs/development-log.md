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
