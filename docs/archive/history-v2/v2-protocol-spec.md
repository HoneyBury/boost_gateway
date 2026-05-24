# v2 客户端协议规范

> 版本: v2.1.0-draft
> 更新日期: 2026-05-12
> 目标受众: 客户端开发者、后端服务开发者

## 1. 概述

本文档定义 BoostAsioDemo v2 游戏服务器框架的外部客户端协议。协议基于 TCP 长连接，采用长度前缀 + 消息号 + 请求序号的二进制帧格式。

### 1.1 设计原则

- **兼容性**: v2 协议向后兼容 v1.x 客户端包格式，现有客户端无需修改
- **请求/响应模型**: 每个客户端请求携带 request_id，服务器响应携带相同的 request_id
- **服务端推送**: 服务器可以主动推送消息（不携带 request_id，使用 0）
- **错误透传**: 所有错误通过统一的 ErrorResponse 返回，error_code 明确表达错误类型

---

## 2. 帧格式

### 2.1 基础帧格式

所有消息使用统一的二进制帧封装：

```
┌──────────────────┬──────────────────┬──────────────────┬──────────────────┬──────────────────┬──────────────────┐
│  4 bytes         │  2 bytes         │  4 bytes         │  4 bytes         │  1 byte          │  N bytes         │
│  总长度(total)   │  消息号(msg_id)  │  请求序号(req_id) │  错误码(err_code) │  标记位(flags)   │  消息体(body)    │
├──────────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┼──────────────────┤
│  uint32_t BE     │  uint16_t BE     │  uint32_t BE     │  int32_t BE      │  uint8_t         │  字符串/JSON     │
└──────────────────┴──────────────────┴──────────────────┴──────────────────┴──────────────────┴──────────────────┘
```

**总长度** = 2 + 4 + 4 + 1 + N = 11 + body 长度

**字段说明**:

| 字段 | 类型 | 字节序 | 说明 |
|------|------|--------|------|
| total | uint32_t | Big Endian | 帧总长度（包含自身 4 字节） |
| msg_id | uint16_t | Big Endian | 消息类型编号，见 §3 消息目录 |
| req_id | uint32_t | Big Endian | 请求序号，由客户端自增。服务端推送时为 0 |
| err_code | int32_t | Big Endian | 错误码。请求消息为 0，响应消息为对应错误码（见 §4） |
| flags | uint8_t | — | 标记位。当前仅使用 bit 0（压缩标记） |
| body | string | — | 消息体，UTF-8 字符串。格式依赖消息类型 |

### 2.2 标记位

| 位 | 名称 | 说明 |
|----|------|------|
| 0 | kCompressed | body 是否为 zlib 压缩（当前暂未启用压缩传输） |
| 1-7 | 保留 | 必须为 0 |

### 2.3 请求-响应关联

- 客户端发送请求时，`err_code` 字段设为 0
- 服务器响应时，`req_id` 与请求相同，`err_code` 表示结果
- 服务端主动推送时，`req_id` 设为 0
- 客户端应维护自增的 `request_id`，范围 [1, 2^32-1]

---

## 3. 消息目录

### 3.1 消息号一览

| 消息号 | 常量名 | 方向 | 说明 |
|--------|--------|------|------|
| 1 | kHeartbeatRequest | C→S | 心跳请求 |
| 2 | kHeartbeatResponse | S→C | 心跳响应 |
| 1001 | kEchoRequest | C→S | Echo 测试请求 |
| 1002 | kEchoResponse | S→C | Echo 测试响应 |
| 1003 | kSessionKickedPush | S→C | 被踢下线推送 |
| 1004 | kSessionResumedPush | S→C | 会话恢复推送 |
| 2001 | kLoginRequest | C→S | 登录请求 |
| 2002 | kLoginResponse | S→C | 登录响应 |
| 3001 | kRoomCreateRequest | C→S | 创建房间请求 |
| 3002 | kRoomCreateResponse | S→C | 创建房间响应 |
| 3003 | kRoomJoinRequest | C→S | 加入房间请求 |
| 3004 | kRoomJoinResponse | S→C | 加入房间响应 |
| 3005 | kRoomLeaveRequest | C→S | 离开房间请求 |
| 3006 | kRoomLeaveResponse | S→C | 离开房间响应 |
| 3007 | kRoomReadyRequest | C→S | 准备/取消准备请求 |
| 3008 | kRoomReadyResponse | S→C | 准备/取消准备响应 |
| 3009 | kRoomStatePush | S→C | 房间状态广播推送 |
| 4001 | kBattleStartRequest | C→S | 开始战斗请求 |
| 4002 | kBattleStartResponse | S→C | 开始战斗响应 |
| 4003 | kBattleInputRequest | C→S | 战斗输入请求 |
| 4004 | kBattleInputResponse | S→C | 战斗输入响应 |
| 4005 | kBattleInputPush | S→C | 战斗输入广播推送 |
| 4006 | kBattleStatePush | S→C | 战斗状态推送 |
| 5001 | kAdminKickPlayer | C→S | 管理：踢出玩家 |
| 5002 | kAdminBanIp | C→S | 管理：封禁 IP |
| 5003 | kAdminServerStatus | C→S | 管理：服务器状态 |
| 5004 | kAdminReloadConfig | C→S | 管理：重载配置 |
| 5005 | kAdminResponse | S→C | 管理：命令响应 |
| 9001 | kErrorResponse | S→C | 通用错误响应 |

### 3.2 消息分组

| 分组 | 消息号范围 | 包含消息 |
|------|-----------|---------|
| 系统 | 1-2 | 心跳 |
| 测试 | 1001-1004 | Echo、踢线推送、恢复推送 |
| 登录 | 2001-2002 | 登录 |
| 房间 | 3001-3009 | 创建、加入、离开、准备、状态推送 |
| 战斗 | 4001-4006 | 开始、输入、输入推送、状态推送 |
| 管理 | 5001-5005 | 踢人、封禁、状态、重载 |
| 错误 | 9001 | 通用错误响应 |

---

## 4. 错误码

### 4.1 错误码表

| 错误码 | 常量名 | 说明 |
|--------|--------|------|
| 0 | kOk | 成功 |
| 1001 | kAuthRequired | 需要认证（未登录即发送业务消息） |
| 1002 | kInvalidUserId | 无效的用户 ID |
| 1003 | kInvalidToken | 无效的 Token |
| 1004 | kDuplicateLogin | 重复登录（同一用户在新连接登录，旧连接被踢） |
| 1005 | kTokenExpired | Token 已过期 |
| 2001 | kInvalidRoomId | 无效的房间 ID |
| 2002 | kRoomAlreadyExists | 房间已存在 |
| 2003 | kRoomNotFound | 房间不存在 |
| 2004 | kRoomInBattle | 房间已在战斗中 |
| 2005 | kNotInRoom | 不在房间中 |
| 2006 | kNotRoomOwner | 不是房主 |
| 2007 | kNotAllReady | 未全部准备就绪 |
| 2008 | kLoginBackendUnavailable | 登录后端不可用（优雅降级） |
| 2009 | kRoomBackendUnavailable | 房间后端不可用（优雅降级） |
| 3001 | kNotEnoughPlayers | 玩家数量不足 |
| 3002 | kBattleAlreadyStarted | 战斗已经开始 |
| 3003 | kBattleNotStarted | 战斗未开始 |
| 3004 | kPlayerNotInBattle | 玩家不在战斗中 |
| 3010 | kBattleBackendUnavailable | 战斗后端不可用（优雅降级） |
| 9001 | kRateLimited | 请求被限频 |
| 9002 | kSessionNotFound | 会话未找到 |

### 4.2 错误响应格式

当服务器返回非零错误码时，使用 `kErrorResponse` (9001) 消息：

```
msg_id: 9001 (kErrorResponse)
req_id: 与原始请求相同
err_code: 具体错误码（见上表）
body: 可选的错误描述文本
```

---

## 5. 协议详细说明

### 5.1 登录协议

#### 登录请求 (kLoginRequest, 2001)

客户端发送登录请求。连接建立后首先发送此消息。

```
msg_id: 2001
req_id: 客户端自增序列号
err_code: 0
body: user_id=<user_id>:token=<token>
```

body 字段（key:value 格式，冒号分隔）:

| 键 | 必填 | 说明 |
|----|------|------|
| user_id | 是 | 用户唯一标识符 |
| token | 是 | 认证令牌。开发模式格式: `token:<user_id>` |

示例 body: `user_id:alice:token:token:alice`

#### 登录成功响应 (kLoginResponse, 2002)

```
msg_id: 2002
req_id: 与请求相同
err_code: 0
body: user_id=<user_id>:display_name=<name>:room_id=<room_id>
```

| 键 | 说明 |
|----|------|
| user_id | 登录的用户 ID |
| display_name | 显示名称 |
| room_id | 如果用户在房间中（恢复登录），返回房间 ID；否则为空 |

示例 body: `user_id:alice:display_name:Alice:room_id:room_alpha`

#### 会话恢复推送 (kSessionResumedPush, 1004)

当用户重新登录并恢复到之前的房间时，服务器推送此消息：

```
msg_id: 1004
req_id: 0
err_code: 0
body: session_id=<sid>:room_id=<room_id>:in_battle=<0|1>
```

#### 被踢下线推送 (kSessionKickedPush, 1003)

当用户在新设备登录导致旧连接被踢时：

```
msg_id: 1003
req_id: 0
err_code: 0
body: old_session_id=<old_sid>:new_session_id=<new_sid>
```

---

### 5.2 房间协议

#### 创建房间 (kRoomCreateRequest, 3001)

```
msg_id: 3001
req_id: 客户端自增
err_code: 0
body: room_id=<room_id>
```

body 字段: `room_id` — 房间唯一标识符

**错误码**: kInvalidRoomId (2001), kRoomAlreadyExists (2002)

#### 加入房间 (kRoomJoinRequest, 3003)

```
msg_id: 3003
req_id: 客户端自增
err_code: 0
body: room_id=<room_id>
```

**错误码**: kRoomNotFound (2003), kRoomInBattle (2004)

#### 离开房间 (kRoomLeaveRequest, 3005)

```
msg_id: 3005
req_id: 客户端自增
err_code: 0
body: room_id=<room_id>
```

**错误码**: kNotInRoom (2005)

#### 准备状态 (kRoomReadyRequest, 3007)

```
msg_id: 3007
req_id: 客户端自增
err_code: 0
body: ready=<1|0>
```

**错误码**: kNotInRoom (2005)

#### 房间状态推送 (kRoomStatePush, 3009)

服务器主动推送房间成员状态变更：

```
msg_id: 3009
req_id: 0
err_code: 0
body: room_id=<room_id>:owner_id=<user_id>:members=<user1,user2,...>:ready=<user1,user2,...>:in_battle=<0|1>
```

---

### 5.3 战斗协议

#### 开始战斗 (kBattleStartRequest, 4001)

只有房主可以发起：

```
msg_id: 4001
req_id: 客户端自增
err_code: 0
body: room_id=<room_id>
```

**错误码**: kNotRoomOwner (2006), kNotAllReady (2007), kNotEnoughPlayers (3001), kBattleAlreadyStarted (3002), kRoomInBattle (2004)

#### 战斗状态推送 (kBattleStatePush, 4006)

服务器推送战斗状态变更，使用 kind 区分类型：

```
msg_id: 4006
req_id: 0
err_code: 0
body: battle_state:kind=<kind>:room_id=<room_id>:battle_id=<battle_id>[:...]
```

kind 类型:

| kind | 说明 | 额外字段 |
|------|------|---------|
| started | 战斗开始 | — |
| frame | 帧推进 | frame=N |
| settlement | 结算 | reason=R, user_id=U |
| finished | 战斗结束 | reason=R, user_id=U |

示例 body:
- 战斗开始: `battle_state:kind=started:room_id=room_alpha:battle_id=battle_0001`
- 帧推进: `battle_state:kind=frame:room_id=room_alpha:battle_id=battle_0001:frame=150`
- 结算: `battle_state:kind=settlement:room_id=room_alpha:battle_id=battle_0001:reason=surrender:user_id=alice`
- 结束: `battle_state:kind=finished:room_id=room_alpha:battle_id=battle_0001:reason=surrender:user_id=alice`

#### 战斗输入 (kBattleInputRequest, 4003)

```
msg_id: 4003
req_id: 客户端自增
err_code: 0
body: battle_input:user_id=<user_id>:seq=<n>:input=<data>
```

| 字段 | 说明 |
|------|------|
| user_id | 发送输入的玩家 ID |
| seq | 客户端本地输入序列号 |
| input | 游戏输入数据（格式由具体游戏定义） |

#### 战斗输入响应 (kBattleInputResponse, 4004)

```
msg_id: 4004
req_id: 与请求相同
err_code: 0（成功）或错误码
body: input_seq:seq=<n>
```

#### 战斗主动结束 (通过 kBattleInputRequest)

在 v2 中，战斗没有独立的"结束战斗"消息。客户端通过发送特殊 input 来请求结束：

```
body: finish:<reason>
```

reason 可选值:
- `surrender` — 投降
- `timeout` — 超时
- `finished` — 正常结束

---

### 5.4 Echo 测试协议

#### Echo 请求 (kEchoRequest, 1001)

```
msg_id: 1001
req_id: 客户端自增
err_code: 0
body: 任意字符串
```

#### Echo 响应 (kEchoResponse, 1002)

```
msg_id: 1002
req_id: 与请求相同
err_code: 0
body: 与请求 body 相同
```

---

### 5.5 心跳协议

#### 心跳请求 (kHeartbeatRequest, 1)

```
msg_id: 1
req_id: 任何值（建议 0）
err_code: 0
body: 空
```

建议心跳间隔: 15-30 秒

#### 心跳响应 (kHeartbeatResponse, 2)

```
msg_id: 2
req_id: 与请求相同
err_code: 0
body: 空
```

---

## 6. 状态机

### 6.1 连接生命周期

```
         TCP 连接建立
              │
              ▼
         ┌─────────┐
         │ 已连接   │
         └────┬────┘
              │ 发送 LoginRequest
              ▼
         ┌─────────┐
         │ 已登录   │◄──────── 重连恢复
         └────┬────┘
              │
         ┌────┴────┐
         ▼         ▼
    ┌────────┐ ┌────────┐
    │ 在房间 │ │ 空闲   │
    └───┬────┘ └────────┘
        │
        ▼
    ┌────────┐
    │ 在战斗 │
    └───┬────┘
        │ 战斗结束
        ▼
    ┌────────┐
    │ 在房间 │ (回切)
    └────────┘
```

### 6.2 玩家生命周期

```
Offline ──BindSession──▶ Authenticating ──LoginRequest──▶ OnlineIdle
                                                              │
                                              RoomAssigned ◄──┘
                                                              ▼
                                                           InRoom
                                                              │
                                              BattleAssigned ◄┘
                                                              ▼
                                                          InBattle
                                                              │
                                            SessionClosed ◄───┴──▶ Suspended
                                                                      │
                                         Reconnect (30s 窗口内) ◄──────┘
                                                                      │
                                         Timeout (30s 后) ◄───────────┴──▶ Offline
```

### 6.3 房间生命周期

```
Open ──SetReady×N──▶ (全部准备) ──StartBattle──▶ Preparing
                                                      │
                                                      ▼
                                              StartingBattle
                                                      │
                                                      ▼
                                                  InBattle
                                                      │
                                           BattleEnded ◄──▶ Open (重置 ready)
```

### 6.4 战斗生命周期

```
Created ──CreateBattle──▶ Running ──Tick/Input──▶ Running
                               │                      ▲
                               │ Disconnect           │ Reconnect (15s 内)
                               ▼                      │
                           (宽限期 15s) ───────────────┘
                               │ (超时)
                               ▼
                           Finished
                               │
                               ▼
                      Settlement + Finished events
```

---

## 7. 重连协议

### 7.1 重连流程

当客户端 TCP 连接意外断开时：

1. 服务器将玩家状态设置为 `kSuspended`，启动 30 秒重连窗口
2. 如果玩家在战斗中，战斗将启动 15 秒断开宽限期（战斗继续运行）
3. 客户端应尽快重新建立 TCP 连接
4. 客户端发送 `LoginRequest` 使用相同的 user_id 和 token
5. 服务器识别为恢复登录，发送 `LoginResponse` 包含 `room_id`，后跟 `SessionResumedPush`
6. 如果在战斗中，客户端还需要等待 `BattleStatePush` 恢复战斗状态

### 7.2 重连窗口

| 超时 | 时长 | 触发操作 |
|------|------|---------|
| 玩家重连窗口 | 30 秒 | 过期后玩家状态转为 kOffline，不可恢复 |
| 战斗断开宽限期 | 15 秒 | 过期后战斗以 kPlayerDisconnected 结束 |

---

## 8. 限频与安全

### 8.1 速率限制

服务器在网关层实施全局速率限制。当客户端超过速率限制时，服务器返回 `kErrorResponse` 携带 `kRateLimited` (9001) 错误码。

当前默认限制:

| 限制类型 | 默认值 | 说明 |
|---------|--------|------|
| 全局消息速率 | 可配置 | 每连接每秒消息数上限 |
| 连接数上限 | 可配置 | 网关最大并发连接数 |

### 8.2 认证

当前使用开发模式的简单 Token 验证:
- Token 格式: `token:<user_id>`
- 生产环境应升级为 JWT RS256（规划于 v2.2.0）

### 8.3 输入校验

服务器对所有客户端输入进行服务端权威校验:
- 战斗输入: 校验帧序号，拒绝重复帧和过期帧
- 移动距离/速度/冷却时间校验（规划于 v2.3.0）

---

## 9. 协议版本策略

### 9.1 当前版本

- 协议版本: v2.0（兼容 v1.x 客户端包格式）
- 帧格式未变，内部消息模型已切换到 typed payload
- 配置文件版本: 独立演进，各服务有独立 JSON 配置

### 9.2 向后兼容承诺

- 帧格式（长度+消息号+请求序号+错误码+标记位+消息体）保持兼容
- 消息号分配保持稳定
- body 格式可扩展（新增字段不影响旧客户端）
- 错误码数值保持稳定

### 9.3 未来版本

- v2.2.0: 新增 JWT 认证（token 格式变更，不影响帧格式）
- v2.3.0: 新增 JSON Schema 消息校验
- v3.0.0: 新增远程 Actor transport（帧格式可能增加路由头部）

---

## 10. 实现参考

### 10.1 服务端端口

| 服务 | 默认端口 | 说明 |
|------|---------|------|
| gateway | 9201 | 客户端唯一接入点 |
| login backend | 9202 | 登录后端（仅 gateway 连接） |
| room backend | 9302 | 房间后端（仅 gateway 连接） |
| battle backend | 9303 | 战斗后端（仅 gateway 连接） |
| management HTTP | 9080 | 管理/诊断 HTTP 端口 |

### 10.2 示例客户端流程

```
1. TCP connect to gateway:9201
2. Send: kLoginRequest  { user_id:alice, token:token:alice }
3. Recv: kLoginResponse { user_id:alice, display_name:Alice }
4. Send: kRoomCreateRequest { room_id:room_alpha }
5. Recv: kRoomCreateResponse (OK)
6. ... (other clients join, ready up)
7. Recv: kRoomStatePush { in_battle:0, members:alice,bob, ready:alice,bob }
8. Send: kBattleStartRequest { room_id:room_alpha }
9. Recv: kBattleStartResponse (OK)
10. Recv: kBattleStatePush { kind:started, battle_id:battle_0001 }
11. Send: kBattleInputRequest { battle_input:user_id:alice:seq:1:input:move:1,2 }
12. Recv: kBattleInputResponse { input_seq:seq:1 }
13. Recv: kBattleStatePush { kind:frame, frame:1 }
14. ... (gameplay loop continues)
15. Send: kBattleInputRequest { finish:surrender }
16. Recv: kBattleStatePush { kind:settlement, reason:surrender }
17. Recv: kBattleStatePush { kind:finished, reason:surrender }
```

---

## 附录 A: 相关文档

- `../history-v2/v2-design.md` — v2 架构设计文档
- `../history-v2/v2-roadmap.md` — v2 模块规划
- `../history-v2/v2-player-lifecycle.md` — 玩家生命周期
- `../history-v2/v2-room-lifecycle.md` — 房间生命周期
- `../history-v2/v2-protocol-bridge.md` — 协议桥接说明
- `../history-v2/v2-enterprise-roadmap.md` — 企业级迭代路线
- `docs/architecture-acceptance-criteria.md` — 架构验收标准
- `include/net/protocol.h` — 协议常量定义（代码事实源）
- `include/v2/gateway/message_types.h` — 网关消息类型
