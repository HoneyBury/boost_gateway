# v2.0 启动清单

> **定位**：本文档用于从当前 `develop` 分支启动 `v2.0.0` 新批次，不等同于 `v1.x` 维护任务续写。
>
> **规划来源**：
> - `docs/v2-roadmap.md`：定义 `v2.0.0` 七大模块与企业级目标
> - `docs/v2-design.md`：定义 `Actor + ECS`、`SessionAdapter`、`GatewayActor`、`PlayerActor` 等最小设计草案
> - `docs/architecture-roadmap.md`：定义从单进程骨架到多服务可拆分架构的演进次序

## 1. 启动结论

当前 `develop` 可以作为 `v2.0.0` 的**启动基线**，但不能视为已经进入 `v2.0.0` 开发。

原因：

- `v1.x` 维护收口、测试护栏、发布流程已经成型
- `v1.2.0 / T21` 已明确：结构升级必须重新立项
- 当前 `develop` 与 `main` 没有实质功能分叉，不存在可直接“接手维护”的 v2 主线

## 2. 规划对齐

`v2.0` 启动阶段不要试图一次吃完 `v2-roadmap` 的全部七大模块。

按规划文档，启动批次只应覆盖：

- `M1 Actor 模型核心`
- `SessionAdapter + GatewayActor` 桥接
- `PlayerActor` 最小状态
- `RoomActor` 最小闭环
- `BattleActor` bootstrap shell（仅限创建、输入受理、状态 push 占位）

明确不在启动批次内的模块：

- `M2 多核 I/O 引擎`
- `M3 内存架构重构`
- `M4 分布式原语`
- `M5 数据层 v2`
- `M6 AOI 空间管理`
- `M7 运维成熟度`
- `BattleActor + ECS World` 完整仿真链
- 现有 `GatewayServer` 主链替换

## 3. 分支策略

建议不要直接在当前 `develop` 上展开 `Actor + ECS` 重构。

建议流程：

1. 保持 `main` 继续承接 `v1.x` 稳定发布
2. 保持 `develop` 继续承接 `v1.x` 日常修复与收尾
3. 从当前 `develop` 拉出 `feature/v2-bootstrap` 或 `v2/develop`
4. 后续所有 `v2` 结构开发先进入该分支，不回写 `v1.x develop`

## 4. 启动前置决策

开始写 `v2` 代码前，先冻结以下输入。

### 4.1 协议兼容策略

来源：`docs/v2-design.md` §2、§5；`docs/architecture-roadmap.md` §3.1。

必须明确：

- `v1.x` 客户端协议是否继续作为 `v2` 的外部入口
- `ClientEnvelope` 是否继续保留 `message_id + request_id + error_code + flags + body`
- `Actor` 内部 typed message 与外部字符串协议如何桥接

建议结论：

- 外部入口继续兼容 `v1`
- 内部主消息模型切换为 typed payload
- 第一批不改客户端包格式

### 4.2 主链切换策略

来源：`docs/v2-design.md` §1、§3；`docs/architecture-roadmap.md` §2。

必须明确：

- `v2` 是先以独立 demo 入口并存，还是直接替换现有 `GatewayServer`
- `SessionAdapter` 是否只挂在新 demo 入口
- 何时允许把 `GatewayActor` 接入现有主链

建议结论：

- 第一阶段采用“并存入口”
- 只新增 `examples/v2_gateway_demo/`
- 不改现有 `echo_server` / `GatewayServer` 主展示入口

### 4.3 回归测试保留范围

来源：`docs/v2-design.md` §2 第 5 条；`docs/development-priority.md` T17–T20。

必须明确：

- 哪些 `v1` 单元测试继续作为护栏
- 哪些 `v1` 集成测试在 `v2` 启动期必须持续通过
- 哪些测试只对 `v2` demo 分支生效

建议最低保留集：

- `gateway_integration_test`
- `room_manager_test`
- `battle_manager_test`
- `lifecycle_assembly_test`
- `persistence_replay_audit_test`

## 5. 启动范围

依据 `docs/v2-roadmap.md` 和 `docs/v2-design.md`，启动批次只做“抽象做对”，不做“规模做大”。

### 5.1 应进入启动批次

- `Actor` 基类
- `ActorRef`
- `MessageHeader` / `Message`
- 单线程 mailbox
- 本地 `tell()` 投递
- `SessionAdapter`
- `GatewayActor`
- `PlayerActor`
- `RoomActor`
- 最小 `BattleActor` shell
- 对应单元测试与最小桥接集成测试

### 5.2 不应进入启动批次

- 多核 `io_context`
- 无锁跨核队列
- cluster router
- supervisor 树完整容错策略
- arena allocator
- replay 生产链
- ECS world 实体系统
- OpenTelemetry / K8s / 控制面
- `GatewayServer` 主入口替换

## 6. 第一批目录骨架

建议第一批只建骨架，不急着迁移业务：

```text
include/v2/actor/
include/v2/runtime/
include/v2/gateway/
include/v2/player/
include/v2/room/
include/v2/common/
src/v2/actor/
src/v2/runtime/
src/v2/gateway/
src/v2/player/
src/v2/room/
tests/v2/unit/
tests/v2/integration/
examples/v2_gateway_demo/
```

约束：

- `v2/` 与现有 `app/`、`net/`、`game/` 并存
- 第一批不修改 `v1` 主链目录语义
- 所有桥接代码显式命名为 `adapter` / `bridge`
- `v2` 目录内避免直接依赖 `SessionManager` 作为状态拥有者

## 7. 分批清单

建议把 `v2.0` 启动拆成 5 个批次，每批都要有独立验收定义。

### B0：初始化批

目标：

- 建 `v2/` 目录骨架
- 接入 `CMake`
- 新增 `tests/v2` 目标
- 补一份 `v2` 模块 README 或索引注释

验收：

- 默认构建不破坏 `v1`
- 空骨架可编译
- `ctest` 能发现 `v2` 测试目标

### B1：Actor Runtime 最小原型

对齐文档：

- `docs/v2-design.md` §4
- `docs/v2-roadmap.md` M1

目标：

- `Actor`
- `ActorId`
- `ActorRef`
- `MessageHeader`
- `Message`
- 单线程 mailbox
- 本地 `tell()`

验收：

- 能在无网络条件下启动最小 `ActorSystem`
- 两个 actor 之间可完成一条消息投递
- actor 生命周期 `on_start/on_stop/on_message` 可单测

### B2：Gateway Bridge

对齐文档：

- `docs/v2-design.md` §5
- `docs/architecture-roadmap.md` §2.1 / §3.4 / §3.6

目标：

- `ClientEnvelope`
- `SessionAdapter`
- `GatewayActor`
- 登录前白名单
- 基础限频钩子占位
- 外部协议到内部 typed message 的转换

验收：

- 不改现有 `Session` 主链语义
- 能把一条客户端消息转成 actor 外部消息并返回响应
- 未建模消息能被显式拒绝

### B3：PlayerActor

对齐文档：

- `docs/v2-design.md` §6
- `docs/architecture-roadmap.md` §3.3 / §3.5 / §3.6

目标：

- 登录态
- 当前连接绑定
- 顶号替换
- 恢复态最小字段
- `player -> room` 引用关系

验收：

- 能在测试中覆盖登录、顶号、重连恢复最小闭环
- `PlayerActor` 成为玩家状态唯一拥有者
- 不再把玩家核心状态散落在多个 manager 上

### B4：RoomActor

对齐文档：

- `docs/v2-design.md` §3、§6
- `docs/architecture-roadmap.md` §3.7

目标：

- 成员关系
- 房主
- ready 状态
- 开战资格判定
- 向 `BattleActor` 发送启动请求

验收：

- 能复用 `v1` 房间主要边界语义
- `RoomActor` 持有房间状态唯一事实源
- 不直接跨域改 `PlayerActor` 状态

### 7.5 2026-05-09 当前实现状态

当前仓库已经完成：

- `B0`：`v2/` 目录骨架、`CMake` 接线、`tests/v2` 目标、`examples/v2_gateway_demo/`
- `B1`：`Actor` / `ActorRef` / `MessageHeader` / `Message` / 单线程 mailbox / `ActorSystem`
- `B2`：`ClientEnvelope`、`SessionAdapter`、`GatewayActor`、登录前白名单、基础限频钩子、未建模消息拒绝
- `B3`：`PlayerActor` 最小状态、顶号替换、房间引用、断线挂起与重连恢复最小闭环
- `B4`：`RoomActor` 成员关系、房主、ready、开战资格判定、向 `BattleActor` 发起启动请求
- `B4+`：`BattleActor` bootstrap shell、`BattleStartResponse`、`BattleStatePush`、`BattleInputResponse`、`BattleInputPush`
- `P1`：`examples/v2_gateway_demo` 已支持 `--script` 烟测和基于现有 `net::Session` 的真实监听入口；`tests/v2/integration` 已新增真实 socket smoke test
- `P2`：已补 battle 最小 lifecycle：`BattleAssigned`、`PlayerDisconnected -> BattleFinished`、`RoomActor` active battle 清理、`PlayerActor` 从 `InBattle` 回切到 `InRoom`
- `P2+`：已补 battle 最小 frame shell：`TickBattleMsg`、`BattleFrameAdvancedMsg`、基于 frame limit 的正常结束路径
- `P3`：现有 `GatewayServer` 已新增可关闭的 packet bridge seam，可旁路镜像 traffic，不改变 `v1` 默认分发结果
- `P4`：已补 `M2-M7` 进入边界文档，明确当前不做项与后续进入条件

当前明确只有原型或占位的部分：

- `BattleActor` 只处理战斗创建和输入受理，不包含 `ECS World`、帧循环、状态广播聚合、结算、回放
- `BattleActor` 已有最小结束路径，但结束条件仍只覆盖“玩家断线触发结束”，没有正常结算、超时、投降等分支
- `SessionAdapter` 已可挂接 demo server，但还没有接入现有 `GatewayServer` 主链
- runtime 仍是单进程、单线程 bootstrap 编排层，不是多核 actor runtime
- battle 输入目前只做到“受理 + 转发”，没有 authoritative simulation
- `PlayerActor` / `RoomActor` / `BattleActor` 之间仍以最小 typed message 协作为主，没有 supervisor 树、timer、`ask()`、延时消息

当前不应误判为已完成的内容：

- `M2` 多核 I/O 引擎
- `M3` 内存架构重构
- `M4` 分布式原语
- `M5` 数据层 v2
- `M6` AOI / ECS 战斗主链
- `M7` 运维成熟度
- `v2` 替换现有 `v1` 默认入口

## 8. 文档产出清单

每个启动批次至少同步以下文档产物：

- `CHANGELOG.md`
- `docs/development-log.md`
- `docs/development-priority.md`
- 当前 `v2` 批次对应的设计补充文档

建议新增的 `v2` 子文档：

- `docs/v2-runtime.md`
- `docs/v2-protocol-bridge.md`
- `docs/v2-player-lifecycle.md`
- `docs/v2-room-lifecycle.md`
- `docs/v2-gateway-cutover-criteria.md`
- `docs/v2-next-phases.md`

## 9. 测试清单

`v2` 启动分支至少补齐以下测试层次：

### 9.1 单元测试

- `Actor` runtime
- mailbox 投递
- `GatewayActor` 协议桥接
- `PlayerActor` 生命周期
- `RoomActor` 状态边界

### 9.2 集成测试

- `SessionAdapter -> GatewayActor -> PlayerActor`
- 登录成功
- 重复登录替换
- 创建房间 / 加入房间 / ready

### 9.3 回归护栏

- `v1` 现有测试必须继续可构建
- `v2` 新增代码不能破坏 `v1` 默认入口

## 10. 启动检查表

- 已创建独立 `v2` 代码目录
- 已确认协议兼容策略
- 已确认主链切换策略
- 已确认 `v1` 回归测试保留范围
- 已完成 `v2` 目录骨架
- 已完成 `ActorSystem` 最小原型
- 已完成 `SessionAdapter` / `GatewayActor` 桥接原型
- 已完成第一批 `v2` 单元测试
- 已完成 `PlayerActor` / `RoomActor` 最小闭环
- 已完成 `BattleActor` bootstrap shell
- 已完成 `v2_gateway_demo` 真实监听入口

说明：

- 如果分支策略仍采用 `develop` 内联推进，则本检查表中的“独立 `v2` 分支”暂按“独立 `v2` 代码目录 + 独立提交批次”执行
- 截至 `2026-05-09`，当前实现更接近“`M1` 可运行原型 + demo 入口”，尚未进入 `M2-M7`

## 11. 推荐下一步

如果继续沿当前原型推进，建议下一阶段不要再扩散模块面，而是按以下顺序收口：

1. 把 `v2_gateway_demo` 的真实收包链路补成可交互 smoke test
2. 给 `BattleActor` 补最小 battle lifecycle，而不是直接跳到 ECS
3. 明确 `GatewayServer` 主链接入条件和切换门槛

不要在当前阶段同时推进多核 I/O、分布式、数据层和 ECS World。
