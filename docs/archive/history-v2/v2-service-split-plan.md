# v2 服务拆分规划

## 1. 结论

`v2` 现有规划里，**有** `M4 分布式原语` 这个大方向，但**没有**把“服务拆分 / 多进程后端服务”写成一条可执行的实施路线。

当前已有文档的问题是：

- `../history-v2/v2-roadmap.md` 把 `M4` 重点放在一致性哈希、领导者选举、服务发现
- `docs/architecture-roadmap.md` 讨论了网关与逻辑服拆分方向
- 但没有一份 `v2` 文档明确回答：
  - 当前到底完成了哪些拆服前置能力
  - 还差哪些真正的多进程协作能力
  - 应该按什么阶段推进
  - 每阶段验收标准是什么

因此本文件补上 `v2` 的服务拆分专项规划。

## 2. 目标与边界

### 2.1 目标

把当前“单进程主链 + 多个独立 demo 入口”的形态，推进到：

- `gateway-only ingress`
- `login / room / battle` 独立后端服务
- 有独立 backend envelope
- 有明确的服务发现 / 路由 / 回包 / 超时 / 健康语义
- 可以做真实的多进程集成测试

### 2.2 当前不在本规划内

- 跨机器集群高可用
- 一致性哈希的完整生产化实现
- 领导者选举
- `SO_REUSEPORT`
- battle authoritative simulation

这些属于后续 `M4` 深化或 `M6` 深化，不是第一版服务拆分的进入门槛。

## 3. 当前已完成的内容

以下内容已经具备，可视为服务拆分的前置基础：

### 3.1 入口与运行时基础

- `GatewayServer` 已支持 `IoEngine` ingress、多 listener、session-core aware outbound
- `DemoServer` / `v2_gateway_demo` 已有可运行的 `v2` 入口
- `GatewayServerShadowBridge` 已能提供协议镜像与诊断扩展

### 3.2 业务边界基础

- `PlayerActor` / `RoomActor` / `BattleActor` 最小闭环已存在
- battle runtime 已部分 world 化，`BattleActor` 更接近编排层
- `v1` 主链的 login / room / battle 基础协议和测试护栏已经稳定

### 3.3 为拆服铺路的抽象

- `ServiceId`
- `ServiceRouter`
- `InternalBus`
- `ServiceRegistry`
- `BackendRouter`

但这些目前只能算**占位或实验性前置抽象**，还不是“服务拆分模块已完成”。

## 4. 当前未完成的关键缺口

以下内容缺任何一项，都不能把当前状态称为“服务拆分已完成”。

### 4.1 ingress 形态未真正分离

当前 `examples/login`、`examples/room`、`examples/battle` 都是各自启动一份 `GatewayServer`。

这意味着当前形态仍然是：

- 每个模块自己带接入层
- 而不是“前置 gateway + 后端逻辑服务”

### 4.2 backend 协议没有独立 envelope

当前 `InternalBus` 直接复用了客户端 packet codec 和 `MessageDispatcher`。

这会导致：

- 内外协议边界不清
- 内部链路继续背负客户端会话语义
- 后续服务路由、超时、错误语义、版本兼容都难以单独演进

### 4.3 路由与回包语义未闭环

当前还没有正式定义：

- backend request id / correlation id
- gateway 到 backend 的超时
- backend 错误如何映射到客户端响应
- 单播回包 / 广播 push / 跨服务级联调用

### 4.4 服务发现和健康语义未闭环

当前 `ServiceRegistry` 只是内存表，没有：

- TTL
- 主动心跳
- 订阅更新
- 摘除与恢复策略

### 4.5 ownership 未收口

当前还没有正式定义：

- player 归属谁
- room 归属谁
- battle 归属谁
- gateway 何时只转发、何时保留本地状态

没有 ownership 规则，服务拆分只会变成“多进程转发”，不会变成稳定架构。

## 5. 建议的实施阶段

建议把服务拆分作为 `M4` 下的专项，按 `S0-S4` 推进。

### S0：边界冻结

目标：

- 冻结第一版拆分边界：`gateway / login / room / battle`
- 明确每个服务的 ownership
- 明确 gateway 保留哪些状态，backend 保留哪些状态
- 定义 backend envelope 草案

完成内容：

- 服务职责表
- backend envelope 字段表
- request / response / push / error / timeout 语义

验收标准：

- 能用一份文档回答“某条消息为什么路由到某服务”
- 能用一份文档回答“某个状态归谁拥有”
- 不再出现“既能本地处理也能后端处理”的模糊表述

### S1：gateway-only ingress 最小版

目标：

- 让 `gateway` 只负责客户端接入、限流、会话、协议编解码、出站
- backend 进程不再直接启动 `GatewayServer`

完成内容：

- 一个最小 `gateway ingress -> backend stub` 路由链
- `login_server / room_server / battle_server` 改成 backend 进程骨架，而不是独立网关样例
- backend 可接收内部 envelope，而不是客户端 packet

验收标准：

- `gateway` 与 `login_server` 可作为两个独立进程协作完成一条最小请求闭环
- backend 进程不直接监听客户端协议端口
- gateway 停机和 backend 停机的错误语义可观测

### S2：login 拆分落地

目标：

- 先把 login 域完整拆出，作为第一条真实后端链路

完成内容：

- 登录请求经 gateway 转发到 `login_server`
- 登录结果回 gateway，再回客户端
- 重复登录、错误登录、超时登录的回包语义固定

验收标准：

- 两进程集成测试覆盖：
  - login success
  - invalid token
  - duplicate login
  - backend timeout
- gateway 不直接执行业务级 token 校验

### S3：room / battle ownership 拆分

目标：

- 让 `room` 和 `battle` 作为真正的后端 ownership 服务存在

完成内容：

- room 请求只路由到 `room_server`
- battle 请求只路由到 `battle_server`
- gateway 只保留会话态和必要的路由缓存
- 定义 room->battle 的级联调用边界

验收标准：

- 三进程集成测试覆盖：
  - create/join/ready/start
  - battle input / finish
  - player disconnect / relogin
- room 和 battle 的事实源不再同时在 gateway 本地保存一份可写状态

### S4：发现、健康与运维闭环

目标：

- 让第一版拆服具备最小可运维能力

完成内容：

- `ServiceRegistry` 增加 TTL / 心跳 / 摘除
- gateway metrics 暴露 backend routing / timeout / error counters
- 管理口可看到 backend 实例健康和路由分布

验收标准：

- backend 下线时 gateway 能在约定时间内摘除
- timeout / unavailable / rejected 三类错误可区分
- 管理口可直接观察当前 backend 拓扑和健康状态

## 6. 推荐优先级

当前推荐顺序：

1. `S0` 边界冻结
2. `S1` gateway-only ingress 最小版
3. `S2` login 拆分
4. `S3` room / battle ownership 拆分
5. `S4` 发现与运维闭环

不建议的顺序：

- 先做服务发现，再去定义 ownership
- 先做 battle 拆分，再去验证 login 最小链路
- 先做多机集群，再补 backend envelope

## 7. 当前状态判定

按本规划口径，当前状态是：

- `S0`：**已完成**（`2026-05-11`）— `ServiceId`、`BackendEnvelope`、`ServiceManifest`、`ServiceErrorCode` 已落地，24 个边界测试通过
- `S1`：**已完成**（`2026-05-11`）— `BackendServer`/`BackendConnection` TCP 传输层、`GatewayServiceBridge` + `BackendSlot` lazy-connect 路由、`IoEngine` 多核 ingress、gateway 作为唯一客户端接入层已落地
- `S2`：**已完成**（`2026-05-11`）— login 域完整拆出：`login_backend` 进程（`examples/v2_login_backend/`）、`kLogin` bridge 路由（`runtime.cpp`）、`BackendEnvelope` request/response 闭环、4 个 login 集成测试（success/invalid token/duplicate/backend timeout）
- `S3`：**已完成**（`2026-05-11`）— room/battle ownership 拆分：`room_backend` 进程（5 handlers + `forward` 级联指令）、`battle_backend` 进程（3 handlers + `push_to_sessions` 广播）、`GatewayServiceBridge` 三 slot（login/room/battle）、Runtime 级联路由（`room_start_battle` → forward → `battle_create` → push_to_sessions → 房间广播）、6 个 S3 集成测试全部通过
- `S4`：**已完成**（`2026-05-12`）— `ServiceRegistry` TTL/心跳/摘除、`BackendMetrics` 路由计数器（per ServiceId）、`DemoServer::diagnostics_json()` 新增 `backend_metrics` + `backend_instances` 字段、7 个单元测试 + 4 个集成测试全部通过

S0 完成内容：

- `include/v2/service/service_id.h` — `ServiceId` 枚举 + `to_string`
- `include/v2/service/backend_envelope.h` — `BackendEnvelope` + `MessageKind` (request/response/push/error) + `to_json`/`from_json`/`is_valid`/`generate_correlation_id`
- `include/v2/service/service_manifest.h` — `ServiceManifest` + `gateway_manifest()`/`login_manifest()`/`room_manifest()`/`battle_manifest()` + `owner_of()`/`handler_of()`
- `include/v2/service/error_codes.h` — `ServiceErrorCode` + `to_client_error()`/`to_string()`
- `tests/v2/unit/service_boundary_test.cpp` — 24 个测试覆盖所有上述类型

S1-S3 完成内容：

- `include/v2/gateway/gateway_service_bridge.h` / `src/v2/gateway/gateway_service_bridge.cpp` — `GatewayServiceBridge` + `BackendSlot` lazy-connect TCP 路由，三 slot（login/room/battle）
- `src/v2/gateway/backend_server.h` / `src/v2/gateway/backend_connection.cpp` — `BackendServer`/`BackendConnection` 传输层
- `src/v2/gateway/runtime.cpp` — 全部 5 个 room/battle handler bridge 分支 + 级联 forward/push_to_sessions 处理
- `examples/v2_login_backend/` — login backend 独立进程
- `examples/v2_room_backend/` — room backend 独立进程（5 handlers + forward 级联指令）
- `examples/v2_battle_backend/` — battle backend 独立进程（3 handlers + push_to_sessions 广播）
- `tests/v2/integration/backend_routing_test.cpp` — 18 个集成测试（12 原始 + 6 S3）

前置基础已具备：

- ingress / diagnostics / actor 边界 / battle world 基础（6-system ECS pipeline + authoritative simulation）
- 拆服相关抽象占位（`ServiceRouter`/`InternalBus`/`ServiceRegistry`/`BackendRouter`）
- 多个 demo 入口和独立可执行样例

因此，当前更准确的判断应是：

> **S0-S4 服务拆分全部完成，v2 第一版服务拆分（`v2-service-split-plan.md` 第 8 节定义）已满足全部 5 条完成定义：gateway 是唯一客户端接入层、login/room/battle 三个域均作为独立 backend 进程跑通、backend 使用独立 BackendEnvelope、有 22 个真实多进程集成测试、管理口可观察 backend 健康/路由/错误语义。**

## 8. 第一版完成定义

只有同时满足以下条件，才能把“v2 第一版服务拆分”判为完成：

1. `gateway` 成为唯一客户端接入层
2. 至少 `login` 域已经作为独立 backend 进程跑通真实请求闭环
3. backend 使用独立 envelope，不再直接消费客户端 packet
4. 有真实多进程集成测试，而不是只靠单进程单测
5. 管理口能观察 backend 健康、路由和错误语义

在这之前，不应把当前状态表述为“多进程后端服务模块已完成”。
