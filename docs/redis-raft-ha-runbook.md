# Redis / Raft HA Runbook

更新时间：2026-05-18

本文档对应生产业务闭环 P4。默认 Docker Compose 生产链路仍是单 Redis、单 matchmaking backend、单 leaderboard backend；Raft 不进入默认部署。`config/environments/ha/` 提供三节点 HA profile 样例，用于固定 runner、预发或正式 HA 环境演练。

## Redis 策略

Redis 是 leaderboard 的生产持久化路径，配置文件为 `env/redis/redis.conf`，Compose 使用 `redis-data` volume。生产至少启用：

- AOF：用于减少异常退出后的写入丢失窗口。
- RDB：用于周期性快照和冷备。
- 定期备份：将 `appendonly.aof`、`dump.rdb` 或云厂商快照复制到异地存储。
- 恢复演练：恢复后运行 SDK full-flow，确认 `battle finish -> leaderboard settlement -> top/rank` 可查询。

Redis 不可达时，leaderboard backend 会降级到内存实现以保护可用性，但该路径不提供持久化保证。排障时必须记录降级窗口，并在 Redis 恢复后重新跑业务闭环验证。

## Raft HA Profile

样例配置：

- `config/environments/ha/matchmaking-node1.json`
- `config/environments/ha/matchmaking-node2.json`
- `config/environments/ha/matchmaking-node3.json`
- `config/environments/ha/leaderboard-node1.json`
- `config/environments/ha/leaderboard-node2.json`
- `config/environments/ha/leaderboard-node3.json`

启动时通过 `CONFIG_PATH` 指向对应文件，或用环境变量覆盖：

```bash
CONFIG_PATH=config/environments/ha/matchmaking-node1.json ./build/default/examples/v2_match_backend/v2_match_backend
CONFIG_PATH=config/environments/ha/leaderboard-node1.json ./build/default/examples/v2_leaderboard_backend/v2_leaderboard_backend
```

生产 HA 环境中，`raft.peers[].host` 必须替换为真实服务发现名称或稳定 Pod/VM 地址；`storage_dir` 必须挂载持久盘，不允许落在临时目录。

## 固定 Runner 验证入口

默认专项：

```bash
python3 scripts/verify_specialized_e2e.py --build-dir build/default --skip-build
```

Redis live：

```bash
python3 scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-redis
python3 scripts/verify_specialized_e2e.py --build-dir build/default --skip-build --profile redis-live
```

Raft HA：

```bash
python3 scripts/verify_specialized_e2e.py --build-dir build/default --skip-build --profile raft-ha
```

`raft-ha` 覆盖 leader election、leader failover、follower catch-up、持久化重启恢复，以及 matchmaking/leaderboard 的 Raft-backed 状态复制。Redis live profile 覆盖 Redis 客户端、event-store、连接池和 leaderboard live sorted-set 路径。

## 上线判定

- 默认生产链路：SDK full-flow 和 Docker production snapshot 均通过即可。
- Redis live HA：除默认生产链路外，必须有 Redis live profile summary，且 Redis backup/restore 演练记录可追溯。
- Raft HA：除默认生产链路外，必须有 `raft-ha` profile summary，且每个 Raft 节点的 `node_id`、`peers`、`storage_dir` 明确写入发布记录。
