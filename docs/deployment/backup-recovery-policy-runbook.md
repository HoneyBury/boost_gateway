# Backup And Recovery Candidate Policy

更新时间：2026-07-26

本文档对应 `TODO-0012` 的 Redis production-validation 受治理激活候选、备份/恢复目标和
fail-closed 校验规则。仓库 Compose、host collector unit 和 policy 已进入
`approved_candidate_pending_host_activation`，但目标 Ubuntu 尚未安装或部署该候选；这不构成
主机 AOF 激活、RPO、RTO 或正式恢复证据。

## Repository Contract

- Redis 候选配置：`env/redis/redis.production-validation.conf`
- 备份恢复策略：`deploy/operations/backup-recovery-policy.example.json`
- 旧备份恢复策略：`deploy/operations/backup-recovery-policy.candidate-v1.json`（SHA-256 保持
  `a3af2423357dca4c75b582c0ced366f8ecac66b28b26345998b265bbe5b75d71`）
- 校验入口：`scripts/tools/check_backup_recovery_policy.py`

运行静态校验：

```bash
python3 scripts/tools/check_backup_recovery_policy.py \
  --summary-path runtime/validation/backup-recovery-policy-summary.json
```

通过结果只表示候选配置与策略一致。summary 必须继续报告：

- `candidate_contract_valid=true`
- `governed_candidate_ready=true`
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

当前仓库 `deploy/operations/docker-compose.production.yml` 已把 profile 只读挂载为
`/etc/redis/redis.conf`，并由 `redis-server` 直接读取；该改动只有进入新的 immutable release、
通过保护分支和目标机 upgrade 后才会改变运行主机。目标机当前 deployment 仍是 RDB-only。
批准依据仍是两种 persistence mode 各三轮对照；不得把仓库 mount 当作目标机激活证据。

在 **Ubuntu 小主机终端**运行同机、同 workload 对照。runner 使用 shared lifecycle lock，每种模式
至少三轮并交错顺序；每轮只创建带 TODO label 的临时 volume、internal bridge network、Redis
server 和独立 benchmark client container。server/client 分属固定 CPU/memory/PID cgroup，均使用
`cap-drop ALL`，并在退出前复核 active production Redis image、volume 和 volume identity：

```bash
CONTROLLER=/home/honeybury/boost-gateway-controller
BENCHMARK_ID="todo0012-aof-$(date -u +%Y%m%dT%H%M%SZ)"
REDIS_IMAGE="$(sudo docker inspect boost-redis --format '{{.Image}}')"

sudo python3 \
  "$CONTROLLER/scripts/tools/benchmark_redis_persistence.py" \
  --benchmark-id "$BENCHMARK_ID" \
  --candidate-profile \
    "$CONTROLLER/env/redis/redis.production-validation.conf" \
  --policy \
    "$CONTROLLER/deploy/operations/backup-recovery-policy.candidate-v1.json" \
  --redis-image "$REDIS_IMAGE" \
  --repetitions 3 \
  --requests 10000 \
  --clients 16 \
  --keyspace 100000 \
  --summary-path \
    "/var/lib/boost-gateway-evidence/recovery/$BENCHMARK_ID.json"
```

summary 记录 synthetic Lua leaderboard workload 的 throughput/P50/P99，以及 Redis sampled RSS、
main+children CPU、cgroup v2 I/O、`aof_delayed_fsync` 和 effective config。每轮 workload 后两种模式
都显式执行并等待 BGSAVE，分别记录 steady-state workload 与 checkpoint 的 CPU/I/O 成本；本数据不
冒充真实 SDK 或服务延迟。证据同时绑定干净的 controller `main` commit、runner SHA-256、candidate
policy/profile SHA-256 和 active production Redis image/volume。AOF 无可观测写入、BGSAVE 未完整落盘、
delayed fsync、workload 超时、临时资源残留或 active production binding 漂移均 fail closed。即使
measurement PASS，summary 仍固定
`activation_ready=false`；还需人工 performance review、governed change record、rollback plan、
effective-config 激活验证、release SDK full-flow 和 crash RPO 演练，禁止直接修改生产 Compose。

首份六轮对照证据 `todo0012-aof-20260727T131127Z` 已通过并由
`docs/decisions/todo0012-redis-aof-activation.json` 绑定。人工评审接受 throughput `-4.76%`、
P50 `+0.048ms`、P99 `-0.072ms`、Redis CPU `+8.51%`、RSS `+1.18%`，以及候选 workload
约 `399 bytes/request` 的新增写入；三轮候选 delayed fsync 均为 0，BGSAVE 均成功。该结果只批准
进入受治理 candidate，实现仍必须保持 `activation_ready=false`，且 synthetic Lua 数据不能替代
release SDK、长时磁盘观察或 crash RPO 证据。评审校验入口为：

```bash
python3 scripts/tools/review_redis_persistence_benchmark.py \
  --benchmark-summary /path/to/todo0012-aof-20260727T131127Z.json \
  --decision docs/decisions/todo0012-redis-aof-activation.json \
  --summary-path /create-only/path/todo0012-aof-review.json
```

AOF 激活后的旧 deployment 不是天然的数据兼容回滚点。旧 Compose 使用 RDB-only；直接恢复它会
忽略只存在于 AOF 的已确认写入。任何 AOF→RDB 回退必须先隔离写入、成功执行新鲜 BGSAVE、确认
`changes_since_last_save=0`、离线校验目标 RDB 并记录 checkpoint identity。候选 Redis 无法完成
checkpoint 时必须 fail closed，转入隔离 AOF/异机备份恢复；禁止直接在 active volume 上启动旧
RDB-only Compose。release transition hook 同时负责 RDB→AOF 的显式 AOF seed 和 AOF→RDB 的
checkpoint bridge；两条路径都保留旧持久化文件，不得用删除 `appendonlydir` 作为恢复手段。

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

### Redis-Only Restore Bundle And Isolated Controller

首轮隔离恢复不会把含 `/etc/boost-gateway` 和 Gmail credential 的完整 plaintext tar 从 Mac
复制回 Ubuntu。完整 tar 只在 Mac vault verifier 的流式解密过程中出现；verifier 已验证 schema 2、
link-free member inventory、`source_links` contract、plaintext SHA-256 和 Redis source checksum。

在 **Mac 终端**导出 create-only Redis restore bundle。bundle 固定只含 `dump.rdb`、`bundle.json`、
`manifest.json`、`receipt.json` 和 `vault-validation.json`，不含 host configuration 或 age private key：

```bash
python3 scripts/tools/export_backup_restore_bundle.py \
  --vault-root /Users/honeybury/Backups/boost-gateway-vault \
  --backup-id '<verified-backup-id>' \
  --vault-validation-summary \
    '/Users/honeybury/Backups/boost-gateway-vault/validations/<verified-backup-id>.json' \
  --age-identity \
    /Users/honeybury/.config/boost-gateway-backup/age-identity.txt \
  --bundle-dir \
    '/Users/honeybury/Backups/boost-gateway-vault/restore-bundles/<restore-id>' \
  --age /opt/homebrew/bin/age
```

Mac 到 Ubuntu 必须使用独立 forced-command SSH key。Ubuntu receiver 的
`--receiver-identity-file` 固定为 `/etc/machine-id`；它的 SHA-256 必须等于 bundle 绑定的 source
host identity。receiver 只接受 `boost-gateway-restore store` 和
`boost-gateway-restore receipt <restore-id>`，没有 shell、路径或删除接口。安装后的 authorized key
必须绑定 Mac Tailscale source address、`restrict` 和以下 forced command：

```text
command="/usr/bin/python3 /opt/boost-gateway/restore-tools/restore_bundle_ssh_receiver.py --staging-root /var/lib/boost-gateway-restore-inputs --receiver-identity-file /etc/machine-id",restrict,from="<mac-tailscale-ip>" <mac-restore-public-key>
```

在 **Mac 终端**发送 bundle；identity 和 known_hosts 必须是 regular non-symlink 文件，private key
权限必须为 `0600`：

```bash
python3 scripts/tools/send_restore_bundle.py \
  --restore-id '<restore-id>' \
  --bundle-dir \
    '/Users/honeybury/Backups/boost-gateway-vault/restore-bundles/<restore-id>' \
  --remote-host 'honeybury@<ubuntu-tailscale-ip>' \
  --ssh-identity-file "$HOME/.ssh/boost-gateway-restore-ed25519" \
  --ssh-known-hosts "$HOME/.ssh/boost-gateway-restore-known-hosts" \
  --receipt-path \
    '/Users/honeybury/Backups/boost-gateway-vault/restore-receipts/<restore-id>.json'
```

receiver 在 Ubuntu 的成功目录加入第六个 create-only 文件 `transport-receipt.json`。隔离控制器
严格要求这六个文件，独立重载和复算 manifest、vault receipt、vault validation、transport receipt、
policy、Redis profile 和 RDB 的全部 binding。

在 **Ubuntu 小主机终端**运行隔离恢复。`REDIS_IMAGE` 必须是本机已有的 immutable image ID；目标
volume 必须使用新的 `boost-gateway-recovery-*` 名称：

```bash
RESTORE_ID='<restore-id>'
REDIS_IMAGE="$(sudo docker image inspect redis:7-alpine --format '{{.Id}}')"

sudo python3 \
  /home/honeybury/boost-gateway-controller/scripts/tools/restore_backup_isolated.py \
  --restore-id "$RESTORE_ID" \
  --bundle-dir "/var/lib/boost-gateway-restore-inputs/$RESTORE_ID" \
  --policy \
    /home/honeybury/boost-gateway-controller/deploy/operations/backup-recovery-policy.example.json \
  --redis-profile \
    /home/honeybury/boost-gateway-controller/env/redis/redis.production-validation.conf \
  --target-volume "boost-gateway-recovery-$RESTORE_ID" \
  --baseline-container "boost-restore-baseline-$RESTORE_ID" \
  --target-container "boost-restore-target-$RESTORE_ID" \
  --redis-image "$REDIS_IMAGE" \
  --summary-path \
    "/var/lib/boost-gateway-evidence/recovery/$RESTORE_ID.json"
```

控制器与 release lifecycle 共用 `.lifecycle.lock`。它先用 `redis-check-rdb` 离线检查，再从同一
bundle RDB 启动 `network=none` baseline Redis，使用 `SCAN`、`TYPE`、`DUMP` 生成 canonical
keyspace SHA-256；禁止使用 `KEYS`。随后创建全新 named volume，在任何业务写入前用同一算法验证
fresh-volume target，且强制存在 `lb:global` 和 `lb:global:names`。active production volume 只做
identity inspect，绝不挂载、写入、切换或删除。`DUMP` 返回任意二进制，因此 canonicalizer 以
binary stdout 读取并做 Base64 编码，禁止通过 UTF-8 文本解码。RDB 通过 stdin 流式写入 fresh
volume，写入进程固定为无新增 capability 的 `redis` 用户，且写入后必须在容器内复算 SHA-256；
不得为了写入 `redis:redis 0755` 的 `/data` 给 root 加回 `DAC_OVERRIDE`。失败时只删除本次创建的 target container/volume；
成功时停止隔离容器并保留 target volume 作为演练证据。

该切片只证明 Redis PING、离线 RDB 和 exact canonical seed。summary 必须继续记录
`restore_known_good=false` 和 `formal_todo0012_claim=false`。完整 host link reconstruction、
隔离业务验证、第二份不同 backup/target 演练和最终受控切换仍是后续边界。

### Isolated Restored Business Verification

业务验证不得直接启动或挂载 retained restore volume 作为可写 Redis。控制器先用 `network=none`
audit Redis 对 retained volume 生成 canonical keyspace SHA-256，然后通过只读 source mount 将
`dump.rdb` 复制到新的 disposable work volume。六个 release image 和 work Redis 只加入本次唯一的
`--internal` bridge network；Redis 和 gateway 均不发布 host 端口。控制器严格绑定 network ID、
IPv4 subnet、唯一 network attachment 和空 PortBindings 后，release SDK 才从本机直连 gateway 的
internal bridge IPv4:9201；远程 Docker endpoint 被拒绝，隔离拓扑不能向其他网络建立连接。

在 **Ubuntu 小主机终端**运行。以下 `RESTORE_SUMMARY` 必须指向前一步成功且保留 target volume 的
summary；`RETAINED_VOLUME` 必须与该 summary 的 `target_volume` 完全相同：

```bash
CONTROLLER=/home/honeybury/boost-gateway-controller
RESTORE_SUMMARY='<successful-isolated-restore-summary.json>'
RETAINED_VOLUME='boost-gateway-recovery-<restore-id>'
DEPLOYMENT_RECORD=/opt/boost-gateway/current/record.json
BUSINESS_ID="todo0012-business-$(date -u +%Y%m%dT%H%M%SZ)"

RELEASE_DIR="$(sudo python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["release_path"])' \
  "$DEPLOYMENT_RECORD")"
REDIS_IMAGE="$(sudo python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1]))["redis_image"])' \
  "$RESTORE_SUMMARY")"

sudo python3 \
  "$CONTROLLER/scripts/tools/verify_restored_business_isolated.py" \
  --business-id "$BUSINESS_ID" \
  --restore-summary "$RESTORE_SUMMARY" \
  --deployment-record "$DEPLOYMENT_RECORD" \
  --release-dir "$RELEASE_DIR" \
  --retained-volume "$RETAINED_VOLUME" \
  --work-volume "boost-gateway-business-work-$BUSINESS_ID" \
  --network "boost-gateway-business-net-$BUSINESS_ID" \
  --redis-image "$REDIS_IMAGE" \
  --summary-path \
    "/var/lib/boost-gateway-evidence/recovery/$BUSINESS_ID.json"
```

控制器强制 release record 为 `verified`，绑定 release manifest、六个 immutable image ID 和 release
自带的可执行 `bin/sdk_full_flow_client`，并要求 `source_build_performed=false`。SDK stdout 必须包含
manual leaderboard submit、rank 和 `ALL TESTS PASSED`；top 请求是该 release 客户端成功路径中的
强制调用。控制器再从 work Redis 复核 Alice/Bob 的 `ZSCORE`、`ZREVRANK`、display name 和 top 20，
分别记录 submit/top/rank pass。历史演练用户可能已占满 top 20，因此本轮用户必须有有效 rank，但不要求
rank 小于等于 20；summary 仍显式记录本轮用户是否出现在 top 20。

业务写入后，所有隔离容器、internal network 和 work volume 都必须删除。控制器随后第二次只读挂载
retained volume；前后 canonical keyspace SHA-256、key count、key set 和 volume identity 必须一致，
active production volume identity 也必须保持一致。internal bridge host-direct 不可达、SDK 失败、
cleanup 失败或 retained seed 漂移都会生成 `overall_pass=false` 的 create-only summary。即使本阶段通过，
summary 仍固定 `restore_known_good=false` 和 `formal_todo0012_claim=false`；第二份独立 backup/target、
完整 host link reconstruction 和最终聚合尚未完成。

### Known-Good Attestation

单轮 restore 或 business summary 不能自行声明 `known-good`。每轮隔离恢复和业务验证全部通过后，
先通过受控 Tailscale/SSH 通道把 Ubuntu 上对应的 create-only summary 复制到 Mac。传输前后必须分别
计算 SHA-256；不得复制 Redis volume、credential 或 age identity：

```bash
# Run on the Mac. Repeat with a different backup ID, restore ID and target volume.
BACKUP_ID='<verified-backup-id>'
RESTORE_ID='<successful-restore-id>'
BUSINESS_ID='<successful-business-validation-id>'
INPUT_ROOT="/Users/honeybury/Backups/boost-gateway-vault/known-good-inputs/$BACKUP_ID"

install -d -m 0700 "$INPUT_ROOT"
scp "miniserver:/var/lib/boost-gateway-evidence/recovery/$RESTORE_ID.json" \
  "$INPUT_ROOT/restore-summary.json"
scp "miniserver:/var/lib/boost-gateway-evidence/recovery/$BUSINESS_ID.json" \
  "$INPUT_ROOT/business-summary.json"
shasum -a 256 "$INPUT_ROOT/restore-summary.json" "$INPUT_ROOT/business-summary.json"
```

然后仅在 **Mac vault 主机**创建 create-only attestation：

```bash
python3 scripts/tools/manage_backup_recovery.py attest-known-good \
  --vault-root /Users/honeybury/Backups/boost-gateway-vault \
  --backup-id "$BACKUP_ID" \
  --vault-validation-summary \
    "/Users/honeybury/Backups/boost-gateway-vault/validations/$BACKUP_ID.json" \
  --restore-summary "$INPUT_ROOT/restore-summary.json" \
  --business-summary "$INPUT_ROOT/business-summary.json"
```

attestation 会复制并重新验证 vault validation、restore 和 business 三份原始证据，绑定 encrypted
archive、manifest、remote receipt、source/vault host identity、deployment、Redis profile、RTO、
retained target volume identity、leaderboard submit/top/rank 和 release SDK full-flow。原始 summary
继续保持 `restore_known_good=false`；只有
`known-good/<backup-id>/attestation.json` 可记录 `restore_known_good=true`，且仍固定
`formal_todo0012_claim=false`。

两份 attestation 必须来自不同 backup ID、不同 restore ID 和不同 target volume identity。同一恢复
目标重复运行、证据 SHA 漂移、任一 formal flag 被篡改或只完成 upload/receipt 都不计入最小保留数。

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
`known-good`；成功上传和 receipt 本身不满足这个定义。少于两份有效且 restore target 独立的
attestation 时，`remote-prune` 必须在移动任何 backup 前 fail closed。intent 和 completion 都必须
绑定 retained known-good backup ID 与 attestation SHA-256。

### Scheduled Daily Backups

仓库提供 root-owned oneshot 和 UTC daily timer。远端 SSH target 只写入
`/etc/boost-gateway/backup-remote-host`，age recipient、forced-command private key、known_hosts 和 vault
identity attestation 继续使用 `/etc/boost-gateway` 下的独立 `0600` 文件，不进入 unit、journal 或仓库。
每周一 UTC 的 daily backup 同时标记为 `weekly`；其他日期只标记为 `daily`：

```bash
sudo deploy/operations/install_backup_host_units.sh \
  --remote-host 'honeybury@<mac-tailscale-ip>' \
  --run-now
```

安装器把 backup engine、当前 policy/profile 和 scheduler 复制到 root-owned host controller 目录，启用
`boost-gateway-backup.timer`，并可立即执行一次真实异机备份。scheduler 使用 shared lifecycle lock、
create-only backup ID 和 forced-command transport；它独立回读 archive、manifest 和 remote receipt，验证
distinct host identity、SHA-256、retention class 与 formal flags 后，才在
`/var/lib/boost-gateway-evidence/recovery` 写入 `overall_pass=true` summary。失败同样写 create-only
`overall_pass=false` summary，且不能声明 off-host copy 已验证。

```bash
systemctl list-timers boost-gateway-backup.timer --no-pager
sudo systemctl status boost-gateway-backup.service --no-pager --full
sudo journalctl -u boost-gateway-backup.service -n 100 --no-pager
```

安装 timer 不改变 production Compose、Redis volume 或 Redis profile。所有 backup、vault validation、
restore/business summary 和 known-good attestation 仍固定各自的 formal boundary；只有 repository TODO
source-of-truth 在全部验收证据通过后才能关闭 TODO-0012。
