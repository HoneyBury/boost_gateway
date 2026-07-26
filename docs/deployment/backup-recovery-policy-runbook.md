# Backup And Recovery Candidate Policy

更新时间：2026-07-26

本文档对应 `TODO-0012` 的第一个仓库切片。它只冻结 Redis production-validation 候选、
备份/恢复目标和 fail-closed 校验规则，不修改当前生产 Compose、systemd unit、timer 或目标机
policy，也不构成备份、恢复、RPO、RTO 或异机留存已经完成的证据。

## Repository Contract

- Redis 候选配置：`env/redis/redis.production-validation.conf`
- 备份恢复策略：`deploy/operations/backup-recovery-policy.example.json`
- 校验入口：`scripts/tools/check_backup_recovery_policy.py`

运行静态校验：

```bash
python3 scripts/tools/check_backup_recovery_policy.py \
  --summary-path runtime/validation/backup-recovery-policy-summary.json
```

通过结果只表示候选配置与策略一致。summary 必须继续报告：

- `candidate_contract_valid=true`
- `activation_ready=false`
- `formal_todo0012_claim=false`
- `live_policy_changed=false`
- `secret_material_recorded=false`

## Redis Decision

候选 profile 同时启用 AOF `everysec` 和 RDB：

- `appendonly yes`、`appendfsync everysec`
- rewrite 期间保持 fsync：`no-appendfsync-on-rewrite no`
- Redis 7 multi-part AOF 使用 RDB preamble
- AOF 截断时拒绝静默加载：`aof-load-truncated no`
- 保留 `save 300 100` 和 `save 60 10000`
- RDB 写入失败时停止新写入：`stop-writes-on-bgsave-error yes`
- `maxmemory-policy noeviction`

`noeviction` 是有意选择。Leaderboard 状态需要能从持久化副本复原；`allkeys-lru` 可能在
备份前静默逐出数据，AOF/RDB 无法恢复已经逐出的 key。容量不足应作为显式写入失败、告警和
扩容信号，而不是被误计为符合 RPO 的正常行为。

当前 `deploy/operations/docker-compose.production.yml` 仍使用 RDB-only inline command，且不
挂载本候选 profile。不得在完成同机、同 workload 的 RDB-only 与 AOF everysec+RDB 三轮对照、
change record 和 rollback plan 前改变该边界。对照至少记录 leaderboard throughput、P50/P99、
Redis CPU/RSS、disk write bytes 和 delayed fsync。

## Backup Contract

每日备份最终必须形成一个 create-only、SHA-256 完整覆盖、先加密后传输的集合，至少包含：

- 一致的 Redis snapshot；不能直接打包正在变化的 Docker volume。
- `/etc/boost-gateway` 配置；其中包含 secret 的内容只能存在于加密载荷中。
- `/opt/boost-gateway/deployments` deployment state。
- `/var/lib/boost-gateway/deployment-transactions` transaction records。
- `/var/lib/boost-gateway-evidence` evidence ledger 和运行证据。

加密候选为 `age` recipient-only 模式。源主机只持有 recipient public material；解密 private
key 不放在源主机，recipient、private key、passphrase、token 或 credential 值不得进入 policy、
manifest、summary 或日志。任何明文 staging 必须在加密成功后清除。

异机目标使用 SSH URI 契约。实现必须校验远端身份与源 host identity 不同，在远端回读并重算
archive SHA-256，然后生成 checksum-bound receipt。同机目录、loopback SSH、只记录上传成功或
只信任 transport exit code 都不能令 `off_host_copy_verified` 成立。

retention 当前冻结为 14 份 daily、8 份 weekly，并始终保留至少两份 known-good backup。
只有远端 copy 和 readback 都验证通过后才能删除旧副本，每次删除必须产生记录。

## Restore Contract

恢复实现不得对 active Redis volume 原地覆盖。标准流程应是：

1. 获取与 release lifecycle 共用的全局 transaction lock。
2. 校验加密 archive、manifest、远端 receipt 和 source/deployment/profile identity。
3. 解密到受限 staging，并拒绝 symlink、path traversal、额外文件和 checksum 漂移。
4. 用 `redis-check-rdb` 与 `redis-check-aof` 离线检查后写入新的 named volume。
5. 在隔离目标启动 Redis，验证原始 leaderboard seed 完全一致。
6. 执行 Redis PING、leaderboard submit/top/rank 和 release SDK full-flow。
7. 全部通过后才允许受控切换；失败时恢复原 volume，并保留失败现场和 summary。

RTO 从故障或受控隔离开始计时，到业务验证全部通过为止。Gateway/backend 上限为 300 秒；
Redis restore、host reboot 和 release rollback 上限为 600 秒。RPO 必须从备份/故障时间线和
恢复后的业务状态测量，不能硬编码为 0。

至少执行两轮独立演练。两轮必须使用不同 backup ID、不同 restore target，并各自绑定 source
host、deployment、Redis profile、backup manifest 和 remote receipt SHA-256。

## Activation Boundary

本切片之后仍需依次完成：

1. 选择真实异机目标，安装并验证 recipient/remote identity，不记录 private key。
2. 实现一致性 snapshot、加密、传输、readback、receipt 和 retention 工具及 timer。
3. 在隔离环境完成损坏备份、错误 recipient、同机目标和 restore rollback 负向演练。
4. 形成 AOF 性能对照并通过 governed change 激活 Redis profile。
5. 在目标 Ubuntu 主机完成两轮保留备份恢复及全部 RTO/RPO/业务验证。

任何一步只产生静态 summary、本机 archive 或同一 volume restart，都不得关闭 `TODO-0012`。
