# v2 PlayerActor 生命周期

## 1. 目标

`PlayerActor` 当前是玩家状态的唯一拥有者。

它已经负责：

- session 绑定
- 登录态
- 顶号替换
- 房间引用
- battle 引用
- 断线挂起
- battle 结束回切

## 2. 当前状态

```text
Offline
  -> Authenticating
  -> OnlineIdle
  -> InRoom
  -> InBattle
  -> Suspended
```

## 3. 当前转移规则

### 3.1 登录

- `BindSessionMsg` 后进入 `Authenticating`
- `LoginRequestMsg` 成功后：
  - 无房间：进入 `OnlineIdle`
  - 已有房间：进入 `InRoom`

### 3.2 顶号

- 新 session 登录同一 `user_id` 时：
  - 旧 session 收到 `SessionKickPush`
  - 新 session 成为当前 binding

### 3.3 进房

- `RoomAssignedMsg` 到达后：
  - 写入 `room_actor_id`
  - 写入 `room_id`
  - 进入 `InRoom`

### 3.4 进战

- `BattleAssignedMsg` 到达后：
  - 写入 `battle_actor_id`
  - 写入 `battle_id`
  - 进入 `InBattle`

### 3.5 断线

- 当前绑定 session 关闭后：
  - 清空 binding
  - 有房间时进入 `Suspended`
  - 无房间时回到 `Offline`

### 3.6 战斗结束

- `BattleEndedMsg` 到达后：
  - 清空 `battle_actor_id`
  - 清空 `battle_id`
  - 有房间则回切到 `InRoom`
  - 无房间则回切到 `OnlineIdle`

## 4. 当前限制

- 当前没有 token metadata、resume metadata 的完整模型
- 当前没有 battle 内断线保活策略，断线即走 battle finish 最小闭环
- 当前没有玩家级 timer / reconnect window
