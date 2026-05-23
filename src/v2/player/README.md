# v2 Player Sources

当前目录主要包含：

- `player_actor.cpp`
  管理玩家登录、顶号、房间归属、战斗归属、挂起/恢复与战斗结束后的状态回切

当前主线状态：

- `PlayerActor` 已与 `Runtime`、`RoomActor`、`BattleActor` 完整接线
- `SessionKickPush` / `SessionResumePush` / `BattleSettlementAppliedMsg` 已被测试覆盖
- 在 v3.4.0 阶段，玩家链路更多工作集中在验证与桥接层，而不是 Actor 本身结构变化
