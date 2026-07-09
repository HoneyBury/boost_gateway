# Boost Gateway Architecture Overview

**Version**: 3.5.0

## High-Level Architecture

```
+-----------------------------------------------------------------------+
|                         Clients (SDK)                                 |
|  C++ SDK  |  Python (ctypes)  |  C# (P/Invoke)  |  gRPC (experimental)|
+-----------------------------+------------------------------------------+
                              | TCP (length-prefixed protocol)
                              v
+-----------------------------------------------------------------------+
|                      Gateway (v2_gateway_demo)                        |
|  +------------+  +------------+  +------------+  +------------------+  |
|  | Session    |  | Gateway    |  | Runtime    |  | HTTP Management |  |
|  | Manager    |  | Actor      |  | (actor)    |  | Port (health/   |  |
|  | (RCU)      |  |            |  |            |  |  metrics/audit) |  |
|  +------------+  +------------+  +------------+  +------------------+  |
|  +------------------------------------------------------------------+  |
|  |               GatewayServiceBridge                                |  |
|  |  Route requests | Circuit breaker | ClusterRouter | Shard        |  |
|  +------------------------------------------------------------------+  |
+-----------------------------------------------------------------------+
                              |
                +-------------+------------+------------+-----------+
                v             v            v            v           v
          +----------+ +--------+ +----------+ +-----------+ +----------+
          |  Login   | |  Room  | |  Battle  | |Matchmaking| |Leaderbrd |
          |  Backend | | Backend| |  Backend | | Backend   | | Backend  |
          +----------+ +--------+ +----------+ +-----------+ +----------+
                                                       |              |
                                                 +-----+-----+  +----+----+
                                                 | Raft      |  | Raft    |
                                                 | Consensus |  |Consensus|
                                                 +-----------+  +---------+
```

## Core Components

### Gateway Layer
- **SessionManager**: Lock-free RCU-based session tracking with backpressure (max_pending_per_session=1024)
- **GatewayActor**: Actor-model request handler, message dispatch, push delivery
- **Runtime**: Battle lifecycle management, match->battle auto-flow, session orchestration
- **GatewayServiceBridge**: Backend routing with circuit breaker, cluster discovery, shard-aware routing

### Backend Services
Each backend runs as an independent process with its own port:
- **Login** (:9101): Authentication, token management, rate limiting
- **Room** (:9102): Room CRUD, player join/leave, ready states, TTL cleanup
- **Battle** (:9103): Real-time game loop, ECS, anti-cheat, replay recording
- **Matchmaking** (:9104): MMR-based matching, Raft consensus for fault tolerance
- **Leaderboard** (:9105): Score submission/query, Raft consensus for consistency

### Realtime Instance Runtime (v3.5.0+)
The Realtime Instance Framework provides a generic tick-based game loop runtime that decouples business logic from lifecycle management.

- **InstanceRuntime** (`v2::realtime::InstanceRuntime`): Manages instance lifecycle (creating/waiting/running/finishing/finished/closed), tick scheduling (`tick_instance()`/`tick_all()`), input queue with per-player ordering, snapshot push, resume support (`get_resume_snapshot()`), and backpressure
- **InstancePlugin SPI** (`v2::realtime::InstancePlugin`): Pure virtual interface with 8 methods — lifecycle hooks (`on_instance_created`, `on_player_join`, `on_player_leave`), input processing (`on_input`), hot-path tick (`on_tick`, noexcept), and snapshot/settlement (`build_snapshot`, `build_settlement`, `build_resume_snapshot`, all noexcept)
- **Error isolation**: Framework wraps all plugin calls in try-catch; noexcept methods include defensive try-catch as deep protection; plugin exceptions never crash the runtime
- **Plugin registry** (`InstancePluginFactory`): Map of `instance_type` → factory function, enabling per-type plugin instantiation

**Concrete plugins:**
- **TankBattlePlugin** (`v2::battle::TankBattlePlugin`, `src/v2/battle/`): Full InstancePlugin implementation using ECS SimpleWorld, supporting move/attack/shoot/finish actions. Used as the framework-integrated reference implementation and SPI compliance test vehicle.
- **EchoPlugin** (`echo_plugin::EchoPlugin`, `examples/realtime_echo_plugin/`): Minimal echo plugin demonstrating the SPI with input→response round-trip.
- **TankPlugin** (`tank::TankPlugin`, `demo/games/tank_battle/`): Demo-specific plugin adapting the standalone TankWorld simulation to the InstancePlugin SPI.

### Actor System
- ActorSystem manages actor lifecycle and message dispatching
- Actors: GatewayActor, RoomBackend, BattleBackend, etc.
- Messages typed via `MessageKind` enum with `target_service` routing

### ECS (Entity Component System)
- World/SimpleWorld architecture with typed component access and `for_each<T>()` iteration
- ParallelSystemExecutor with topological sort (Kahn algorithm) for concurrent system execution via `std::async`; SequentialSystemExecutor as default fallback

**Registered systems in `create_battle_world()`** (7 systems):
- `BattleClockSystem` — per-tick frame counter and trigger tracking
- `BattleInputSystem` — parses pending input strings into move/attack intents
- `MovementSystem` — speed-limited movement with anti-cheat teleport detection
- `CombatSystem` — attack cooldown, damage bounds, attacks-per-frame limit
- `AoiSystem` — ECS-integrated Area of Interest via SpatialGrid
- `BattleLifecycleSystem` — auto lifecycle state machine (kCreated→kRunning→kFinished) with idle timeout (300 frames) and all-offline timeout (60 frames)
- `BattleReplaySystem` — per-frame state snapshot capture for deterministic replay

**Additional system used by TankBattlePlugin:**
- `ProjectileSystem` — travel-time projectiles with interpolation, single-target damage, AoE radius, and Damage-over-Time (DoT) ticks

**ECS Component types** (defined in `runtime_components.h`):
- `BattleClockComponent`, `BattleParticipantComponent`, `BattleMetadataComponent`, `BattleReplayLogComponent`
- `PositionComponent`, `HealthComponent`, `AttackStateComponent`, `AttackCooldownComponent`
- `ProjectileComponent`, `DamageOverlayComponent`

### Performance Features
- RCU (Read-Copy-Update) for lock-free broadcast in AOI and session management
- PerfCounters with TSC-based timing and TLS storage
- Cache-line aligned arena allocator
- Parallel system execution with dependency graph
- Per-session flow control and backpressure

### Persistence Layer
- **BattleArchiveSink** abstract interface: save/load replay, result, snapshot; high-level `persist()` method.
- **JsonFileBattleDataStore**: File-backed JSON implementation storing in `replays/`, `results/`, `snapshots/` subdirectories.
- **CachedBattleDataStore**: Decorator wrapping a delegate with separate LRU caches (1000 entries each for replay/result/snapshot) + `WriteBehindDataStore` for asynchronous flush.
- **WriteBehindDataStore**: Background worker thread processes queued commands; failed saves increment `failed_count_` and continue (no retry).
- **LruCache<K,V>**: Generic thread-safe LRU cache with `put()` returning evicted entries, `for_each()` read-only iteration, `drain()` bulk retrieval.
- **StorageEngineSQLite**: Optional SQLite engine with connection pool (min=2/max=8, WAL mode) behind `#ifdef HAS_SQLITE`.
- **PlayerData LRU cache**: 1024 entries for player profile caching.

### SDK
- C API (src/sdk/src/c_api.cpp) as the stable ABI boundary
- Python bindings via ctypes
- C# bindings via P/Invoke
- Async API available via callback registration

### Consistency & HA
- **Raft consensus** for matchmaking and leaderboard state (3-node configs in config/environments/ha/)
- **Circuit breaker** pattern in gateway-to-backend routing
- **ClusterRouter** for dynamic service discovery and health checks (5s interval)
- **ServiceRegistrar** for automatic backend registration

## Data Flow: Request Routing

```
Client -> Gateway Session -> GatewayActor -> GatewayServiceBridge
    -> resolve_backend() [cluster router -> consistent hash -> static fallback]
    -> ensure_connection() [connection pool with round-robin]
    -> send_request() [with retry on failure]
    -> circuit breaker tracking
    -> response -> Client
```

## Data Flow: Matchmaking -> Battle

```
Client A -> MatchJoin
Client B -> MatchJoin
    -> Matchmaker (MMR-based pairing)
    -> MatchFoundCallback
    -> Runtime::on_match_found()
    -> create_room_with_players()
    -> send_push(kMatchFoundPush) to both clients
    -> wait for ready (or timeout)
    -> start_battle()
    -> redirect clients to Battle backend
```

## Deployment Models

### Single Process (Development)
```
v2_gateway_demo --io-cores 4 --port 9201
```
All backends are in-process actors. No Raft.

### Multi-Process (Production)
```
v2_gateway_demo --port 9201    (gateway)
v2_login_backend --port 9101
v2_room_backend --port 9102
v2_battle_backend --port 9103
v2_matchmaking_backend --port 9104 [--raft]
v2_leaderboard_backend --port 9105 [--raft]
```
Each process independent. Raft for stateful services.

### Kubernetes
```yaml
# Helm values/source-of-truth: env/k8s/helm/boost-gateway/
# Operator scaffold: operator/boostgateway-operator/
# CRD: operator/boostgateway-operator/config/crd/bases/gateway.boost.io_boostgatewayclusters.yaml
```

## Testing Strategy
| Level | Location | Scope |
|-------|----------|-------|
| Unit | tests/v2/unit/ | Individual components with mocks |
| Integration | tests/v2/integration/ | Multi-component E2E |
| Multi-process | tests/v2/integration/ (multi_process) | Real OS process orchestration |
| Chaos | tests/chaos/ | Network partition, crash, latency injection |
| Fuzz | tests/fuzz/ | Protocol codec fuzzing (libFuzzer) |
| Security | tests/security/ | Protocol-level attack simulation |
| Performance | scripts/collect_release_baseline.py | Throughput/latency regression detection |

## Key Protocols
- **Wire format**: `[length:4][version:1][message_id:2][request_id:4][sequence:4][error_code:4][flags:1][body:N]`
- **kFixedMetadataSize**: 16 bytes
- **Transport**: TCP (default), gRPC (experimental, behind BOOST_BUILD_GRPC flag)

## Related Documents
- [Current State](current-state.md)
- [Docs Index](README.md)
- [Performance Baseline](performance-baseline.md)
- [Release Governance](release-governance.md)
- [Production Deployment Runbook](production-deployment-runbook.md)
- [TLS / mTLS Runbook](tls-mtls-runbook.md)
