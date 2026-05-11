# v2.0.0 企业级游戏服务器架构规划

## 一、v1.0.0 现状评估

### 已达到的能力

v1.0.0 完成了一个**单进程、功能完整**的游戏服务器框架。核心限制：

| 维度 | v1.0.0 状态 | 企业级要求 |
|---|---|---|
| 并发模型 | 单 io_context + strand 串行化 | 多核并行 + 连接亲和性 |
| 业务抽象 | Session 裸指针 + 函数回调 | Actor 模型 + 状态机 + 监督树 |
| 内存管理 | BufferPool + ObjectPool（全局） | 分层内存：Arena → Pool → 预分配 |
| 数据持久化 | IPlayerStore 接口 + JSON 文件 | 缓存层 + 写后异步 + 快照/恢复 |
| 集群能力 | ServiceRouter + InternalBus（单跳） | 服务发现 + 一致性哈希 + 领导者选举 |
| 运维部署 | Docker + docker-compose | Kubernetes Operator + 灰度/金丝雀 |
| 可观测性 | Prometheus + Grafana + 审计日志 | OpenTelemetry 分布式追踪 + 火焰图 |
| 游戏特性 | 房间/战斗/匹配基础闭环 | AOI 空间管理 + 状态同步 + 反作弊 |

---

## 二、v2.0.0 七大模块

```
┌──────────────────────────────────────────────────────────────────┐
│                        V2.0.0 架构全景                            │
├────────────┬────────────┬────────────┬────────────┬──────────────┤
│  M1        │  M2        │  M3        │  M4        │  M5          │
│  Actor     │  多核 I/O  │  内存架构   │  分布式    │  数据层 v2   │
│  模型      │  引擎      │  重构       │  原语      │              │
├────────────┴────────────┴────────────┴────────────┴──────────────┤
│  M6: AOI 空间管理系统 (游戏特性)                                   │
│  M7: 运维成熟度 (K8s + 灰度 + 追踪)                                │
└──────────────────────────────────────────────────────────────────┘
```

## 二点五、当前实现状态（`2026-05-11`）

> 本文档是长期目标，不等于当前代码完成度。下面这段用于避免把 roadmap 误读成“已实现列表”。

| 模块 | 当前状态 | 说明 |
|---|---|---|
| `M1 Actor` | `in-place done` | `ActorSystem`、`PlayerActor`、`RoomActor`、`BattleActor` 最小主链已跑通并有回归测试 |
| `M2 多核 I/O` | `foundation done` | `AsioIoEngine`、pinned listen、multi-listener ingress、session-core outbound、core diagnostics 已落地 |
| `M3 内存架构` | `not started` | 仍未进入 arena / pool hierarchy / false sharing 专项 |
| `M4 分布式` | `not started` | 仅保留方向性规划，没有 remote actor / cluster router 实现 |
| `M5 数据层 v2` | `not started` | replay/result 事实源已具备，但持久化 schema 和写入链未开始 |
| `M6 battle world` | `foundation done` | ECS world、battle runtime metadata、replay inputs、result summary 已初步 world 化 |
| `M7 运维成熟度` | `bootstrap only` | 已有 `/metrics*`、diagnostics、shadow bridge 扩展观测，但还不是正式控制面 |

当前最重要的边界：

- `M2` 已进入实现期，但尚未完成 `SO_REUSEPORT`、跨核 mailbox、actor 亲核调度
- `M6` 已进入实现期，但尚未进入 authoritative simulation / AOI / deterministic replay
- `M5/M4/M3/M7` 仍以规划为主，不应被文档误判为已落地

---

## 三、M1: Actor 模型核心

### 3.1 为什么需要 Actor 模型

v1.0.0 的业务代码直接操作 `Session` 裸指针和 `std::function` 回调：

```cpp
// v1.0.0 模式：函数回调 + 裸指针
session->set_packet_handler([this](shared_ptr<Session> s, PacketMessage msg) {
    dispatcher_.dispatch(s, msg.message_id, msg.request_id, msg.error_code, move(msg.body));
});
```

**问题**：
- Session 生命周期与业务逻辑强耦合
- 无法对单个玩家做独立的错误隔离
- 难以实现监督树（父 Actor 监控子 Actor 健康）
- 状态机内嵌在回调链中，难以测试和调试
- 无法热更新单个 Actor 的代码

### 3.2 Actor 模型设计

```
┌─────────────────────────────────────────┐
│              Actor System                │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  │
│  │ Player  │  │  Room   │  │ Battle  │  │
│  │ Actor   │  │  Actor  │  │  Actor  │  │
│  └────┬────┘  └────┬────┘  └────┬────┘  │
│       │            │            │        │
│  ┌────▼────────────▼────────────▼────┐   │
│  │         Actor Supervisor           │   │
│  │   (监督树: 重启/升级/日志)          │   │
│  └───────────────────────────────────┘   │
│  ┌───────────────────────────────────┐   │
│  │         Message Mailbox            │   │
│  │   (SPSC 无锁队列, 优先级投递)       │   │
│  └───────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

**核心接口**：

```cpp
// Actor 基类
class Actor {
public:
    virtual void on_start() = 0;
    virtual void on_message(Message& msg) = 0;
    virtual void on_stop() = 0;

    void tell(Message msg);           // 异步发送（fire-and-forget）
    future<Response> ask(Message msg); // 同步请求（request-response）
    void become(State new_state);      // 热切换状态机
    void schedule(duration delay, Message msg); // 延时消息

protected:
    ActorRef self_;                    // 自身引用
    ActorRef parent_;                  // 父 Actor（监督者）
    Mailbox mailbox_;                  // 消息邮箱
};

// Player Actor 示例
class PlayerActor : public Actor {
    StateMachine<LoggedOut, LoggedIn, InRoom, InBattle> state_;
    unique_ptr<Session> session_;

    void on_message(Message& msg) override {
        state_.dispatch(msg, [&](auto& state) {
            state.handle(msg, *this);  // 状态相关的消息处理
        });
    }
};
```

**关键设计决策**：
- 每个 Actor 单线程执行 → 无需锁
- Actor 之间通过消息通信 → 天然解耦
- 监督树保证故障隔离 → 一个 Actor 崩溃不影响其他
- 位置透明 → Actor 可在本地或远程，切换无需改代码
- 状态机内建 → `become()` 热切换行为

### 3.3 迁移路径

| v1.0.0 组件 | v2.0.0 Actor 替换 |
|---|---|
| `Session` + `SessionManager` | `PlayerActor` + `ActorSystem` |
| `RoomManager` | `RoomActor`（一个房间 = 一个 Actor） |
| `BattleManager` | `BattleActor` |
| `LoginService` | `AuthActor` |
| `MatchmakingService` | `MatchmakerActor`（集群单例） |

---

## 四、M2: 多核 I/O 引擎

### 4.1 当前瓶颈

v1.0.0 的 `io_context` 模型：

```cpp
asio::io_context io;                              // 单实例
for (int i = 0; i < n_threads; ++i)
    threads.emplace_back([&] { io.run(); });      // 多线程跑同一个 io
```

**问题**：所有连接共享一个 `io_context`，线程间竞争 epoll/iocp 队列。单个慢连接的回调阻塞会影响其他连接。

### 4.2 多核 I/O 设计

```
┌──────────────────────────────────────────────┐
│              I/O Engine v2.0                  │
│                                               │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐         │
│  │ Core 0  │ │ Core 1  │ │ Core N  │         │
│  │ io_ctx  │ │ io_ctx  │ │ io_ctx  │         │
│  │ ┌─────┐ │ │ ┌─────┐ │ │ ┌─────┐ │         │
│  │ │Conn │ │ │ │Conn │ │ │ │Conn │ │         │
│  │ │  1  │ │ │ │  2  │ │ │ │  N  │ │         │
│  │ └─────┘ │ │ └─────┘ │ │ └─────┘ │         │
│  └─────────┘ └─────────┘ └─────────┘         │
│                                               │
│  ┌──────────────────────────────────────┐     │
│  │     Connection Affinity Router        │     │
│  │  (SO_REUSEPORT + CPU affinity)        │     │
│  └──────────────────────────────────────┘     │
└──────────────────────────────────────────────┘
```

**核心实现**：

```cpp
class IoEngine {
    struct Core {
        asio::io_context io;
        asio::ip::tcp::acceptor acceptor;  // SO_REUSEPORT 共享端口
        vector<ActorRef> local_actors;
    };

    vector<Core> cores_;  // 每 CPU 核心一个

    // 新连接分配到负载最低的核心
    Core& assign_core(tcp::socket&& sock) {
        auto& core = *min_element(cores_.begin(), cores_.end(),
            [](auto& a, auto& b) { return a.local_actors.size() < b.local_actors.size(); });
        return core;
    }
};
```

**关键设计**：
- `SO_REUSEPORT` 内核级连接分发，无需应用层负载均衡
- 每个 `io_context` 绑定一个 CPU 核心（`thread_affinity`）
- Actor 创建时绑定到特定核心，避免跨核迁移
- 跨核通信通过无锁 SPSC 队列

---

## 五、M3: 内存架构重构

### 5.1 分层内存模型

```
┌──────────────────────────────────────────┐
│           Memory Architecture             │
│                                           │
│  ┌─────────────────────────────────────┐  │
│  │         Arena Allocator              │  │
│  │  (预分配大块, 指针碰撞分配, 批量释放)  │  │
│  │  ┌─────────┐ ┌─────────┐ ┌───────┐  │  │
│  │  │ Frame   │ │ Session │ │ Actor │  │  │
│  │  │ Arena   │ │ Arena   │ │ Arena │  │  │
│  │  │ (每帧)  │ │ (每连接)│ │(每对象)│  │  │
│  │  └─────────┘ └─────────┘ └───────┘  │  │
│  └─────────────────────────────────────┘  │
│                                           │
│  ┌─────────────────────────────────────┐  │
│  │         Object Pool Hierarchy        │  │
│  │  Message Pool → Packet Pool → Buffer │  │
│  └─────────────────────────────────────┘  │
└──────────────────────────────────────────┘
```

**Frame Arena**：每帧重置，零碎片。所有帧内临时分配走此 Arena。
**Session Arena**：连接存活期间有效，释放时整块回收。
**Actor Arena**：Actor 生命周期内有效。

```cpp
// Frame Arena: 每 tick 重置
class FrameArena {
    char* base_;       // 预分配 64MB
    char* current_;    // 碰撞指针
    char* end_;

    void* alloc(size_t n) {
        if (current_ + n > end_) return fallback_alloc(n);
        auto* p = current_;
        current_ += n;
        return p;
    }

    void reset() { current_ = base_; }  // O(1) 重置
};
```

### 5.2 缓存行对齐

```cpp
// 热路径数据结构：避免 false sharing
struct alignas(64) SessionHotData {
    atomic<uint64_t> packets_sent{0};
    atomic<uint64_t> packets_recv{0};
    atomic<size_t> write_queue_bytes{0};
    // ... 长度恰好 64 字节
};

struct alignas(64) SessionColdData {
    string user_id;
    string display_name;
    chrono::time_point login_time;
    // ... 不常访问的字段
};
```

---

## 六、M4: 分布式原语

### 6.1 集群拓扑

```
┌─────────────────────────────────────────────────┐
│                 Service Mesh                      │
│                                                   │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐     │
│  │ Gateway  │   │ Gateway  │   │ Gateway  │     │
│  │ Instance │   │ Instance │   │ Instance │     │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘     │
│       │               │               │           │
│  ┌────▼───────────────▼───────────────▼─────┐     │
│  │           Message Bus                     │     │
│  │   (发布/订阅, 分区, 持久化, 至少一次投递)   │     │
│  └────┬───────────────┬───────────────┬─────┘     │
│       │               │               │           │
│  ┌────▼─────┐   ┌─────▼────┐   ┌─────▼────┐     │
│  │  Login   │   │   Room   │   │  Battle  │     │
│  │  Service │   │  Service │   │  Service │     │
│  │ (N 实例) │   │ (M 分片) │   │ (K 实例) │     │
│  └──────────┘   └──────────┘   └──────────┘     │
│                                                   │
│  ┌──────────────────────────────────────────┐     │
│  │         Coordination Service              │     │
│  │  (服务发现 + 领导者选举 + 一致性哈希)      │     │
│  └──────────────────────────────────────────┘     │
└─────────────────────────────────────────────────┘
```

### 6.2 一致性哈希分片

```cpp
// Room 分片: room_id → 虚拟节点 → 物理节点
class ConsistentHashRing {
    map<uint32_t, ServiceInstance> ring_;  // 150 个虚拟节点

    ServiceInstance lookup(const string& key) {
        auto hash = murmur3_32(key);
        auto it = ring_.lower_bound(hash);
        return it != ring_.end() ? it->second : ring_.begin()->second;
    }
};

// 使用示例:
// 创建房间时，根据 room_id 哈希到特定 Room Service 实例
// 加入房间时，同样哈希到同一实例
// 保证同一房间的所有操作在同一进程内完成
```

### 6.3 领导者选举 (Bully/Raft)

```cpp
// 用于 Matchmaking 等单例服务
class LeaderElection {
    // Raft 简化实现:
    // - 心跳保活
    // - 任期号递增
    // - 只有 Leader 执行匹配逻辑
    // - Follower 热备，Leader 宕机后立即接管

    enum State { Follower, Candidate, Leader };
    atomic<State> state_{Follower};
};
```

---

## 七、M5: 数据层 v2

### 7.1 分层存储架构

```
┌─────────────────────────────────────────────┐
│              Data Layer v2                    │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │           Cache Layer                     │  │
│  │  Local LRU (1M entries, ~100ns)          │  │
│  │          ↓ miss                           │  │
│  │  Distributed Cache (Redis-compatible)     │  │
│  │          ↓ miss                           │  │
│  │  Database (SQLite/PostgreSQL)             │  │
│  └─────────────────────────────────────────┘  │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │         Write Path                       │  │
│  │  Write → WAL Buffer → Async Flush        │  │
│  │       → Snapshot (every N writes)        │  │
│  └─────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### 7.2 Actor 状态快照

```cpp
class Snapshotable : public Actor {
    // 定期快照: 序列化当前状态到存储
    virtual void take_snapshot() = 0;

    // 从快照恢复: 重启后恢复状态
    virtual void restore_from_snapshot(const Snapshot& snap) = 0;

    // 事件溯源: 重放事件日志恢复状态
    virtual void replay_events(const vector<Event>& events) = 0;
};

// Player Actor 快照示例
class PlayerActor : public Snapshotable {
    PlayerState state_;
    vector<Event> event_log_;

    void take_snapshot() override {
        Snapshot snap = snapshotter_.capture(state_);
        store_.save_snapshot(user_id_, snap);
        event_log_.clear();  // 快照后的旧事件可以 GC
    }
};
```

---

## 八、M6: AOI 空间管理系统

### 8.1 适用场景

大世界/开放世界游戏需要空间索引来管理实体可见性。当场景中有 10,000+ 实体时，全量广播不可行。

### 8.2 十字链表 + 网格混合

```
┌──────────────────────────────────────────────┐
│            AOI System (十字链表)               │
│                                               │
│  ┌───┬───┬───┬───┬───┐                       │
│  │   │   │   │   │   │  每个格子维护:          │
│  ├───┼───┼───┼───┼───┤  - 实体链表 (X 轴)     │
│  │   │   │ P │   │   │  - 实体链表 (Y 轴)     │
│  ├───┼───┼───┼───┼───┤                       │
│  │   │ Q │   │   │   │  实体移动: O(1) 链表操作 │
│  ├───┼───┼───┼───┼───┤  范围查询: 遍历视野内格子 │
│  │   │   │   │   │   │                       │
│  └───┴───┴───┴───┴───┘                       │
│                                               │
│  玩家 P 视野: 周围 3×3 格子                     │
│  实体 Q 进入 P 视野 → 触发 EnterView 事件        │
└──────────────────────────────────────────────┘
```

```cpp
class AoiSystem {
    Grid grid_;                         // 网格划分
    unordered_map<EntityId, AoiNode> entities_;

    // 实体移动 → 检查是否需要跨格 → 触发进入/离开视野事件
    void on_entity_move(EntityId eid, Vec3 new_pos) {
        auto& node = entities_[eid];
        auto old_cell = grid_.cell_of(node.position);
        auto new_cell = grid_.cell_of(new_pos);

        if (old_cell != new_cell) {
            grid_.move(node, old_cell, new_cell);   // O(1) 十字链表操作
        }

        node.position = new_pos;

        // 通知视野内实体
        for (auto other : grid_.entities_in_view(new_cell, view_radius_)) {
            send_event(other, EnterView{eid});
        }
    }
};
```

---

## 九、M7: 运维成熟度

### 9.1 Kubernetes Operator

```yaml
# Gateway 自动伸缩配置
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
spec:
  scaleTargetRef:
    kind: GameServer
    name: gateway
  metrics:
    - type: Pods
      pods:
        metric:
          name: gateway_active_sessions
        target:
          type: AverageValue
          averageValue: 5000   # 每个 Pod 承载 5000 连接
```

**GameServer CRD** (自定义资源):

```cpp
// Kubernetes Operator 管理游戏服务器生命周期
class GameServerOperator {
    // 监听 etcd 变更
    // 协调期望状态 vs 实际状态
    // 处理: 滚动更新, 金丝雀发布, 自动扩缩容
    // 游戏专用: 排干连接 → 迁移房间 → 更新镜像 → 恢复房间
};
```

### 9.2 灰度发布与功能开关

```cpp
// 功能开关: 按百分比逐步放量
class FeatureFlags {
    // user_id → 百分比桶 → 是否启用
    bool is_enabled(const string& flag, const string& user_id) {
        auto bucket = hash(user_id) % 100;
        return bucket < rollout_percentage_[flag];
    }

    // 示例: 新战斗协议只对 5% 用户启用
    // feature_flags.is_enabled("battle_v2_protocol", user_id)
};
```

### 9.3 OpenTelemetry 分布式追踪

```cpp
// 跨服务追踪: Gateway → Login → Room → Battle
class Tracer {
    Span start_span(const string& operation) {
        auto trace_id = trace_context_.trace_id;
        auto span_id = next_span_id();
        auto parent_span_id = trace_context_.current_span;

        // 导出到 Jaeger/Zipkin
        // 火焰图可看到完整调用链: Gateway(2ms) → Room(5ms) → Battle(15ms)
        return Span{trace_id, span_id, parent_span_id, operation};
    }
};
```

---

## 十、v2.0.0 实施路线

### 阶段划分

```
V2.0.0 总工期预估: 按建议顺序执行

┌──────────────────────────────────────────────────────────┐
│ 阶段 A: 内核重构 (M1 + M2 + M3)                           │
│   Actor 模型 → 多核 I/O → 内存架构                         │
│   影响: 所有模块                                          │
│   工期: 最大, 是整个 v2.0.0 的基础                        │
├──────────────────────────────────────────────────────────┤
│ 阶段 B: 分布式能力 (M4)                                    │
│   一致性哈希 → 领导者选举 → 服务发现                        │
│   依赖: M1 (Actor 位置透明)                               │
│   工期: 中                                                │
├──────────────────────────────────────────────────────────┤
│ 阶段 C: 数据层升级 (M5)                                    │
│   缓存层 → Write-Behind → Snapshot/Restore                │
│   依赖: M1 (Actor 状态快照)                               │
│   工期: 中                                                │
├──────────────────────────────────────────────────────────┤
│ 阶段 D: 游戏特性深化 (M6)                                   │
│   AOI 空间管理                                            │
│   适用场景: MMO/大世界/BR                                 │
│   工期: 小 (框架级, 业务层由具体游戏实现)                   │
├──────────────────────────────────────────────────────────┤
│ 阶段 E: 运维成熟度 (M7)                                    │
│   K8s Operator → OpenTelemetry → 灰度发布                  │
│   依赖: M4 (分布式基础)                                    │
│   工期: 中                                                │
└──────────────────────────────────────────────────────────┘
```

### 每个阶段的里程碑

| 阶段 | 主要交付 | 测试标准 |
|---|---|---|
| A | ActorSystem, IoEngine, FrameArena | 单进程 Actor 吞吐 > 100K msg/s |
| B | ConsistentHashRing, LeaderElection, ServiceDiscovery | 3 节点集群故障转移 < 5s |
| C | LruCache, WriteBehindStore, Snapshotable | 10K 次写入/s, 重启恢复 < 1s |
| D | AoiSystem, SpatialGrid | 10K 实体 AOI 查询 < 1ms |
| E | GameServerOperator, Tracer, FeatureFlags | 金丝雀发布零停机 |

### 技术选型参考

| 组件 | 候选方案 | 建议 |
|---|---|---|
| 消息总线 | Kafka / NATS / 自研 | 内网部署使用自研（基于现有多线程 I/O） |
| 分布式缓存 | Redis / Dragonfly | Redis 生态成熟 |
| 追踪 | Jaeger / Zipkin / OpenTelemetry | OpenTelemetry 标准 |
| 编排 | Kubernetes / HashiCorp Nomad | K8s 生态最大 |
| 服务网格 | Istio / Linkerd / 自研 | 自研（游戏协议特殊） |

---

## 十一、兼容性策略

v2.0.0 不会破坏 v1.0.0 的 API 兼容性：

1. **协议格式兼容**：v2.0.0 的服务端可接受 v1.0.0 客户端连接
2. **渐进式迁移**：Actor 模型通过 `ActorAdapter` 包装现有 `Session` 实现平滑过渡
3. **配置向后兼容**：v1.0.0 的 `gateway.json` 可直接在 v2.0.0 使用
4. **构建系统保持不变**：CMake + FetchContent 继续使用

---

## 十二、v2.0.0 目标总结

```
                    v1.0.0                    v2.0.0
并发模型:   单 io_context + strand     多核 io_context 池 + CPU 亲和
业务抽象:   Session + 函数回调         Actor 模型 + 状态机 + 监督树
内存管理:   全局 Pool                  三层 Arena (Frame/Session/Actor)
数据层:     IPlayerStore 接口          LruCache + WriteBehind + Snapshot
集群:       InternalBus (实验性)       一致性哈希 + 领导者选举 + 服务发现
游戏特性:   房间/战斗/匹配基本闭环      AOI 空间管理 + 状态同步
可观测性:   Prometheus + 审计日志      OpenTelemetry 分布式追踪 + 火焰图
部署:       Docker + Compose           Kubernetes Operator + 灰度发布

测试:       54 个 (单进程)              100+ 个 (含集群集成测试)
吞吐:       ~10K msg/s (单核)           ~100K msg/s (8 核)
延迟:       p99 < 5ms (单机)            p99 < 2ms (同核心 Actor)
```
