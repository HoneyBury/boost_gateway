# v2 RoomActor 生命周期

## 1. 目标

`RoomActor` 当前是房间状态的唯一事实源。

它已经负责：

- 房主
- 成员列表
- ready 状态
- 开战资格判定
- active battle 标记
- battle 结束后的 ready 清理

## 2. 当前最小状态

- `room_id`
- `owner_user_id`
- `members`
- `active_battle_id`

## 3. 当前行为规则

### 3.1 创建房间

- `CreateRoomMsg`
  - 初始化 `room_id`
  - 初始化 `owner_user_id`
  - 自动把 owner 写入成员列表

### 3.2 加入房间

- `JoinRoomMsg`
  - 若成员不存在则追加
  - 初始 `ready = false`

### 3.3 ready

- `SetReadyMsg`
  - 只修改对应成员的 ready 位

### 3.4 开战判定

当前 `StartBattleMsg` 会按以下顺序校验：

1. 当前不能已有 `active_battle_id`
2. 请求者必须是房主
3. 成员数至少 2
4. 所有成员必须 ready

通过后发出 `BattleStartRequestedMsg`。

### 3.5 battle started

- `BattleStartedMsg`
  - 写入 `active_battle_id`

### 3.6 battle ended

- `BattleEndedMsg`
  - 清空 `active_battle_id`
  - 把所有成员 `ready` 重置为 `false`

## 4. 当前限制

- 当前还没有 leave / kick / owner transfer
- 当前 battle 结束后只做最小重置，不包含结算和房间后处理
- 当前没有 room snapshot / persistence / replay 关联
