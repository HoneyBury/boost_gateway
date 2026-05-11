# v2 下一阶段边界

## 1. 当前结论

截至 `2026-05-11`，`v2` 已经不再只是 `M1` bootstrap 原型，而是进入了：

- `M1` 收口完成态
- `M2` 多核 I/O 基础设施首轮落地
- `M6` battle runtime 向 ECS world 的最小迁移

当前已稳定具备：

- `ActorSystem`、`PlayerActor`、`RoomActor`、`BattleActor` 最小闭环
- `DemoServer` 与 `GatewayServer` 均可走 `IoEngine` ingress
- session-core aware outbound、core diagnostics、pinned listen、multi-listener ingress
- `BattleActor` 主要承担编排，runtime state 已可从 world 构造
- `v1` 管理口与 `v2_gateway_demo` 已能输出结构化 diagnostics

但这仍然 **不代表** 项目已经完成：

- `SO_REUSEPORT`
- 跨核 mailbox / actor 亲核调度
- battle authoritative simulation
- 数据层 v2 / 分布式 / 内存架构重构 / 运维控制面正式化

## 2. 下一阶段顺序

后续建议顺序如下：

1. 继续收束 `M2` 的 ingress / accept policy / 观测一致性
2. 继续压缩 `BattleActor`，把 battle runtime 主事实源下沉到 `world/system`
3. 进入 `M5` 数据层 v2，先做 replay / result / snapshot 的落盘边界
4. 再评估 `M4` 分布式原语
5. 之后再进入 `M3` 内存架构重构
6. `M7` 运维成熟度放在入口和 battle 主链都稳定之后

说明：

- `M2` 还没做完，但“可运行最小版”已经落地，后续应聚焦真正的 listener / accept policy 和更深多核能力，而不是重复做接缝
- `M6` 已进入 world 化阶段，但 battle 仍未形成完整 authoritative simulation，因此仍高于 `M4/M5/M7`
- `M5` 的优先级已高于 `M3`，因为 replay/result 已经有 runtime 事实源，开始具备落盘边界
- `M3` 很重要，但不应在 battle 主链和 ingress 形态尚未最终定型前提前大改分配模型

## 3. 各模块进入门槛

### 3.1 `M2` 多核 I/O

已满足的进入条件：

- battle lifecycle 已稳定
- `GatewayServer` bridge seam 已有最小灰度方式
- v1/v2 smoke test 都可持续运行

当前已完成：

- `AsioIoEngine`
- `dispatch_to_core()` / `dispatch_to_all_cores()`
- pinned listen / multi-listener ingress
- `GatewayServer` / `DemoServer` 的 `IoEngine` 接入
- session-core aware outbound
- core diagnostics 与 management snapshot

当前仍未做：

- `SO_REUSEPORT`
- actor 亲核调度
- 跨核 mailbox

下一步应聚焦：

- 多 acceptor / accept policy 真正组装
- `shadow bridge` / `v2 demo` 的 core 观测继续统一
- 是否值得进入 `SO_REUSEPORT` 试验

### 3.2 `M6` ECS world / battle runtime

已满足的进入条件：

- battle start / input / frame / finish 协议已经稳定
- `PlayerActor` / `RoomActor` / `BattleActor` 边界已经固定
- frame push 语义不再频繁变化

当前已完成：

- `SimpleWorld`
- battle metadata / participant / replay / score / frame runtime component
- `BattleActor` 按需从 world 构造 runtime state
- result summary / frame-limit finish / replay input 收口到 world helper

当前仍未做：

- deterministic simulation
- 真正的 game system 拆分
- authoritative simulation / AOI

### 3.3 `M5` 数据层 v2

进入前至少满足：

- room / battle lifecycle 已固定到可落盘边界
- replay / result / snapshot 的事实源已经能从 runtime state 明确导出

当前建议先做：

- replay 持久化 schema
- battle result archive
- world snapshot 的最小存储模型

### 3.4 `M4` 分布式原语

进入前至少满足：

- 单进程 battle/runtime 已经跑顺
- ingress / player / room / battle actor 的本地边界稳定

当前不做：

- cluster router
- service discovery
- remote actor transport

### 3.5 `M3` 内存架构重构

进入前至少满足：

- battle 高频对象模型成型
- 确认热路径在哪里

当前不做：

- arena allocator
- object pool hierarchy
- false sharing 优化专项

### 3.6 `M7` 运维成熟度

进入前至少满足：

- 主入口切换策略清晰
- smoke test / integration test 可持续
- metrics 需要扩展到 v2 主链

当前不做：

- OpenTelemetry
- K8s operator
- 灰度控制面

## 4. 当前最值得做的不是哪些

当前最不值得提前做的是：

1. 直接把 `v2` 切成默认入口
2. 在没有 accept policy 结论前直接冲 `SO_REUSEPORT`
3. 在 battle 主链没定型前做大规模内存优化
4. 把 `battle_state` 字符串 body 过早冻结成最终协议
5. 在 replay/result 还没落盘前就提前做分布式 battle

## 5. 当前值得继续做的是什么

如果继续沿当前代码推进，最值得继续做的是：

1. 多 listener / pinned listener 的配置化和运维可见性
2. battle runtime 剩余镜像状态继续 world 化
3. replay / result / snapshot 的数据层入口
4. `GatewayServer` / `shadow bridge` / `v2 demo` 的诊断口径统一
