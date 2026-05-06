# v2.0.0 设计文档

> **仓库说明**：本文档为 **`v2.0.0` 设计草案**；**在 `v1.2.0` 决策点之前不进入 v2 范畴的开发**，详见 `docs/v2-roadmap.md` 与 `docs/development-priority.md`。

## 1. 文档目标

本文档用于定义 `v2.0.0` 的核心设计草案，重点回答：

- `Actor + ECS` 混合架构在本项目中的具体落地方式
- `PlayerActor`、`RoomActor`、`BattleActor`、`ECS World` 的职责边界
- 核心消息模型、状态模型、生命周期模型
- 新旧架构之间的桥接方式

本文档不直接提供完整代码实现，而是作为后续代码目录初始化、接口编写、测试编写和迁移实施的设计依据。

---

## 2. 设计原则

`v2.0.0` 设计遵循以下原则：

1. 单一状态拥有权  
   任一业务状态只能有一个主拥有者。

2. 消息驱动，不直接跨域改状态  
   Actor 之间通过消息协作，不允许像 `v1.x` 那样跨 manager 双写状态。

3. 高频仿真与业务生命周期分层  
   battle 外层生命周期由 Actor 负责，battle 内部高频模拟由 ECS 负责。

4. 兼容迁移优先  
   `v2.0.0` 设计必须考虑 `v1.x` 客户端协议和网关入口的平滑迁移。

5. 可测试优先  
   所有核心模块都必须能脱离真实网络独立测试。

---

## 3. 总体结构

### 3.1 分层图

```text
Client
  -> SessionAdapter
  -> GatewayActor
  -> PlayerActor
  -> RoomActor
  -> BattleActor
       -> ECS World
       -> FramePipeline
  -> PersistenceActor / Data Layer
  -> Cluster Router / Control Plane
```

### 3.2 角色分工

- `SessionAdapter`
  - 负责网络连接与 Actor 消息入口/出口转换
- `GatewayActor`
  - 负责 ingress 过滤、协议兼容、路由到目标 Actor
- `PlayerActor`
  - 负责玩家登录态、在线态、恢复态
- `RoomActor`
  - 负责房间成员关系、房主、ready、开战资格
- `BattleActor`
  - 负责战斗生命周期、ECS world tick、战斗外部消息边界
- `ECS World`
  - 负责战斗内实体更新和帧模拟

---

## 4. Actor Runtime 最小接口

### 4.1 Actor 基类

建议最小接口如下：

```cpp
class Actor {
public:
    virtual ~Actor() = default;

    virtual void on_start() {}
    virtual void on_stop() {}
    virtual void on_message(Message&& msg) = 0;
    virtual void on_timeout(TimerId timer_id) {}

    ActorId id() const noexcept;
    ActorRef self() const noexcept;
    ActorRef parent() const noexcept;

protected:
    void tell(const ActorRef& target, Message msg);
    Future<ResponseMessage> ask(const ActorRef& target, RequestMessage msg, Duration timeout);
    TimerId schedule(Duration delay, Message msg);
    void cancel_timer(TimerId timer_id);
};
```

### 4.2 ActorRef

```cpp
class ActorRef {
public:
    ActorId actor_id() const noexcept;
    NodeId node_id() const noexcept;
    bool is_local() const noexcept;

    void tell(Message msg) const;
    Future<ResponseMessage> ask(RequestMessage msg, Duration timeout) const;
};
```

### 4.3 Message 基础结构

建议不要继续沿用“原始 message_id + string body”作为 Actor 内部主消息模型。  
内部消息应切换为带类型标签和 payload 的结构。

```cpp
struct MessageHeader {
    MessageKind kind;
    TraceId trace_id;
    RequestId request_id;
    ActorId source_actor;
    ActorId target_actor;
    Timestamp created_at;
};

struct Message {
    MessageHeader header;
    MessagePayload payload;
};
```

其中 `MessagePayload` 建议采用：

- `std::variant<...>` typed payload
- 或 schema id + arena-backed payload

---

## 5. SessionAdapter 与 GatewayActor

### 5.1 SessionAdapter 职责

`SessionAdapter` 是 `v1.x Session` 与 `v2.0.0 Actor` 之间的桥。

负责：

- 接收网络包
- 解码协议
- 转换为 Actor 外部消息
- 将 Actor 输出转换为网络响应或 push
- 维护连接级 trace/context

不负责：

- 登录状态决策
- 房间状态
- 战斗状态

### 5.2 GatewayActor 职责

`GatewayActor` 是 ingress 入口控制者。

负责：

- 登录前白名单
- 基础限频
- 协议兼容分发
- `session -> player_actor` 路由
- 未建模消息拒绝

不负责：

- 玩家业务状态
- 房间业务规则
- 战斗业务规则

### 5.3 外部消息模型

建议定义一层外部协议消息：

```cpp
struct ClientEnvelope {
    SessionId session_id;
    uint16_t protocol_message_id;
    uint32_t request_id;
    int32_t error_code;
    uint8_t flags;
    std::string body;
};
```

`GatewayActor` 将 `ClientEnvelope` 转换成内部消息。

---

## 6. PlayerActor 设计

## 6.1 职责

`PlayerActor` 是玩家状态的唯一拥有者。

负责：

- 登录态
- 当前连接绑定关系
- display name / user id / token 状态
- 当前房间引用
- 当前战斗引用
- 恢复态与顶号逻辑

不负责：

- 房间成员列表
- 房间 ready 状态全集
- 战斗仿真细节

### 6.2 最小状态

```cpp
enum class PlayerLifecycleState {
    kOffline,
    kAuthenticating,
    kOnlineIdle,
    kInRoom,
    kInBattle,
    kSuspended
};

struct PlayerSessionBinding {
    SessionId session_id;
    ConnectionId connection_id;
    Timestamp bound_at;
};

struct PlayerRuntimeState {
    PlayerLifecycleState lifecycle = PlayerLifecycleState::kOffline;
    std::string user_id;
    std::string display_name;
    std::optional<PlayerSessionBinding> binding;
    std::optional<ActorRef> room_actor;
    std::optional<ActorRef> battle_actor;
    std::optional<TokenMeta> token_meta;
    std::optional<ResumeMeta> resume_meta;
};
```

### 6.3 核心消息

#### 输入消息

```cpp
struct LoginRequestMsg {
    SessionId session_id;
    std::string user_id;
    std::string token;
    std::optional<std::string> display_name;
};

struct BindSessionMsg {
    SessionId session_id;
    ConnectionId connection_id;
};

struct KickForDuplicateLoginMsg {
    SessionId old_session_id;
    SessionId new_session_id;
};

struct RoomAssignedMsg {
    ActorRef room_actor;
    RoomId room_id;
};

struct BattleAssignedMsg {
    ActorRef battle_actor;
    BattleId battle_id;
};

struct SessionClosedMsg {
    SessionId session_id;
    CloseReason reason;
};
```

#### 输出消息

```cpp
struct LoginAcceptedMsg {
    SessionId session_id;
    std::string user_id;
    std::string display_name;
    std::optional<RoomId> room_id;
};

struct SessionKickPushMsg {
    SessionId session_id;
    KickReason reason;
};

struct SessionResumePushMsg {
    SessionId session_id;
    RoomId room_id;
    bool in_battle = false;
};
```

### 6.4 状态转换

建议状态流转：

```text
Offline
  -> Authenticating
  -> OnlineIdle
  -> InRoom
  -> InBattle
  -> InRoom
  -> OnlineIdle
  -> Offline
```

特殊分支：

- 顶号：`Online* -> Suspended -> Rebind -> Online*`
- 断线待恢复：`InRoom/InBattle -> Suspended`

### 6.5 PlayerActor 测试重点

- 登录成功/失败
- 顶号替换
- 重连恢复
- room/battle 引用一致性
- session close 后状态迁移

---

## 7. RoomActor 设计

## 7.1 职责

`RoomActor` 是房间状态的唯一拥有者。

负责：

- 房间成员关系
- 房主
- ready 状态
- 房间广播视图
- 开战资格判断
- room -> battle 过渡

不负责：

- 玩家登录态
- 战斗输入处理
- ECS 仿真

### 7.2 最小状态

```cpp
struct RoomMemberState {
    ActorRef player_actor;
    std::string user_id;
    std::string display_name;
    bool ready = false;
    bool online = true;
    Timestamp joined_at;
};

enum class RoomLifecycleState {
    kOpen,
    kPreparing,
    kStartingBattle,
    kInBattle,
    kClosing
};

struct RoomRuntimeState {
    RoomId room_id;
    RoomLifecycleState lifecycle = RoomLifecycleState::kOpen;
    std::string owner_user_id;
    std::vector<RoomMemberState> members;
    std::optional<ActorRef> battle_actor;
};
```

### 7.3 核心消息

#### 输入消息

```cpp
struct CreateRoomMsg {
    ActorRef owner_actor;
    std::string owner_user_id;
    std::string display_name;
    RoomId room_id;
};

struct JoinRoomMsg {
    ActorRef player_actor;
    std::string user_id;
    std::string display_name;
};

struct LeaveRoomMsg {
    ActorRef player_actor;
    std::string user_id;
    LeaveReason reason;
};

struct SetReadyMsg {
    ActorRef player_actor;
    std::string user_id;
    bool ready;
};

struct StartBattleMsg {
    ActorRef requester;
    std::string user_id;
};

struct BattleStartedMsg {
    ActorRef battle_actor;
    BattleId battle_id;
};

struct BattleEndedMsg {
    BattleId battle_id;
};
```

#### 输出消息

```cpp
struct RoomJoinAcceptedMsg {
    RoomId room_id;
    uint32_t player_count;
};

struct RoomStateBroadcastMsg {
    RoomId room_id;
    std::string owner_user_id;
    std::vector<std::string> member_ids;
    std::vector<std::string> ready_ids;
    bool in_battle = false;
};

struct StartBattleApprovedMsg {
    RoomId room_id;
    std::vector<PlayerDescriptor> players;
};
```

### 7.4 RoomActor 行为原则

- 不持有裸 `Session`
- 不依赖 `SessionManager` 回查成员身份
- battle 状态只通过 `battle_actor` 引用表达
- 广播视图基于 room 自身状态生成

### 7.5 RoomActor 测试重点

- 建房/加房/离房
- 房主切换
- ready 状态流转
- 开战前条件验证
- room -> battle 生命周期切换

---

## 8. BattleActor 设计

## 8.1 职责

`BattleActor` 是战斗生命周期拥有者。

负责：

- 战斗启动
- 战斗参与者登记
- ECS World 创建与驱动
- battle tick 调度
- 输入投递
- replay 事件流输出
- 结算与 battle 结束

不负责：

- 房间成员资格来源
- 登录态鉴权
- 低层网络发送

### 8.2 最小状态

```cpp
enum class BattleLifecycleState {
    kCreated,
    kInitializing,
    kRunning,
    kFinishing,
    kFinished,
    kAborted
};

struct BattleParticipant {
    ActorRef player_actor;
    std::string user_id;
    uint32_t slot = 0;
    bool online = true;
};

struct BattleRuntimeState {
    BattleId battle_id;
    RoomId room_id;
    BattleLifecycleState lifecycle = BattleLifecycleState::kCreated;
    std::vector<BattleParticipant> participants;
    uint64_t next_input_seq = 1;
    uint32_t frame_number = 0;
    Timestamp started_at;
    std::optional<ReplayStreamId> replay_stream_id;
};
```

### 8.3 核心消息

#### 输入消息

```cpp
struct InitializeBattleMsg {
    BattleId battle_id;
    RoomId room_id;
    std::vector<PlayerDescriptor> players;
};

struct SubmitBattleInputMsg {
    ActorRef player_actor;
    std::string user_id;
    std::string payload;
    ClientTick client_tick;
};

struct TickBattleMsg {
    uint32_t frame_number;
};

struct PlayerDisconnectedMsg {
    std::string user_id;
};

struct EndBattleMsg {
    BattleEndReason reason;
};
```

#### 输出消息

```cpp
struct BattleStartedBroadcastMsg {
    BattleId battle_id;
    RoomId room_id;
    uint32_t player_count;
};

struct BattleInputAcceptedMsg {
    BattleId battle_id;
    uint64_t input_seq;
};

struct BattleFrameBroadcastMsg {
    BattleId battle_id;
    uint32_t frame_number;
    FrameDelta delta;
};

struct BattleFinishedMsg {
    BattleId battle_id;
    BattleResultSummary result;
};
```

### 8.4 BattleActor 行为原则

- `BattleActor` 只处理战斗生命周期，不直接把 ECS 内部对象暴露给外界
- 外部只通过 battle message 与 `BattleActor` 通信
- replay 输出由 `BattleActor` 统一驱动
- battle 主事实源由 `BattleActor` 独占

### 8.5 BattleActor 测试重点

- 初始化
- 输入投递
- tick 推进
- battle 结束
- replay 输出
- 玩家断线对 battle 的影响

---

## 9. ECS World 设计

## 9.1 职责

`ECS World` 是 battle 内部高频仿真内核。

负责：

- entity / component 管理
- frame 内系统执行
- AOI 查询
- deterministic tick
- 生成 frame snapshot / delta

不负责：

- 房间权限
- 连接状态
- 顶号与恢复策略
- battle 生命周期调度

### 9.2 基础对象

```cpp
using EntityId = uint32_t;

struct EntityHandle {
    EntityId id;
    uint32_t generation;
};

class World {
public:
    EntityHandle create_entity();
    void destroy_entity(EntityHandle entity);

    template <typename T, typename... Args>
    T& add_component(EntityHandle entity, Args&&... args);

    template <typename T>
    T* get_component(EntityHandle entity);

    void tick(const FrameContext& frame_ctx);
    FrameSnapshot snapshot() const;
    FrameDelta build_delta_since(FrameNumber frame) const;
};
```

### 9.3 基础组件建议

第一版建议只保留最小闭环组件：

- `TransformComponent`
- `VelocityComponent`
- `InputBufferComponent`
- `HealthComponent`
- `TeamComponent`
- `SkillStateComponent`
- `AoiNodeComponent`

### 9.4 基础系统建议

第一版建议系统顺序：

1. `InputCollectSystem`
2. `MovementSystem`
3. `SkillApplySystem`
4. `DamageResolveSystem`
5. `DeathCleanupSystem`
6. `AoiUpdateSystem`
7. `SnapshotEmitSystem`

### 9.5 FramePipeline

建议用统一 pipeline 表达每帧顺序：

```cpp
class FramePipeline {
public:
    void run(World& world, const FrameContext& ctx) {
        input_collect_.run(world, ctx);
        movement_.run(world, ctx);
        skill_apply_.run(world, ctx);
        damage_resolve_.run(world, ctx);
        death_cleanup_.run(world, ctx);
        aoi_update_.run(world, ctx);
        snapshot_emit_.run(world, ctx);
    }
};
```

### 9.6 ECS 测试重点

- entity 生命周期
- 组件增删
- system 顺序
- deterministic tick
- snapshot / delta 构造
- AOI 查询正确性

---

## 10. 核心消息流

## 10.1 登录流

```text
SessionAdapter
  -> GatewayActor
  -> PlayerActor(LoginRequestMsg)
  -> Auth policy / token verifier
  -> PlayerActor(LoginAcceptedMsg)
  -> SessionAdapter
```

## 10.2 建房流

```text
PlayerActor
  -> RoomActor(CreateRoomMsg)
  -> RoomActor(RoomJoinAcceptedMsg)
  -> RoomStateBroadcastMsg
```

## 10.3 开战流

```text
PlayerActor(owner)
  -> RoomActor(StartBattleMsg)
  -> RoomActor(StartBattleApprovedMsg)
  -> BattleActor(InitializeBattleMsg)
  -> BattleStartedBroadcastMsg
  -> PlayerActor(BattleAssignedMsg)
```

## 10.4 战斗输入流

```text
SessionAdapter
  -> GatewayActor
  -> PlayerActor
  -> BattleActor(SubmitBattleInputMsg)
  -> ECS World InputBuffer
  -> TickBattleMsg
  -> BattleFrameBroadcastMsg
```

## 10.5 结算与回放流

```text
BattleActor
  -> ECS World final snapshot
  -> ReplayWriter
  -> BattleFinishedMsg
  -> RoomActor(BattleEndedMsg)
  -> PlayerActor state update
```

---

## 11. 兼容迁移设计

### 11.1 兼容层目标

`v2.0.0` 不能一开始就要求所有客户端和所有业务入口重写。

需要以下兼容层：

- `v1_protocol_adapter`
- `v1_session_bridge`
- `v1_gateway_adapter`

### 11.2 迁移顺序

建议按下面顺序迁移：

1. `Session` -> `SessionAdapter`
2. `SessionManager` -> `PlayerActor`
3. `RoomManager` -> `RoomActor`
4. `BattleManager` -> `BattleActor shell`
5. `BattleActor shell` -> `BattleActor + ECS World`

### 11.3 迁移期规则

- 不允许新功能同时写旧 manager 状态和新 actor 状态
- 迁移期任何业务事实源都必须明确“当前由谁拥有”
- compat 层必须显式记录哪些消息还走旧链

---

## 12. 测试设计要求

### 12.1 单元测试

- `PlayerActor`：
  - 登录成功/失败
  - 顶号
  - 恢复
- `RoomActor`：
  - 成员增删
  - 房主切换
  - ready
  - 开战资格
- `BattleActor`：
  - 初始化
  - 输入接收
  - tick
  - 结算
- `ECS World`：
  - entity
  - components
  - systems
  - deterministic

### 12.2 集成测试

- 登录 -> 建房 -> 加房 -> 准备 -> 开战 -> 输入 -> 结算
- 顶号恢复 -> 重新绑定 room / battle
- battle replay save/load/replay

### 12.3 回归测试

- 协议兼容
- shutdown/restore
- replay 格式版本兼容
- snapshot 恢复兼容

### 12.4 性能测试

- Actor 吞吐
- mailbox 背压
- battle tick
- AOI 查询
- replay 生成开销

---

## 13. 第一版最小实现边界

为了避免 `v2.0.0` 第一版过度膨胀，建议第一版只做：

- Actor runtime 最小闭环
- PlayerActor / RoomActor / BattleActor 最小实现
- ECS 最小 battle 闭环
- replay 最小生成/读取闭环
- TLS / token / admin policy 只做主链骨架，不追求完备控制面

第一版不建议强行完成：

- 全量 AOI 大系统
- 完整分布式集群
- 完整灰度发布系统
- 复杂技能系统
- 全部业务一次性迁移

---

## 14. 结论

`v2.0.0` 设计的核心不在于“把现在的 manager 改个名字”，而在于真正完成以下三件事：

1. 用 Actor 重建状态拥有权和生命周期
2. 用 ECS 承担 battle 高频仿真内核
3. 用 compat 层保证迁移期间系统仍然可运行、可测试、可回归

只有这三件事成立，`v2.0.0` 才不是概念升级，而是可执行的架构升级。

---

## 15. 头文件级接口清单

本节用于把四个核心模块进一步拆到“建议头文件”和“最小公开接口”粒度，便于后续开始建目录和写代码。

## 15.1 `game/player/`

建议文件：

```text
include/game/player/
├── player_actor.h
├── player_state.h
├── player_messages.h
├── player_types.h
└── player_view.h
```

### `player_types.h`

建议放纯数据类型：

```cpp
using PlayerId = std::string;
using SessionId = std::uint64_t;
using ConnectionId = std::uint64_t;

enum class PlayerLifecycleState {
    kOffline,
    kAuthenticating,
    kOnlineIdle,
    kInRoom,
    kInBattle,
    kSuspended,
};

enum class KickReason {
    kDuplicateLogin,
    kAdminKick,
    kProtocolViolation,
    kTransportClosed,
};
```

### `player_state.h`

建议只定义玩家运行时状态和快照结构：

```cpp
struct PlayerSessionBinding {
    SessionId session_id = 0;
    ConnectionId connection_id = 0;
    Timestamp bound_at{};
};

struct PlayerRuntimeState {
    PlayerLifecycleState lifecycle = PlayerLifecycleState::kOffline;
    PlayerId player_id;
    std::string display_name;
    std::optional<PlayerSessionBinding> binding;
    std::optional<ActorRef> room_actor;
    std::optional<ActorRef> battle_actor;
    std::optional<TokenMeta> token_meta;
    std::optional<ResumeMeta> resume_meta;
};

struct PlayerSnapshot {
    PlayerId player_id;
    std::string display_name;
    PlayerLifecycleState lifecycle;
    std::optional<RoomId> room_id;
    std::optional<BattleId> battle_id;
};
```

### `player_messages.h`

建议集中定义 `PlayerActor` 直接处理的消息：

```cpp
struct LoginRequestMsg;
struct LoginAcceptedMsg;
struct LoginRejectedMsg;
struct BindSessionMsg;
struct SessionClosedMsg;
struct KickForDuplicateLoginMsg;
struct RoomAssignedMsg;
struct RoomClearedMsg;
struct BattleAssignedMsg;
struct BattleClearedMsg;
struct SessionKickPushMsg;
struct SessionResumePushMsg;
```

要求：

- 输入消息与输出消息可分组
- 外部协议消息不要直接混入内部状态消息

### `player_view.h`

建议只提供只读视图：

```cpp
struct PlayerPublicView {
    PlayerId player_id;
    std::string display_name;
    bool online = false;
    bool in_room = false;
    bool in_battle = false;
};
```

### `player_actor.h`

建议最小公开接口：

```cpp
class PlayerActor final : public runtime::actor::Actor {
public:
    explicit PlayerActor(PlayerRuntimeState initial_state = {});

    void on_start() override;
    void on_stop() override;
    void on_message(runtime::actor::Message&& msg) override;
    void on_timeout(runtime::actor::TimerId timer_id) override;

    [[nodiscard]] PlayerSnapshot snapshot() const;
    [[nodiscard]] PlayerPublicView public_view() const;

private:
    void handle_login_request(const LoginRequestMsg& msg);
    void handle_bind_session(const BindSessionMsg& msg);
    void handle_session_closed(const SessionClosedMsg& msg);
    void handle_room_assigned(const RoomAssignedMsg& msg);
    void handle_room_cleared(const RoomClearedMsg& msg);
    void handle_battle_assigned(const BattleAssignedMsg& msg);
    void handle_battle_cleared(const BattleClearedMsg& msg);
    void handle_duplicate_kick(const KickForDuplicateLoginMsg& msg);

    void transition_to(PlayerLifecycleState next);
    void emit_login_success(SessionId session_id);
    void emit_login_failure(SessionId session_id, ErrorCode code);
    void emit_resume_if_needed(SessionId session_id);

private:
    PlayerRuntimeState state_;
};
```

---

## 15.2 `game/room/`

建议文件：

```text
include/game/room/
├── room_actor.h
├── room_state.h
├── room_messages.h
├── room_types.h
└── room_view.h
```

### `room_types.h`

```cpp
using RoomId = std::string;

enum class RoomLifecycleState {
    kOpen,
    kPreparing,
    kStartingBattle,
    kInBattle,
    kClosing,
};

enum class LeaveReason {
    kVoluntary,
    kDisconnected,
    kKicked,
    kRoomClosing,
};
```

### `room_state.h`

```cpp
struct RoomMemberState {
    ActorRef player_actor;
    PlayerId player_id;
    std::string display_name;
    bool ready = false;
    bool online = true;
    Timestamp joined_at{};
};

struct RoomRuntimeState {
    RoomId room_id;
    RoomLifecycleState lifecycle = RoomLifecycleState::kOpen;
    PlayerId owner_player_id;
    std::vector<RoomMemberState> members;
    std::optional<ActorRef> battle_actor;
};

struct RoomSnapshot {
    RoomId room_id;
    PlayerId owner_player_id;
    RoomLifecycleState lifecycle;
    std::vector<PlayerId> member_ids;
    std::vector<PlayerId> ready_ids;
    std::optional<BattleId> battle_id;
};
```

### `room_messages.h`

```cpp
struct CreateRoomMsg;
struct JoinRoomMsg;
struct LeaveRoomMsg;
struct SetReadyMsg;
struct StartBattleMsg;
struct BattleStartedMsg;
struct BattleEndedMsg;
struct RoomJoinAcceptedMsg;
struct RoomJoinRejectedMsg;
struct RoomStateBroadcastMsg;
struct StartBattleApprovedMsg;
struct StartBattleRejectedMsg;
```

### `room_view.h`

```cpp
struct RoomPublicView {
    RoomId room_id;
    PlayerId owner_player_id;
    std::size_t player_count = 0;
    bool in_battle = false;
};
```

### `room_actor.h`

```cpp
class RoomActor final : public runtime::actor::Actor {
public:
    explicit RoomActor(RoomRuntimeState initial_state = {});

    void on_start() override;
    void on_stop() override;
    void on_message(runtime::actor::Message&& msg) override;

    [[nodiscard]] RoomSnapshot snapshot() const;
    [[nodiscard]] RoomPublicView public_view() const;

private:
    void handle_create_room(const CreateRoomMsg& msg);
    void handle_join_room(const JoinRoomMsg& msg);
    void handle_leave_room(const LeaveRoomMsg& msg);
    void handle_set_ready(const SetReadyMsg& msg);
    void handle_start_battle(const StartBattleMsg& msg);
    void handle_battle_started(const BattleStartedMsg& msg);
    void handle_battle_ended(const BattleEndedMsg& msg);

    [[nodiscard]] bool can_start_battle(PlayerId requester) const;
    [[nodiscard]] std::vector<PlayerDescriptor> build_battle_roster() const;
    void broadcast_room_state();
    void reassign_owner_if_needed();
    void transition_to(RoomLifecycleState next);

private:
    RoomRuntimeState state_;
};
```

---

## 15.3 `game/battle/actor/`

建议文件：

```text
include/game/battle/actor/
├── battle_actor.h
├── battle_state.h
├── battle_messages.h
├── battle_types.h
├── battle_view.h
└── battle_result.h
```

### `battle_types.h`

```cpp
using BattleId = std::string;
using FrameNumber = std::uint32_t;
using InputSequence = std::uint64_t;

enum class BattleLifecycleState {
    kCreated,
    kInitializing,
    kRunning,
    kFinishing,
    kFinished,
    kAborted,
};

enum class BattleEndReason {
    kNormalFinish,
    kPlayerDisconnect,
    kRoomClosed,
    kAdminStop,
    kInternalError,
};
```

### `battle_state.h`

```cpp
struct BattleParticipant {
    ActorRef player_actor;
    PlayerId player_id;
    std::uint32_t slot = 0;
    bool online = true;
};

struct BattleRuntimeState {
    BattleId battle_id;
    RoomId room_id;
    BattleLifecycleState lifecycle = BattleLifecycleState::kCreated;
    std::vector<BattleParticipant> participants;
    FrameNumber frame_number = 0;
    InputSequence next_input_seq = 1;
    Timestamp started_at{};
    std::optional<ReplayStreamId> replay_stream_id;
};

struct BattleSnapshot {
    BattleId battle_id;
    RoomId room_id;
    BattleLifecycleState lifecycle;
    FrameNumber frame_number;
    std::vector<PlayerId> participants;
};
```

### `battle_result.h`

```cpp
struct BattleScore {
    PlayerId player_id;
    std::int64_t score = 0;
};

struct BattleResultSummary {
    BattleId battle_id;
    RoomId room_id;
    BattleEndReason reason;
    std::optional<PlayerId> winner_player_id;
    std::vector<BattleScore> scores;
    FrameNumber total_frames = 0;
};
```

### `battle_messages.h`

```cpp
struct InitializeBattleMsg;
struct SubmitBattleInputMsg;
struct TickBattleMsg;
struct PlayerDisconnectedMsg;
struct EndBattleMsg;
struct BattleStartedBroadcastMsg;
struct BattleInputAcceptedMsg;
struct BattleFrameBroadcastMsg;
struct BattleFinishedMsg;
```

### `battle_view.h`

```cpp
struct BattlePublicView {
    BattleId battle_id;
    RoomId room_id;
    BattleLifecycleState lifecycle;
    std::size_t player_count = 0;
    FrameNumber frame_number = 0;
};
```

### `battle_actor.h`

```cpp
class BattleActor final : public runtime::actor::Actor {
public:
    BattleActor(BattleRuntimeState initial_state,
                std::unique_ptr<ecs::World> world,
                std::unique_ptr<ecs::FramePipeline> pipeline);

    void on_start() override;
    void on_stop() override;
    void on_message(runtime::actor::Message&& msg) override;
    void on_timeout(runtime::actor::TimerId timer_id) override;

    [[nodiscard]] BattleSnapshot snapshot() const;
    [[nodiscard]] BattlePublicView public_view() const;

private:
    void handle_initialize(const InitializeBattleMsg& msg);
    void handle_submit_input(const SubmitBattleInputMsg& msg);
    void handle_tick(const TickBattleMsg& msg);
    void handle_player_disconnected(const PlayerDisconnectedMsg& msg);
    void handle_end_battle(const EndBattleMsg& msg);

    void start_tick_loop();
    void run_frame(FrameNumber frame);
    void emit_frame_broadcast(const ecs::FrameDelta& delta);
    void emit_battle_result(const BattleResultSummary& result);
    void transition_to(BattleLifecycleState next);

private:
    BattleRuntimeState state_;
    std::unique_ptr<ecs::World> world_;
    std::unique_ptr<ecs::FramePipeline> pipeline_;
};
```

---

## 15.4 `game/battle/ecs/`

建议文件：

```text
include/game/battle/ecs/
├── world.h
├── entity.h
├── registry.h
├── frame_context.h
├── frame_snapshot.h
├── frame_delta.h
├── frame_pipeline.h
├── components/
│   ├── transform_component.h
│   ├── velocity_component.h
│   ├── input_buffer_component.h
│   ├── health_component.h
│   ├── team_component.h
│   ├── skill_state_component.h
│   └── aoi_node_component.h
├── systems/
│   ├── input_collect_system.h
│   ├── movement_system.h
│   ├── skill_apply_system.h
│   ├── damage_resolve_system.h
│   ├── death_cleanup_system.h
│   ├── aoi_update_system.h
│   └── snapshot_emit_system.h
└── aoi/
    ├── aoi_index.h
    ├── cell_coord.h
    └── view_query.h
```

### `entity.h`

```cpp
using EntityId = std::uint32_t;

struct EntityHandle {
    EntityId id = 0;
    std::uint32_t generation = 0;

    [[nodiscard]] bool valid() const noexcept { return id != 0; }
};
```

### `frame_context.h`

```cpp
struct FrameContext {
    BattleId battle_id;
    RoomId room_id;
    FrameNumber frame_number = 0;
    std::chrono::milliseconds tick_interval{33};
    Timestamp started_at{};
    DeterministicRng* rng = nullptr;
};
```

### `frame_snapshot.h`

```cpp
struct EntityStateView;

struct FrameSnapshot {
    FrameNumber frame_number = 0;
    std::vector<EntityStateView> entities;
};
```

### `frame_delta.h`

```cpp
struct EntityDeltaView;

struct FrameDelta {
    FrameNumber frame_number = 0;
    std::vector<EntityDeltaView> changed_entities;
    std::vector<EntityId> removed_entities;
};
```

### `world.h`

```cpp
class World {
public:
    World();

    EntityHandle create_entity();
    void destroy_entity(EntityHandle entity);
    [[nodiscard]] bool exists(EntityHandle entity) const;

    template <typename Component, typename... Args>
    Component& add_component(EntityHandle entity, Args&&... args);

    template <typename Component>
    Component* get_component(EntityHandle entity);

    template <typename Component>
    const Component* get_component(EntityHandle entity) const;

    template <typename Component>
    bool remove_component(EntityHandle entity);

    FrameSnapshot snapshot() const;
    FrameDelta delta_since(FrameNumber frame) const;

private:
    Registry registry_;
    FrameNumber last_snapshot_frame_ = 0;
};
```

### `frame_pipeline.h`

```cpp
class FramePipeline {
public:
    FramePipeline();

    void run(World& world, const FrameContext& ctx);

private:
    systems::InputCollectSystem input_collect_;
    systems::MovementSystem movement_;
    systems::SkillApplySystem skill_apply_;
    systems::DamageResolveSystem damage_resolve_;
    systems::DeathCleanupSystem death_cleanup_;
    systems::AoiUpdateSystem aoi_update_;
    systems::SnapshotEmitSystem snapshot_emit_;
};
```

---

## 15.5 目录初始化建议顺序

为了减少一次性创建太多空壳文件，建议按下面顺序初始化：

1. `player_types.h`
2. `player_state.h`
3. `player_messages.h`
4. `player_actor.h`
5. `room_types.h`
6. `room_state.h`
7. `room_messages.h`
8. `room_actor.h`
9. `battle_types.h`
10. `battle_state.h`
11. `battle_messages.h`
12. `battle_result.h`
13. `battle_actor.h`
14. `entity.h`
15. `frame_context.h`
16. `frame_snapshot.h`
17. `frame_delta.h`
18. `world.h`
19. `frame_pipeline.h`

这样可以先把“Actor 外壳”冻结，再补 ECS 内核接口。

---

## 15.6 接口冻结建议

在开始编码前，建议先冻结以下内容：

1. `PlayerLifecycleState`
2. `RoomLifecycleState`
3. `BattleLifecycleState`
4. `PlayerActor` 输入/输出消息清单
5. `RoomActor` 输入/输出消息清单
6. `BattleActor` 输入/输出消息清单
7. `World` 的最小 CRUD 接口
8. `FramePipeline` 的系统执行顺序

如果这 8 项不先冻结，后续实现很容易一边写一边改消息边界，导致 `v2` 设计再次漂移。
