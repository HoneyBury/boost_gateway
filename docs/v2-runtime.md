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

它还没有实现：

- `ask()` / request-response future
- timer / delayed message
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
- 玩家断线触发 battle finish 与 room/player 回切

## 4. 当前限制

- 所有 dispatch 都在单线程内完成
- battle lifecycle 只覆盖 bootstrap 级最小路径
- runtime 里仍有较多 orchestration 逻辑，尚未拆出更清晰的 domain coordinator
- 没有多核 I/O、无锁队列和背压治理

## 5. 下一步建议

runtime 下一阶段应优先补：

1. battle 结束分支扩展
2. actor timer / delayed message
3. battle 状态广播与 frame 驱动壳
4. 与现有 `GatewayServer` 的桥接缝，而不是直接替换入口
