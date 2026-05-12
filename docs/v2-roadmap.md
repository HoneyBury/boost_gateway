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
| `M2 多核 I/O` | `done` | accept policy、SPSC mailbox、session counting、multi-listener ingress、core diagnostics、SO_REUSEPORT（MultiIoAcceptor 每核独立 bind）、actor 核心亲和（ActorRef::core_id + 跨核 SPSC 路由 + drain_mailbox_and_dispatch）全部落地 |
| `M3 内存架构` | `done` | BumpArena（指针碰撞 + O(1) reset + 64MB 默认）、ObjectPool\<T, BlockSize\>（header-only template + intrusive free list + placement new + std::mutex）、CacheLine（kCacheLineSize=64 + CacheLinePad + HotCold\<Hot,Cold\> 避免 false sharing）、24 个单元测试 |
| `M4 分布式` | `S0-S4 done` | S0-S4 服务拆分全部完成：边界冻结、gateway-only ingress、login/room/battle 独立 backend、ServiceRegistry TTL/心跳/摘除、BackendMetrics 路由计数器、diagnostics_json 管理口、22 个集成测试 |
| `M5 数据层 v2` | `done` | LruCache（thread-safe, O(1)）、WriteBehindDataStore（异步写队列+后台worker）、Snapshotable mixin（Actor::take_snapshot/restore_from_snapshot）、BattleActor 快照/恢复、CachedBattleDataStore（LRU读缓存+WriteBehind写）、31 个单元测试 + 6 个集成测试 |
| `M6 battle world` | `done` | 7-system ECS pipeline（Clock→Input→Movement→Combat→AOI→Lifecycle→Replay）、authoritative simulation（MovementSystem/CombatSystem）、deterministic replay、AOI 空间管理（SpatialGrid + AoiSystem + AoiViewComponent）、14+12 个单元测试已通过 |
| `M7 运维成熟度` | `done` | DiagnosticsManager（统一 BackendMetrics+ServiceRegistry+io_core+shadow bridge 快照 + JSON 序列化）、HealthCheck（真实健康检查 pass/fail/warn + RFC 格式 JSON + 替换假 /health）、FeatureFlags（hash(user_id)%100 百分比灰度 + shared_mutex 线程安全 + header-only）、TraceContext+Span（轻量自研追踪 + trace_id 贯穿 gateway→login→room→battle）、27 个单元测试 |

当前最重要的边界：

- `M2` 已完成，accept policy + SPSC mailbox + session counting + multi-listener ingress + SO_REUSEPORT（MultiIoAcceptor）+ actor 核心亲和（cross-core SPSC routing + drain_mailbox_and_dispatch）全部落地
- `M6` 已完成，7-system ECS pipeline + authoritative simulation（MovementSystem/CombatSystem）+ deterministic replay + AOI 空间管理（SpatialGrid + AoiSystem + AoiViewComponent）全部落地
- `M4` S0-S4 服务拆分全部完成（`BackendEnvelope`、`ServiceManifest`、`ServiceErrorCode`、GatewayServiceBridge 三 slot 路由、级联 forward/push_to_sessions、`ServiceRegistry` TTL/心跳、`BackendMetrics` 计数器、22 个集成测试）
- `M5` 已完成，LruCache + WriteBehindDataStore + Snapshotable mixin + CachedBattleDataStore 全部落地，31 个单元测试 + 6 个集成测试通过
- `M3` 已完成，BumpArena（指针碰撞 + O(1) reset）+ ObjectPool\<T, BlockSize\>（header-only template + intrusive free list + placement new + std::mutex）+ CacheLine（kCacheLineSize + CacheLinePad + HotCold\<Hot,Cold\>）全部落地，24 个单元测试通过
- `M7` 已完成，DiagnosticsManager（统一快照+JSON序列化）+ HealthCheck（真实健康检查 pass/fail/warn + RFC 格式 JSON）+ FeatureFlags（hash(user_id)%100 百分比灰度）+ TraceContext+Span（轻量自研追踪）全部落地，27 个单元测试通过

### v2.0.1 生产加固 (已完成 2026-05-12)

- `H1` 配置热加载 — `v2::ConfigWatcher` (自有 io_context + 线程, 轮询 last_write_time), 5 个单元测试
- `H2` 断路器 — `v2::service::CircuitBreaker` (CLOSED→OPEN→HALF_OPEN 三态, 可配置阈值), 8 个单元测试
- `H3` 优雅降级 — 按后端错误码 (`kLoginBackendUnavailable`/`kRoomBackendUnavailable`/`kBattleBackendUnavailable`), `BackendMetrics::record_degraded`
- `H4` 背压保护 — `SessionOptions::backpressure_high_watermark/low_watermark` 可配置, `backpressure_activate_count_` 暴露
- `H5` 连接限制 — `DemoServerOptions::max_connections`, `total_session_count()`, 超限返回 `kRateLimited`
- `H6` 内存保护 — `ObjectPool::max_blocks`, `exhausted_count_` 计数器, `BumpArena::exhausted_count_`

### v2.0.2 性能基线 (已完成 2026-05-12)

- `B1` 测量基础设施 — `LatencyHistogram` (指数分桶 14 桶), `ThroughputTracker` (滑动窗口 5s/10 子桶), `v2_gateway_pressure` benchmark harness
- `B2` 吞吐量基线 — `DiagnosticsSnapshot::messages_per_second` 字段, `BackendMetrics::latency_sample_count`
- `B3` 延迟基线 — `GatewayServiceBridge::route()` 记录 backend 往返延迟到 `BackendMetrics::record_latency()`
- `B4-B6` 文档产出 — `docs/performance-baseline.md` (测量方法 + SLO/SLI 定义 + 容量规划公式 + 基准命令), 356 单元测试

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

**核心接口（设计目标，实际实现见 `include/v2/actor/actor.h`）**：

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

**核心实现（概念设计，实际接口见 `include/v2/io/io_engine.h`）**：

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

### 5.1 已实现组件

| 组件 | 文件 | 说明 |
|---|---|---|
| `BumpArena` | `include/v2/memory/arena.h` | 指针碰撞分配器，预分配大块内存，O(1) reset，对齐到 `max_align_t`，OOM 返回 nullptr |
| `ObjectPool<T, BlockSize>` | `include/v2/memory/object_pool.h` | header-only template，intrusive free list 通过 `reinterpret_cast`，placement new 构造，`std::mutex` 线程安全，默认 BlockSize=256 |
| `CacheLinePad` / `HotCold<Hot,Cold>` | `include/v2/memory/cache_line.h` | `kCacheLineSize=64`，`CacheLinePad` 强制下一字段进入新缓存行，`HotCold<Hot,Cold>` 将热/冷字段分离到不同缓存行避免 false sharing |

### 5.2 分层内存模型 (设计目标)

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

> `M4` 当前除了“一致性哈希 / 领导者选举 / 服务发现”这些抽象目标，还需要一条可执行的“服务拆分”落地路线。该专项规划见 [v2-service-split-plan.md](./v2-service-split-plan.md)。

### 6.0 当前实现边界（`2026-05-11`）

当前仓库已经完成 S0 边界冻结：

- `ServiceId`、`BackendEnvelope`（request/response/push/error + correlation_id + timeout + error_code）
- `ServiceManifest`（gateway/login/room/battle 四服务职责声明 + ownership）
- `ServiceErrorCode`（backend 错误码 + client mapping）
- 24 个边界测试

此外有前置抽象占位：

- `ServiceRouter / InternalBus / ServiceRegistry / BackendRouter`

但这**不等于**已经完成：

- gateway-only ingress（S1）
- backend 独立 envelope 落地到真实多进程链路
- 多进程请求/回包/超时闭环
- 服务发现与健康摘除（S4）

因此，`M4` 在当前阶段应拆成两部分理解：

1. 服务拆分最小闭环（S0-S4 done：边界冻结 → gateway-only ingress → login/room/battle 独立 backend → ServiceRegistry TTL/心跳/摘除）
2. 分布式能力深化（cluster router / remote actor transport / 一致性哈希 仍为远期目标）

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

### 6.2 一致性哈希分片（远期设计目标，当前未实现）

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

### 6.3 领导者选举（远期设计目标，当前未实现）

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

### 7.1 已实现组件

| 组件 | 文件 | 说明 |
|---|---|---|
| `LruCache<K,V>` | `include/v2/data/lru_cache.h` | 线程安全泛型 LRU 缓存，`list`+`unordered_map` O(1) 操作，`shared_mutex` 读写锁 |
| `WriteBehindDataStore` | `include/v2/data/write_behind_store.h` | `BattleArchiveSink` 装饰器，异步写队列 + 后台 worker 线程，析构自动 flush |
| `Snapshotable` | `include/v2/actor/actor.h` | Actor mixin，纯虚 `take_snapshot()`/`restore_from_snapshot()`，Actor 提供默认空实现 |
| `BattleActor` 快照 | `src/v2/battle/battle_actor.cpp` | 通过 `battle_world_snapshot_to_json` / `battle_world_restore_from_json` 实现快照/恢复 |
| `CachedBattleDataStore` | `include/v2/data/cached_data_store.h` | 组合 LruCache（读缓存）+ WriteBehind（异步写），读命中缓存、miss 回退 delegate，写直通缓存+异步落盘 |

### 7.2 分层存储架构

```
┌─────────────────────────────────────────────┐
│              Data Layer v2                    │
│                                               │
│  ┌─────────────────────────────────────────┐  │
│  │  CachedBattleDataStore                   │  │
│  │  ┌──────────┐    ┌───────────────────┐  │  │
│  │  │ LruCache │    │ WriteBehindDataStore│  │  │
│  │  │ (read)   │    │ (async write)      │  │  │
│  │  └──────────┘    └────────┬──────────┘  │  │
│  │         ↓ miss            ↓              │  │
│  │  ┌──────────────────────────────────┐    │  │
│  │  │  JsonFileBattleDataStore (disk)  │    │  │
│  │  └──────────────────────────────────┘    │  │
│  └─────────────────────────────────────────┘  │
└─────────────────────────────────────────────┘
```

### 7.3 Actor 状态快照

```cpp
// Snapshotable mixin — all actors can snapshot
class Snapshotable {
public:
    virtual ~Snapshotable() = default;
    [[nodiscard]] virtual std::string take_snapshot() const = 0;
    virtual bool restore_from_snapshot(const std::string& snapshot_data) = 0;
};

// Actor inherits from Snapshotable with default no-op implementations
class Actor : public Snapshotable {
    [[nodiscard]] std::string take_snapshot() const override { return {}; }
    bool restore_from_snapshot(const std::string&) override { return false; }
};

// BattleActor overrides for real snapshot support
class BattleActor final : public Actor {
    std::string take_snapshot() const override;       // → battle_world_snapshot_to_json
    bool restore_from_snapshot(const std::string&) override;  // → battle_world_restore_from_json
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

### 9.1 Kubernetes Operator（远期设计目标，当前未实现，需真实 K8s 集群）

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

### 9.3 OpenTelemetry 分布式追踪（远期设计目标，当前为自研 TraceContext）

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
| A | ActorSystem, IoEngine, FrameArena | ✅ 已完成 |
| B | ConsistentHashRing, LeaderElection, ServiceDiscovery | 远期目标 — 未实现 |
| C | LruCache, WriteBehindDataStore, Snapshotable, CachedBattleDataStore | ✅ 已完成 |
| D | AoiSystem, SpatialGrid | ✅ 已完成 |
| E | GameServerOperator, Tracer, FeatureFlags | FeatureFlags ✅ 已完成；K8s Operator/OpenTelemetry 远期目标 |

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

1. **协议格式桥接**：v2 通过 `SessionAdapter` → `GatewayActor` → `Runtime` 桥接链兼容 v1 字符串协议，外部客户端无需感知内部 Actor 模型
2. **渐进式迁移**：v1 `GatewayServer` 与 v2 `DemoServer` 并存，v1 主链未替换
3. **配置向后兼容**：v1 的 `gateway.json` 继续用于 v1 入口；v2 新增独立 backend 配置
4. **构建系统保持不变**：CMake + FetchContent 继续使用

---

## 十二、v2.0.0 目标总结

```
                    v1.0.0                    v2.0.0
并发模型:   单 io_context + strand     多核 io_context 池 + CPU 亲和
业务抽象:   Session + 函数回调         Actor 模型 + 状态机 + 监督树
内存管理:   全局 Pool                  三层 Arena (Frame/Session/Actor) + ObjectPool + HotCold false-sharing 防护
数据层:     IPlayerStore 接口          LruCache + WriteBehind + Snapshotable + CachedBattleDataStore
集群:       InternalBus (实验性)       一致性哈希 + 领导者选举 + 服务发现
游戏特性:   房间/战斗/匹配基本闭环      AOI 空间管理 + 状态同步
可观测性:   Prometheus + 审计日志      OpenTelemetry 分布式追踪 + 火焰图
部署:       Docker + Compose           Kubernetes Operator + 灰度发布

测试:       54 个 (单进程)              473 个 (含 v2 单元+集成测试)
吞吐:       ~10K msg/s (单核)           目标 ~100K msg/s (8 核) — 待基准测定
延迟:       p99 < 5ms (单机)            目标 p99 < 2ms (同核心 Actor) — 待基准测定
```
