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
- `/opt/boost-gateway/releases` immutable release state；deployment 中的 release 链接不能替代该源。
- `/var/lib/boost-gateway/deployment-transactions` transaction records。
- `/var/lib/boost-gateway-evidence` evidence ledger 和运行证据。

明文 tar 必须是 link-free archive。遍历 source 时不得跟随或写入 symbolic link；同 inode 的
hardlink 必须物化为独立 regular file，tar member 中 symbolic link 和 hard link 数量都必须为
0。每个 symbolic link 都要先解析并验证最终目标存在，且最终目标位于上述声明 source root
之一。损坏链接、循环链接、目标越界或指向 socket/device/FIFO 的链接必须令整次备份失败。

通过校验的链接只在 manifest 的 `source_links` 中记录。每条记录同时包含
`target_source_id`、`target_relative_path` 和 `target_type`，让恢复端通过受控 source mapping
重建；`original_link_text` 只作审计证据，不能直接作为恢复目标。manifest 还必须绑定
`backup_policy_sha256`，从而使归档契约变更可追溯。

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
3. 解密到受限 staging，确认 tar 中没有 symbolic/hard link，并拒绝 path traversal、额外文件和
   checksum 漂移；随后只依据 `target_source_id` + `target_relative_path` 在 staging 内重建链接，
   不信任 `original_link_text`。
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

## Encrypted Backup Tool Slice

仓库提供三个尚未定时激活的工具：

- `scripts/tools/manage_backup_recovery.py`：在 lifecycle lock 内通过 `redis-cli --rdb`
  生成一致 RDB，生成 link-free tar 和经验证的 link manifest，使用 recipient-only `age` 加密，
  然后以流式帧上传。
- `scripts/tools/backup_vault_ssh_receiver.py`：Mac 上的 SSH forced-command receiver；source key
  只能执行 `boost-gateway-vault store` 和 `boost-gateway-vault receipt <backup-id>`。
- `scripts/tools/verify_backup_vault.py`：在 Mac 上流式解密并复算 plaintext tar、manifest、receipt
  和 host identity；只把 Redis RDB 写入受限临时目录，再用不可变 Redis image 离线校验。

receiver 不接受远端路径、shell、`prune` 或删除操作。上传先进入 Mac vault 的 `.incoming`，验证长度和
双 SHA-256 后重新打开 archive/manifest 回读，生成 create-only receipt，再原子改名为最终 backup
目录。Ubuntu source 收到的 receipt 还必须匹配预置 vault identity digest，且该 digest 必须不同于
source host identity。

当前选定 Mac vault 为：

```text
/Users/honeybury/Backups/boost-gateway-vault
```

在 Mac 上创建稳定 vault identity。该文件不是 age private key，不参与解密；它只用于证明回执来自
预期异机。identity 必须保留，不能每次备份重新生成：

```bash
VAULT=/Users/honeybury/Backups/boost-gateway-vault
IDENTITY="$VAULT/.vault-identity"

install -d -m 0700 "$VAULT"
test ! -e "$IDENTITY"
umask 077
dd if=/dev/urandom of="$IDENTITY" bs=32 count=1
shasum -a 256 "$IDENTITY"
```

将最后的 SHA-256 以单行文本安装到 Ubuntu 的
`/etc/boost-gateway/backup-remote-host-id.sha256`。不要把 Mac 上的 age private identity 复制到
Ubuntu；Ubuntu 只安装 public recipient 到 `/etc/boost-gateway/backup.age-recipient`。

### Forced SSH Key

为自动备份单独生成一把 source key，不复用交互登录 key。在 Ubuntu 上生成 key 后，只把 `.pub`
内容带到 Mac：

```bash
sudo ssh-keygen -t ed25519 -N '' \
  -f /etc/boost-gateway/backup-vault-ed25519
sudo cat /etc/boost-gateway/backup-vault-ed25519.pub
```

先把两个 receiver 文件安装到 Mac 的固定路径。运行中的 forced command 不指向 Git 工作区，避免
切换分支或清理 checkout 改变接收面：

```bash
RECEIVER_ROOT="$HOME/.local/libexec/boost-gateway-backup"
install -d -m 0700 "$RECEIVER_ROOT"
install -m 0500 scripts/tools/manage_backup_recovery.py "$RECEIVER_ROOT/"
install -m 0500 scripts/tools/backup_vault_ssh_receiver.py "$RECEIVER_ROOT/"
install -m 0500 scripts/tools/verify_backup_vault.py "$RECEIVER_ROOT/"
```

先在 Mac 上运行 `command -v python3` 确认绝对路径。然后把下列内容作为 `authorized_keys` 的一行；
`<ubuntu-tailscale-ip>`、`<python3-absolute-path>` 和 `<source-public-key>` 必须替换为实值，整行不能
换行：

```text
from="<ubuntu-tailscale-ip>",restrict,command="<python3-absolute-path> /Users/honeybury/.local/libexec/boost-gateway-backup/backup_vault_ssh_receiver.py --vault-root /Users/honeybury/Backups/boost-gateway-vault --vault-identity-file /Users/honeybury/Backups/boost-gateway-vault/.vault-identity" <source-public-key>
```

source 侧 SSH 调用强制使用 `BatchMode=yes`、`StrictHostKeyChecking=yes` 和
`ClearAllForwardings=yes`，并要求显式传入 regular non-symlink 的 `--ssh-identity-file` 和
`--ssh-known-hosts`；工具同时设置 `IdentitiesOnly=yes`，不依赖 root 的隐式 `~/.ssh` 状态。启用前
必须在 Mac 本机读取 `/etc/ssh/ssh_host_ed25519_key.pub` 的 fingerprint，
与 Ubuntu `ssh-keyscan` 结果人工比对，再把精确 host key 安装到 root 使用的 known_hosts。不能把
`StrictHostKeyChecking` 改为 `accept-new` 或 `no`。

### Vault Verification

上传和 remote readback 通过后，在 Mac 上使用本地不可变 Redis image identity 验证。工具不会把
包含配置 secret 的完整 plaintext tar 写盘；tar 中任何 link、path traversal、重复路径或特殊文件
都会 fail closed：

```bash
python3 "$RECEIVER_ROOT/verify_backup_vault.py" \
  --vault-root /Users/honeybury/Backups/boost-gateway-vault \
  --backup-id '<backup-id>' \
  --age-identity /Users/honeybury/.config/boost-gateway-backup/age-identity.txt \
  --summary-path "/Users/honeybury/Backups/boost-gateway-vault/validations/<backup-id>.json" \
  --age /opt/homebrew/bin/age \
  --docker /usr/local/bin/docker \
  --redis-image 'sha256:<64-hex-image-id>'
```

该 summary 固定记录 `formal_todo0012_claim=false` 和 `restore_known_good=false`。解密、tar 与 RDB
离线校验只能证明备份可读取，不能替代隔离恢复、leaderboard 业务验证或 SDK full-flow。

### Local-Only Retention

Ubuntu source key 无权删除异机备份。14 daily、8 weekly 和至少 2 known-good 的 retention 只能在
Mac 本机执行 `remote-prune`，并且必须以最新 verified remote receipt 的 SHA-256 为 anchor：

```bash
python3 scripts/tools/manage_backup_recovery.py remote-prune \
  --vault-root /Users/honeybury/Backups/boost-gateway-vault \
  --anchor-backup-id '<verified-backup-id>' \
  --anchor-receipt-sha256 '<verified-receipt-sha256>' \
  --daily-copies 14 \
  --weekly-copies 8 \
  --minimum-known-good 2
```

prune 先把候选原子移动到 `.trash/<deletion-id>`，再写 create-only intent；物理删除成功后才写
completion record。删除失败时保留 quarantine 和 intent，不能生成虚假的 completion。只有已完成
解密、离线 Redis 校验、隔离恢复和业务 full-flow 的 backup 才能在后续 evidence 中称为
`known-good`；成功上传和 receipt 本身不满足这个定义。

本工具仍未安装 timer，也未改变 production Compose、systemd 或 Redis profile。生成的 manifest
固定记录 `formal_todo0012_claim=false` 以及 policy activation state；在两轮独立恢复演练通过前，
不得据此关闭 TODO-0012。
