# v2 下一阶段边界

## 1. 当前结论

截至 `2026-05-09`，`v2` 已经具备：

- `M1` 可运行原型
- demo 入口
- battle bootstrap lifecycle
- `GatewayServer` 旁路 bridge seam

但这仍然 **不代表** 项目已经进入 `M2-M7` 的实作阶段。

## 2. 下一阶段顺序

后续建议顺序如下：

1. `M1` 收口
2. `M2` 多核 I/O 引擎
3. `M6` battle runtime 壳到 ECS world
4. `M5` 数据层 v2
5. `M4` 分布式原语
6. `M3` 内存架构重构
7. `M7` 运维成熟度

说明：

- `M6` 在优先级上高于 `M4/M5/M7`，因为 battle 仍是当前 `v2` 最大空洞
- `M3` 很重要，但不应在 battle 主链未成型前提前大改分配模型

## 3. 各模块进入门槛

### 3.1 `M2` 多核 I/O

进入前至少满足：

- battle lifecycle 已稳定
- `GatewayServer` bridge seam 已有最小灰度方式
- v1/v2 smoke test 都可持续运行

当前不做：

- `SO_REUSEPORT`
- actor 亲核调度
- 跨核 mailbox

### 3.2 `M6` ECS world / battle runtime

进入前至少满足：

- battle start / input / frame / finish 协议已经稳定
- `PlayerActor` / `RoomActor` / `BattleActor` 边界已经固定
- frame push 语义不再频繁变化

当前不做：

- ECS component/system
- deterministic simulation
- replay stream

### 3.3 `M5` 数据层 v2

进入前至少满足：

- room / battle lifecycle 已固定
- 需要持久化的状态集合已经冻结

当前不做：

- snapshot schema
- replay 持久化
- battle result 持久化

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
2. 提前做分布式和多核
3. 在 battle 主链没定型前做大规模内存优化
4. 把 `battle_state` 字符串 body 过早冻结成最终协议

## 5. 当前值得继续做的是什么

如果继续沿当前代码推进，最值得继续做的是：

1. battle 正常结束分支扩展
2. frame push 语义稳定化
3. `GatewayServer` shadow bridge 的灰度接入测试
4. battle runtime 壳向 ECS world 过渡前的消息清单冻结
