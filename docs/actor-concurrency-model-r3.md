# R3 Actor 并发模型与线程边界

> 日期：`2026-05-23`
> 范围：`ActorSystem`、`ActorRef::tell()`、跨 core mailbox、dispatch owner core、shutdown 顺序、debug 断言契约。

## 1. Owner Core 模型

每个 Actor 创建时可通过 `affinity_core` 参数绑定到一个 IO core。ActorSystem 在运行时维护 `dispatch_owner_core_` 状态，标识当前 dispatch 所属的 owner core。

### 核心规则

- **Actor 内部状态只能由 owner core 操作**。Owner core 是指 `dispatch_ready()` 或 `drain_mailbox_and_dispatch()` 调用时传入的 core id。
- `ActorSystem::actors_` map 在 `dispatch_ready()` 执行期间仅由 owner core 读写。
- `ActorSystem::ready_actors_` deque 仅由 owner core 在 dispatch 阶段修改。
- Actor 实例的 `on_start()`、`on_message()`、`on_stop()` 生命周期回调**总是**在 owner core 上同步调用。

### dispatch_owner_core() 返回值

| 调用路径 | dispatch_owner_core() 值 |
|---|---|
| `dispatch_all()` 且 IO engine 有 current core | 当前 IO core id |
| `dispatch_all()` 且没有 IO current core | `nullopt` |
| `drain_mailbox_and_dispatch(core_id)` | 传入的 `core_id` |
| dispatch 结束后（已退出 dispatch_ready） | 恢复为进入 dispatch 前的值 |
| `shutdown()` 后 | `nullopt` |

### Debug 断言保护

```cpp
// 在 dispatch_ready() 入口：
assert(is_on_owner_core());
// 确保如果 dispatch_owner_core_ 已设置（重入场景），当前线程仍在同一 core。

// 在 send() 本地投递路径：
// 确保如果存在 IO engine 且目标有 core affinity，则 dispatch owner core 与目标 core 一致。
// 若不一致，跨核路由应已通过 post_mailbox 转发。
// 到达本地路径却 core 不匹配说明跨核路由被错误绕过。
```

## 2. 跨核投递机制

`ActorRef::tell()` 和 `ActorSystem::send()` 是唯一允许跨线程调用的入口。

### 投递流程

```
发送线程 (core A)
    │
    ├─ ActorRef::tell(message)
    │   └─ system_->send(message)
    │       ├─ 目标 actor 有 core_id?
    │       │   ├─ 否 → 本地投递（fallback，单线程模式或未绑定）
    │       │   └─ 是
    │       │       ├─ current_core_id() 已知?
    │       │       │   ├─ 否 → 本地投递（非 IO 线程上下文）
    │       │       │   └─ 是
    │       │       │       ├─ 等于目标 core → 本地投递（同核）
    │       │       │       └─ 不等于目标 core → 跨核投递
    │       │       └─ 【跨核】io_engine_->post_mailbox(target_core)
    │       │           └─ SPSC mailbox.try_enqueue(message)
    │       │               ├─ 成功 → 消息在目标 core 的 mailox 中等待 drain
    │       │               └─ 失败（满队列）→ 消息静默丢弃（见 §6）
    │       │
    │       └─ 【本地】cell->mailbox.push_back(message)
    │           └─ enqueue_ready_actor() → ready_actors_ 队尾
    │
接收线程 (core B)
    │
    ├─ IoEngine 事件循环每次迭代后:
    │   ├─ SPSC mailbox.drain()
    │   └─ 对每条消息:
    │       ├─ cell->mailbox.push_back()
    │       └─ enqueue_ready_actor()
    │
    ├─ drain_mailbox_and_dispatch(core_id)
    │   ├─ 显式 drain mailbox → 入 actor mailbox → dispatch_ready
    │   └─ 同步批量处理，调用者确保在目标 core 线程上执行
    │
    └─ dispatch_ready()
        ├─ promote_scheduled_messages → 到期消息走 send()
        ├─ 遍历 ready_actors_
        │   ├─ dequeue ready actor
        │   ├─ pop front from actor mailbox
        │   └─ actor->on_message(std::move(message))
        └─ 如果 mailbox 仍非空 → re-enqueue ready actor（公平轮转）
```

### Happens-Before 保证

```
Sender:  message.header.target_actor = X;     [sequenced-before]
         post_mailbox(target_core, message);   [释放 store — write_idx release]
                                                   │
                                                   │ 线程间 synchronizes-with
                                                   ▼
Receiver: SPSC mailbox.try_dequeue()             [获取 load — read_idx acquire]
          → 读取 message 内容                      [happens-before]
          → cell->mailbox.push_back(message)     [sequenced-before]
          → enqueue_ready_actor()                [sequenced-before]
          → dispatch_ready()                     [sequenced-before]
          → actor->on_message(std::move(message)) [最终消费]
```

### 跨核投递 vs 本地投递的断言契约

在 Debug 构建中，如果 `send()` 进入本地投递路径，但 `dispatch_owner_core_` 已设置且与目标 `cell->core_id` 不匹配，assertion 失败。这表示跨核路由应被触发但未被触发——是调度逻辑错误。

## 3. Mailbox Drain 触发点

### 3a. IoEngine 事件循环每次迭代

`AsioIoEngine` 的每个 IO core 运行自己的 `asio::io_context`。事件循环每次迭代后（通过 asio post 编排），core 线程会 drain 自己的 SPSC mailbox：

```cpp
// 概念流程（io_context::run 循环内编排）：
while (!io_context.stopped()) {
    io_context.poll_one();                      // 处理 asio 事件
    auto msgs = cores_[my_id]->mailbox.drain(); // drain 跨核消息
    for (auto& msg : msgs) {
        actor_system_->on_mailbox_message(msg); // push → enqueue_ready
    }
    actor_system_->dispatch_ready(my_id);       // dispatch ready actors
}
```

### 3b. dispatch_all() 同步 drain

`dispatch_all()` 是同步 drain + dispatch 单次调用的入口。它由 owner core 显式调用，通常在测试和 shutdown 场景使用。它同时负责提升 `dispatch_round_` 计数的 scheduled messages。

### 3c. drain_mailbox_and_dispatch() 显式 drain

`drain_mailbox_and_dispatch(core_id)` 由 IO core 线程在事件循环迭代中的编排点调用。它：
1. 从 core 的 SPSC mailbox 批量 drain 所有可用消息（`SpscQueue::drain()`，无上限）
2. 将消息推入目标 actor 的 `ActorCell::mailbox` deque
3. 调用 `dispatch_ready(core_id)` 进行 dispatch

### 批量 drain 策略

- `SpscQueue::drain()` 每次调用 drain 当前 SPSC 队列中**所有**消息（无上限枚举）。
- Actor 级 dispatch 对每个 actor 每次只处理一条消息。如果 actor mailbox 处理完后仍然非空，会被重新加入 `ready_actors_` 队尾，实现公平轮转。
- 同一个 `dispatch_ready()` 循环中，所有 ready actor 都会被至少处理一次。如果持续有新消息入队，dispatch 循环会持续处理直到 `ready_actors_` 为空或 `shutting_down_` 为 true。

## 4. Actor 生命周期线程安全

### create_actor

- 可以在任何线程调用。
- 如果 `shutting_down_` 为 true，返回空 `ActorRef`（静默忽略）。
- 创建的 actor 的 `on_start()` 在 `create_actor()` 调用线程上同步调用——调用者需要确保这发生在正确的上下文中。
- 如果在 IO 线程上下文中调用 `create_actor()`，actor 的 `on_start()` 在该 IO 线程上执行。

### on_message

- 只在 owner core 的 `dispatch_ready()` 循环中调用。
- 从 actor mailbox deque `pop_front()` 出队一条消息后同步调用。
- `on_message()` 执行期间 `dispatch_owner_core_` 保持为 owner core id，actor 可通过 `self().system()->dispatch_owner_core()` 查询。

### stop / destroy

- `shutdown()` 遍历所有 actor 并调用 `cancel_all_owned_schedules()` + `on_stop()`。
- `on_stop()` 调用后 actor map 立即清空。
- 在 `dispatch_ready()` 过程中如果某个 actor 的 `on_message()` 调用了 `shutdown()`，当前消息处理完后 dispatch 循环立即退出，不再继续处理 `ready_actors_` 中剩余的 actor。
- 在 `send()` 中，如果 `shutting_down_` 为 true，消息静默丢弃（不路由不投递）。

### 生命周期安全矩阵

| 操作 | 允许的线程 | 同步/异步 |
|---|---|---|
| `create_actor()` | 任意线程 | 同步调用 on_start |
| `send()` / `tell()` | 任意线程 | 跨核异步（SPSC）、同核同步（deque push） |
| `schedule_after/every` | 任意线程 | 同步插入 scheduled_messages_ |
| `cancel_schedule()` | 任意线程 | 同步从 scheduled_messages_ 移除 |
| `on_message()` | owner core 线程 only | dispatch_ready 循环中同步调用 |
| `on_start()` | create_actor 调用线程 | 同步 |
| `on_stop()` | shutdown 调用线程 | 同步 |
| read `actors_` | dispatch_ready / shutdown | 受 dispatch_owner_core_ 约束 |
| write `actors_` | dispatch_ready / shutdown | 受 dispatch_owner_core_ 约束 |
| SPSC mailbox enqueue | 任意线程 | 无锁（SPSC） |
| SPSC mailbox dequeue | owner core thread only | 无锁（SPSC） |

## 5. Shutdown 顺序

`ActorSystem::shutdown()` 按以下固定顺序执行：

```
1. shutting_down_ = true          // 阻止后续所有 send/create/schedule
2. dispatch_owner_core_.reset()   // 清除 owner core 上下文
3. ready_actors_.clear()          // 清空待调度队列
4. scheduled_messages_.clear()    // 清空所有定时消息和周期调度
5. 遍历 actors_:
   ├─ cell.actor->cancel_all_owned_schedules()
   └─ cell.actor->on_stop()
6. actors_.clear()                // 销毁所有 actor
```

### dispatch 过程中 shutdown 的特殊处理

如果在 `dispatch_ready()` 循环中，某个 actor 的 `on_message()` 调用了 `shutdown()`：

```
dispatch_ready loop:
    message = cell->mailbox.pop_front()
    actor->on_message(std::move(message))
        └─ system_.shutdown()         // 设置 shutting_down_ = true
    // dispatch_ready 检测到 shutting_down_
    // → 恢复 dispatch_owner_core_，return dispatched
    // ready_actors_ 中剩余的 actor 不会被处理
```

### External IO engine 的 shutdown

当 `ActorSystem` 与 `AsioIoEngine` 配合时，外部协调者应保证 shutdown 顺序：

1. 停止 acceptor（停止接受新连接）
2. 通知 IO engine stop（`io_context.stop()`、join threads）
3. 调用 `ActorSystem::shutdown()`
4. 销毁 ActorSystem

## 6. SPSC Mailbox 满队列行为

`SpscQueue<T>` 默认容量为 1024，在 `AsioIoEngine::post_mailbox()` 中调用 `try_enqueue()`：

- 队列满时 `try_enqueue()` 返回 `false`。
- `ActorSystem::send()` **不检查** `post_mailbox()` 的返回值。
- **满队列时跨核消息静默丢弃**。

当前不会阻塞或重试。这是一个已知的丢消息风险点，适用于以下缓解措施：

| 缓解层 | 措施 |
|---|---|
| 容量 | 默认 1024，可通过 SpscQueue 构造参数调整 |
| 背压 | 应用层可检测并通过消息确认重试（不在此版本范围内） |
| 监控 | 可扩展 SPSC 队列 size/capacity 监控指标 |

## 7. 线程封闭对象清单

| 对象 | 线程约束 | 说明 |
|---|---|---|
| `ActorSystem::actors_` map | owner core only（dispatch/shutdown 期间） | 所有 actor cell 的全局注册表 |
| `ActorSystem::ready_actors_` deque | owner core only（dispatch 期间） | 标记为 ready 的 actor id 列表 |
| `ActorSystem::scheduled_messages_` vector | owner core only（dispatch 期间） | 定时消息和周期调度 |
| `ActorSystem::dispatch_owner_core_` optional | dispatch 期间写入，任何线程可读 | 标识当前 dispatch 上下文 |
| `ActorSystem::shutting_down_` bool | 任何线程原子读 | shutdown 标志 |
| `ActorCell::mailbox` deque | owner core push_back + pop_front | actor 级消息队列 |
| `ActorCell::actor` unique_ptr | owner core only | actor 实例的生命周期 |
| ActorCell `queued` / `started` / `core_id` | owner core only | actor 元数据 |
| `Core::mailbox` (SpscQueue) | producer: 任意线程，consumer: owner core only | 跨核 SPSC 通信 |
| `Core::io_context` | owner core only（asio thread） | asio 事件循环 |
| `g_current_io_core_id` (thread_local) | per-thread | 线程局部 IO core id |

## 8. 已固化测试

| 测试 | 覆盖 |
|---|---|
| `CreateActorStartsAndShutdownStops` | 基本生命周期 |
| `DispatchAllDeliversMessagesBetweenActors` | 同核消息投递 |
| `DispatchOwnerCoreReflectsCurrentIoCoreDuringDispatch` | dispatch 中 owner core 正确暴露 |
| `DrainMailboxDispatchUsesDrainedCoreAsOwner` | 跨核 drain 使用目标 core |
| `ShutdownDuringDispatchStopsWithoutDeliveringQueuedTail` | dispatch 中 shutdown 安全退出 |
| `CrossCoreMailboxStressDoesNotDropMessages` | 10K 跨核 tell 不丢消息 |
| `ShutdownDropsQueuedCrossCoreMailboxMessagesSafely` | shutdown 后 mailbox 老消息安全丢弃 |
| `CrossCoreMultiActorStress` | 多 actor 多 core 高频跨核 tell（R3 新增） |
| `ShutdownRaceWithPendingSchedules` | shutdown 与定时调度竞态（R3 新增） |
| `ShutdownRaceWithConcurrentCreateAndSend` | shutdown 与 create/send 竞态（R3 新增） |
| `SpscQueueFullBehaviorDropsMessages` | SPSC 满队列验证丢弃行为（R3 新增） |
