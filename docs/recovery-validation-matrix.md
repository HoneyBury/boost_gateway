# Recovery Validation Matrix

Date: 2026-05-23

This document enumerates recovery scenarios for the Boost Gateway game server framework, their expected behavior, and current test coverage status.

---

## Matrix

| # | Scenario | Fault | Recovery | Expected | Test Coverage | Coverage Detail |
|---|----------|-------|----------|----------|---------------|-----------------|
| 1 | Redis degradation | Redis server goes down | Automatic fallback to in-memory storage | Service continues without interruption; leaderboard reads fall back to local memory; event store writes return false but caller handles gracefully | **YES** | `RedisClientTest.ConnectFailsGracefully`, `RedisClientTest.DisconnectedOperationsReturnEmpty`, `RedisEventStoreTest.NoRedisAppendReturnsFalse`, `RedisEventStoreTest.NoRedisReadReturnsEmpty`, `RedisConnectionPoolTest.AcquireWhenRedisDownReturnsEmpty`, `RedisLeaderboardDegradedTest.*` |
| 2 | Redis recovery | Redis server comes back online | Reconnection via connection pool | Connection pool revives dead connections; subsequent operations succeed | **YES** | `RedisConnectionPoolTest.DeadConnectionRevivedOnAcquire`, redis degraded + live gate pairing in `verify_specialized_e2e.py` |
| 3 | Redis data consistency after failover | Redis restarts with data loss | Application reads from Redis post-recovery | Memory-fallback data is NOT automatically written back to Redis; manual or app-level re-sync required | **NO** | No test validates data consistency after Redis restart + memory-fallback round trip |
| 4 | Redis automatic reconnection | Redis temporarily unavailable, then available | Pool automatically reuses revived connections | Connections transition from dead to alive transparently | **YES** | `RedisConnectionPoolTest.DeadConnectionRevivedOnAcquire` |
| 5 | Raft leader election | Kill current Raft leader | New leader elected from followers | All committed log entries preserved; new leader serves reads/writes | **YES (unit)** | `RaftTest.PersistentLogAndCommitStateRestoreAfterRestart`, `RaftTest.ApplyCallbackReplaysCommittedEntriesAfterRestart` |
| 6 | Raft committed state after leader restart | Leader restarts | Leader rejoins as follower; replays committed log | State machine catches up to current term; no divergence | **YES (unit)** | `RaftTest.PersistentLogAndCommitStateRestoreAfterRestart` |
| 7 | Raft follower catch-up | Follower restarts with stale log | Follower replays all missing entries from leader | Follower state matches leader state at same term/index | **YES (integration)** | `V2BackendRoutingTest.LeaderboardFollowerCatchesUpAfterLeaderRestart` |
| 8 | Multi-node restart order perturbation | Multiple followers restart in arbitrary order | Cluster re-establishes quorum after each restart | Leader election succeeds; committed entries preserved | **PARTIAL** | Unit tests cover single-node restart; integration tests cover follower catch-up; no test for multi-node simultaneous restart |
| 9 | Raft-backed leaderboard: committed scores survive restart | Leader crash after score commit | New leader elected, scores replayed from Raft log | All committed scores present after recovery | **YES** | `V2BackendRoutingTest.LeaderboardRestoresCommittedScoresAfterRestart`, `V2BackendRoutingTest.LeaderboardReplicatesCommittedScoresAcrossRaftFollowers` |
| 10 | Raft-backed matchmaking: committed match survives restart | Leader crash after match creation | New leader replays committed match entries | Committed matches restored after recovery | **YES** | `V2BackendRoutingTest.MatchmakingRestoresCommittedMatchAfterRestart`, `V2BackendRoutingTest.MatchmakingReplicatesQueuedPlayersAndMatchesAcrossFollowers` |
| 11 | Write-behind store: data not lost on crash | Process crash before flush | On restart, data is replayed from persistent log | Pending writes survive process crash | **YES** | `V2WriteBehindStoreTest.WriteBehindMultipleWritesAllFlushed`, `V2WriteBehindStoreTest.WriteBehindDestructorFlushesRemaining`, `V2WriteBehindStoreTest.WriteBehindDestructorDrainsLargePendingQueue` |
| 12 | Cached data store backend fallback | Delegate (persistent store) unavailable | Cache serves reads if warm; writes are queued | If cached data exists, reads succeed; writes are queued in write-behind buffer | **YES** | `V2CachedDataStoreTest.LoadFallsBackToDelegateOnCacheMiss`, `V2CachedDataStoreTest.CacheHitAvoidsDelegateRead` |
| 13 | Service bus circuit breaker recovery | Backend timeout triggers circuit open | After timeout, circuit transitions to half-open; probe succeeds | Circuit closes after successful probe; traffic resumes | **YES** | `ServiceBusIntegrity.GatewayBridgeCircuitBreakerHalfOpenProbeRecovers`, `ServiceBusIntegrity.GatewayBridgeRecoversAfterBackendConfigUpdate` |
| 14 | Gateway bridge timeout recovery | Stale connection after backend timeout | Gateway closes stale connection; new connection established | Gateway reconnects on next request; no permanent state leak | **YES** | `ServiceBusIntegrity.GatewayBridgeTimeoutClosesStaleConnectionAndRecovers` |
| 15 | Persistent replay store round trip | Arbitrary crash during write | JSON/SQLite replay store recovers on reload | All re-constructed state is identical to pre-crash state | **YES** | `PersistenceReplayAuditTest.JsonFilePlayerStoreRoundTrip`, `PersistenceReplayAuditTest.SqlitePlayerStoreRoundTrip`, `PersistenceReplayAuditTest.JsonFileBattleReplayStoreRoundTrip`, `PersistenceReplayAuditTest.ReplayPlayerLoadsFramesAndPlaysToCompletion` |
| 16 | Redis live operations | Redis available | Full CRUD operations succeed | Set, Get, Del, Exists, Incr, List, SortedSet, Hash operations all succeed | **YES (live gate)** | `RedisClientTest.*` and `RedisConnectionPoolTest.*` live filters in `verify_specialized_e2e.py` |

---

## Verification Scripts

| Script | Coverage Scenarios | When Run |
|--------|-------------------|----------|
| `scripts/verify_data_recovery_gate.py` | 1, 2, 5, 6, 11, 12, 15 | Per-commit data recovery gate (P3) |
| `scripts/verify_specialized_e2e.py` | 1-10 (redis-live / raft-ha profiles), 16 | Fixed-runner specialized E2E |
| `scripts/verify_stability_soak.py` | 11, 13, 14 | Nightly stability / soak |
| `scripts/verify_production_resilience_gate.py` | 1-16 (orchestrates all sub-gates) | Production resilience gate (P5) |

---

## Key Coverage Gaps

1. **Redis data consistency after failover (Scenario 3)**: No test validates that in-memory fallback data is not automatically written back to Redis on reconnection, or that consistency is maintained if it is. A manual reconciliation procedure may be needed.

2. **Multi-node Raft restart perturbation (Scenario 8)**: No test validates that a cluster survives when multiple followers restart simultaneously or in rapid succession. The existing tests cover single-node restart only.

3. **Redis -> memory -> Redis round-trip consistency (Scenario 3)**: The degraded-mode tests verify that operations gracefully degrade when Redis is unavailable, but there is no end-to-end test for: (a) write while degraded, (b) Redis comes back, (c) verify data is eventually consistent.

4. **Automatic vs. manual Redis recovery**: The codebase documents that Redis recovery after memory fallback requires manual triggering or a re-sync mechanism (`CachedBattleDataStore` flushes on write, but a read-only degradation path may not auto-sync). No test verifies this behavior.

---

## Running the Verification

```bash
# P3 data recovery gate (default path)
python scripts/verify_data_recovery_gate.py --build-dir build/windows-msvc-debug

# P3 with Redis live tests (requires running Redis)
python scripts/verify_data_recovery_gate.py --build-dir build/windows-msvc-debug --include-redis-live

# Specialized E2E -- raft HA profile
python scripts/verify_specialized_e2e.py --build-dir build/windows-msvc-debug --profile raft-ha

# Specialized E2E -- redis live profile
python scripts/verify_specialized_e2e.py --build-dir build/windows-msvc-debug --profile redis-live

# Specialized E2E -- all profiles (raft + redis + operator kind)
python scripts/verify_specialized_e2e.py --build-dir build/windows-msvc-debug --profile all
```
