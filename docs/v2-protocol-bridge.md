# v2 协议桥接说明

## 1. 目标

`v2` 当前继续兼容 `v1` 外部协议包格式，不修改客户端入口协议。

外部入口仍然是：

```text
[4字节总长度]
[2字节消息号]
[4字节请求序号]
[1字节标记位]
[N字节消息体]
```

内部主消息模型已经切到 typed payload。

## 2. 当前桥接分层

```text
net::Session
  -> SessionAdapter
  -> ClientEnvelope
  -> GatewayActor
  -> GatewayCommand
  -> Runtime
  -> typed actor message
```

当前职责边界：

- `net::Session`
  - 负责收包 / 发包 / close handler
- `SessionAdapter`
  - 负责网络包与 `ClientEnvelope` / `SessionWrite` 转换
- `GatewayActor`
  - 负责登录前白名单、限频钩子、未建模消息拒绝
- `Runtime`
  - 负责把 gateway command 编排到 `PlayerActor` / `RoomActor` / `BattleActor`

## 3. 当前已建模消息

- `kLoginRequest`
- `kRoomCreateRequest`
- `kRoomJoinRequest`
- `kRoomReadyRequest`
- `kBattleStartRequest`
- `kBattleInputRequest`
- `kEchoRequest`

当前 push / response 已覆盖：

- `kLoginResponse`
- `kRoomCreateResponse`
- `kRoomJoinResponse`
- `kRoomReadyResponse`
- `kBattleStartResponse`
- `kBattleInputResponse`
- `kBattleInputPush`
- `kBattleStatePush`
- `kSessionKickedPush`
- `kSessionResumedPush`
- `kErrorResponse`

## 4. 当前 battle 相关 body 约定

当前仍使用字符串 body，主要约定如下：

- `battle_started:{room_id}:{battle_id}`
- `battle_state:{room_id}:{battle_id}`
- `input_seq:{seq}`
- `{user_id}:{seq}:{input_data}`
- `battle_end_accepted:{reason}`
- `battle_finished:{room_id}:{battle_id}:{reason}:{user_id}`

当前用于主动结束 battle 的临时约定：

- `finish:surrender`
- `finish:timeout`
- `finish:<custom_reason>`

这些格式当前只用于 demo / prototype，不应视为最终 `v2` battle 协议。

## 5. 当前限制

- `GatewayActor` 仍只是最小 ingress actor
- `Runtime` 仍承担了较重的编排职责
- battle body 仍是临时字符串协议，不是最终 typed external schema
- 还没有接入现有 `GatewayServer` 主链
