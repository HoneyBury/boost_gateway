# v2 runtime 原型说明

## 1. 当前定位

当前 `v2` runtime 是一个 **单进程、单线程、可测试优先** 的 actor 原型，不是 `v2-roadmap` 中完整的多核 actor runtime。

它已经实现：

- `Actor`
- `ActorRef`
- `MessageHeader` / `Message`
- 单线程 mailbox
- `ActorSystem::dispatch_all()`
- 本地 `tell()` 投递
- 基于 dispatch round 的最小 delayed message
- 基于 `steady_clock` 的最小 wall-clock delayed message

它还没有实现：

- `ask()` / request-response future
- 可取消、可周期化的正式 timer / scheduler
- supervisor 树
- 跨线程 / 跨核 actor 调度
- 远程 actor 位置透明

## 2. 当前结构

当前最小链路是：

```text
SessionAdapter
  -> GatewayActor
  -> Runtime
  -> PlayerActor / RoomActor / BattleActor
```

说明：

- `GatewayActor` 只做 ingress 过滤和协议翻译
- `Runtime` 当前承担 bootstrap 编排责任
- 业务状态仍由各自 actor 拥有，不由 `Runtime` 直接保存业务对象细节

## 3. 已验证能力

截至 `2026-05-09`，当前 runtime 已通过的验证包括：

- actor 间消息投递
- 登录、顶号、恢复最小闭环
- 创建房间 / 加房 / ready / 开战最小闭环
- battle input 受理和跨 session push
- battle frame 推进与基于 frame limit 的正常结束
- battle 主动结束请求 `finish:<reason>`
- battle settlement 预备事件与 `finished` 前置 push
- room/player 已可消费 battle settlement typed event，并在 finish 前完成最小状态收口
- runtime 已归档最小 battle result summary 和 replay payload，能接到现有 `ReplayPlayer` 读取链
- 玩家断线触发 battle finish 与 room/player 回切
- battle finish reason 已切到最小枚举语义，但外部协议仍保持字符串兼容层
- battle wire body 已集中到统一 codec，并已有字段级 parse/format 回归测试
- battle wire body 已拆出独立 parser / validator，bridge 和测试可共用字段模型
- gateway command body 已补最小 parser / validator，当前覆盖 login / room id / ready state / battle start / battle input
- actor runtime 已支持最小 `send_after()` / `tell_after()` 能力
- actor runtime 已支持可取消的单次 wall-clock schedule 和最小 repeating schedule

## 4. 当前限制

- 所有 dispatch 都在单线程内完成
- battle lifecycle 只覆盖 bootstrap 级最小路径
- battle 已支持最小 frame 推进和主动结束请求，但 frame 驱动仍是 prototype 级壳
- runtime 里仍有较多 orchestration 逻辑，尚未拆出更清晰的 domain coordinator
- 没有多核 I/O、无锁队列和背压治理
- scheduler 当前还不支持 actor-owned handle、统一 tick ownership 和更复杂的 backpressure / drift 策略
- battle wire schema 虽已稳定到键值字符串，但还没有独立 versioning

## 5. 下一步建议

runtime 下一阶段应优先补：

1. battle result summary 继续延伸到更真实的结算、战报和回放持久化入口
2. 把当前 scheduler 继续收口到 actor-owned handle 和统一 tick ownership
3. battle frame 驱动从 prototype 壳走向可替换的 authoritative loop
4. 把 runtime 内 orchestration 继续从入口逻辑里剥离
5. 与现有 `GatewayServer` 的桥接缝保持旁路，不直接替换入口
