# v2 GatewayServer 接入门槛

## 1. 当前结论

截至 `2026-05-09`，`v2` **还不能直接替换** 现有 `GatewayServer` 主链。

当前推荐形态仍然是：

- 保留 `v1` 默认入口
- 使用 `examples/v2_gateway_demo/` 作为 `v2` bootstrap 入口
- 通过独立测试和文档推进 `v2` 原型，而不是直接切主链
- 如需在现有主链上试验，仅允许启用 **startup-only** 的 `v2 shadow bridge`

## 2. 当前还差什么

以下条件未满足前，不应把 `SessionAdapter` / `GatewayActor` 接入现有主链：

1. `BattleActor` 还没有完整 lifecycle
2. battle 还没有 authoritative simulation / tick / replay
3. 没有把 `v1` 现有 gateway / room / battle 护栏完整映射到 `v2`
4. 没有完成 `GatewayServer` 与 `Runtime` 的责任拆分方案
5. 没有完成最小回滚策略

## 3. 允许接入前的最低门槛

建议至少满足以下门槛：

### 3.1 功能门槛

- 登录 / 顶号 / 重连
- 创建房间 / 加房 / ready / 开战
- battle start / input / finish
- battle finish 后 room/player 状态回切

### 3.2 测试门槛

- `v2` 真实 socket smoke test 通过
- `v1` 最低护栏集持续通过
- 新增一组“主链接入但不开启切流”的桥接测试

### 3.3 结构门槛

- `SessionAdapter` 可挂到现有 `Session`
- `GatewayActor` ingress 规则不弱于 `v1`
- `Runtime` 编排逻辑已和入口解耦
- battle body 协议至少冻结到可回归状态

当前已具备的最小试验缝：

- `GatewayServer::set_packet_bridge(...)`
- `gateway.v2_shadow_bridge_enabled`
- `gateway.v2_shadow_bridge_emit_responses`
- `gateway.v2_shadow_bridge_login`
- `gateway.v2_shadow_bridge_room`
- `gateway.v2_shadow_bridge_battle`
- `gateway.v2_shadow_bridge_echo`
- `gateway.v2_shadow_bridge_emit_battle_input_push`
- `gateway.v2_shadow_bridge_emit_battle_state_started`
- `gateway.v2_shadow_bridge_emit_battle_state_frame`
- `gateway.v2_shadow_bridge_emit_battle_state_settlement`
- `gateway.v2_shadow_bridge_emit_battle_state_finished`

当前限制：

- 只建议启动时配置
- 不支持热更新切换
- 默认仍应保持关闭
- 当前仍建议优先镜像 `login / room`，再逐步放开 `battle`
- response mirror 当前主要细化到 battle 域，还没有推广到全部 v1/v2 response family
- gateway command parser 当前只覆盖最小 ingress 字段和 battle request 归一化，不应误判为已冻结完整 external schema

## 4. 推荐接入顺序

不要直接替换主入口，建议顺序如下：

1. 保持 `DemoServer` 独立演化
2. 给现有 `GatewayServer` 增加一个可关闭的 `v2 bridge` 试验缝
3. 先接 login / room，再评估是否打开 battle mirror
4. 最后再评估 battle 是否可迁移

## 5. 当前优先级判断

在现阶段，主链接入优先级低于：

1. battle result / replay 入口继续收口
2. timer / delayed message 从当前 scheduler 原型推进到 actor-owned 调度能力
3. battle frame loop 与 external schema 继续稳定
4. v2 文档、配置和测试护栏固定
