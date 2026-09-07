# Mac 外部运营职责迁移到阿里云 Runbook

更新时间：2026-09-07

本文档定义把 Mac 承担的外部业务 canary、Alertmanager SSH tunnel、异机备份 vault 和
异机 evidence verification 迁移到 Tailscale 节点 `aliyunserver` 的受控步骤。迁移后
`miniserver` 继续是唯一生产服务主机，继续运行 Boost Gateway、Redis、Prometheus、
Alertmanager、Grafana、exporter、生产备份生成器和本机 evidence timer。

本 runbook 不迁移生产服务、生产 Prometheus 数据或 SMTP relay，不把阿里云主机声明为
[`operations-host-policy.json`](../../deploy/operations/operations-host-policy.json) 所定义的
生产运营主机，也不改变已经完成的 `TODO-0011`、`TODO-0012`、`TODO-0013` 和
`TODO-0016` 历史结论。

## 2026-09-07 执行快照

本节是迁移收口时的事实快照，不代替后续验收记录，也不把预检样本计入 `TODO-0017`。当前
`miniserver` 生产栈未迁移；backup source、external canary、watchdog、Alertmanager forward 和
evidence archive 的日常职责已切到阿里云，四项迁移门禁全部 PASS 并已正式签收，但新的
Day 0 尚未声明。

| 范围 | 当前状态 | 已验证事实 | 仍需完成 |
|---|---|---|---|
| 阿里云主机基线 | 已验证 | SSH key-only、UFW/Tailscale 边界、持久 journal、NTP、swap、Docker/Compose 和 age 已就绪；Tailscale direct；OOM 后正常 reboot 到 boot ID `3c82a323-4bd1-4804-9bc9-842ed8b4458f`，相关 unit 自动恢复且 failed unit 为零 | 持续采集容量、listener、Tailscale 和 unit 状态；所有大型文件检查必须流式 |
| external canary 预检 | 已切换但非正式窗口 | Mac 停止后至少 11 个连续纯云自然分钟 PASS、无 incident；`09:27 CST` 前相同 synthetic identity 的并发竞态不属于生产异常 | 另选未来自然 UTC 分钟声明 Day 0；现有分钟不计入 |
| Alertmanager forward | 已验证并签收 | 阿里云只监听 `127.0.0.1:19093`；9093 正向通过，shell、9090 local 和 reverse forward 均拒绝；最终 drill 计数 23→25、failed=3，两条目标端 Message-ID 已写入最终 create-only attestation `7f25b5f…` | 保留 attestation 与 preflight digest，持续检查投递 |
| active vault 与 source cutover | 已验证 | 两份不同 restore/business/known-good 独立 PASS；修复后第三份 backup PASS；迁移后首个自然 scheduled backup `todo0012-scheduled-20260907T022120Z-e2fa52e1` 自动 upload/readback PASS，当前 4 backups / 2 known-good / incoming 0 / trash 0 / logical bytes `5,428,028,032` / free `27,361,124,352` | backup timer 下一次 `2026-09-08 10:21:07 CST`；持续检查 freshness |
| Mac 历史冷档 | 已验证 | 阿里云冷档中的迁移 partial 为 `2,108,033,646` bytes、70 个文件，`SHA256SUMS` 全部通过；其中两份完整历史 backup 与 Mac checksum 相同，另有一份未完成传输且不具备恢复资格 | 保留为只读历史；不得加入 active retention 或宣称未完成传输有效 |
| retention 调度 | 已验证并启用 | receiver/known-good/retention 共锁与容量修复已重部署；`prune-20260907T014058Z-c5dd7494` PASS；当时快照为 3 backup / 2 known-good、空删除集、logical bytes `4,063,038,013`、free `28,906,655,744` | retention timer enabled/active，下一次 `2026-09-07 12:04:53 CST`；不要与 backup timer 混淆 |
| production 回归 | 已验证 | 迁移签收时所用 controller 下的 lifecycle `status`/`verify`、observability preflight、SMTP relay 和 13 个 governed production containers 均 PASS，生产卷 identity 未改变 | controller 治理对齐属于迁移后的独立工作；不得把后续兼容验证结果追写成迁移时事实 |
| evidence round trip | 已验证 | package `todo0017-aliyun-cutover-20260906T205500Z` 在阿里云校验 PASS，create-only receipt 已回传 `miniserver` 受保护 raw evidence | 保留 package/manifest/receipt digest，不改写原包 |
| Mac 收口与 Day 0 | 迁移已签收；Day 0 待声明 | 四个迁移门禁全部 PASS；LaunchAgent disabled/unloaded、配置保留；最终 canary archive 40,594 files / 70,130,774 bytes，checksum/readback PASS；Mac runner、Docker/CWA 与原 vault 未触碰 | 选择尚未采样的未来自然 UTC 分钟声明 Day 0；不得追溯 |

Mac 原 vault 约 43 GB、42 份 backup，仍留在 Mac 且未被移动、裁剪或删除。阿里云 cold
archive 不受 active vault 的 20 GB 上限保护；因此“active vault 不超过 20 GB”不能被解释成
整台主机、整个 `/srv` 或所有历史数据合计不超过 20 GB。

## 目标拓扑

```text
                                  Tailscale
  aliyunserver                                                     miniserver
  ┌─────────────────────────────┐                   ┌─────────────────────────────┐
  │ external canary + watchdog  │── TCP 9201 ─────▶│ gateway production endpoint │
  │ 127.0.0.1:19093 tunnel      │── SSH tunnel ───▶│ 127.0.0.1:9093 Alertmanager │
  │ create-only backup vault    │◀─ forced SSH ────│ scheduled backup producer   │
  │ off-host evidence verifier  │◀─ verified copy ─│ evidence ledger/package     │
  └─────────────────────────────┘                   └─────────────────────────────┘
             │
             └── active rolling vault: 20,000,000,000-byte budget

  Mac: stopped external jobs + independently retained historical cold archive
```

边界固定如下：

- `miniserver` 的 deployment、production host identity、Compose topology、数据卷、45 天
  Prometheus retention、Alertmanager/SMTP 配置和 release lifecycle 不因迁移而改变。
- `aliyunserver` 只运行外部工作负载。Prometheus、Grafana、Redis 和生产容器不得为了本迁移
  在该机长期运行；离线 backup validation 使用的短生命周期容器除外。
- 9090、9093、Redis、exporter 和 Docker API 不向公网或 Tailscale 直接发布。外部 canary
  只通过受限 SSH local forward 访问生产 Alertmanager。
- Mac 上迁移前形成的 backup、known-good attestation、canary sample、ledger package 和
  delivery receipt 进入独立冷档，不与阿里云 active vault 合并，也不计入其十进制
  20,000,000,000-byte 上限。
- 迁移不允许把 secret、token、private key、SMTP credential、canary credential 或 age
  identity 写入 Git、命令行、journal、evidence summary 或本文档。

## 当前主机事实与准入边界

`aliyunserver` 为 Ubuntu 24.04 x86-64、2 vCPU、约 1.6 GiB memory、1 GiB swap 和 40 GB
单根盘。Docker 29.1.3、Compose 2.40.3、age、持久 journald 与时间同步已安装或启用；最近
一次 active-vault retention 检查有 `28,906,655,744` bytes filesystem free，至 `miniserver`
的 Tailscale 路径为约 6 ms direct。
这些数字只描述最后一次迁移预检，不是持续准入证据，最终验收必须重新采集。

由于该主机不满足生产运营主机的 12 logical CPU、16 GB memory、512 GB disk 等门槛，
它只能承载本 runbook 的轻量 off-host 角色。正式启用前至少满足：

1. 为 canary、tunnel、vault receiver 和短生命周期验证留出经过实测的 CPU/memory 余量；
   当前 1.6 GiB memory 不足以安全并行运行完整监控栈，必要时先扩容。
2. active vault 对 `/srv/boost-gateway-vault` 下所有 regular file 的 logical bytes 实施十进制
   `20,000,000,000` bytes（约 18.63 GiB）应用层硬门禁，并在任何 upload、known-good
   attestation 或 retention 完成后保持至少 `5,000,000,000` bytes filesystem free。独立数据盘
   或 filesystem quota 是额外的
   defense-in-depth，当前不能假定已经具备；系统盘剩余容量也不能被当成独立备份故障域。
3. Tailscale、NTP 和持久 journald 在重启后自动恢复，节点身份不会在正式窗口内到期。
4. 到 `miniserver` 的 9201 和 SSH tunnel 连续可用。DERP 路径只有在 canary 时限、backup
   上传/readback 和恢复演练均通过时才可接受，否则必须先恢复 direct path。
5. 阿里云安全组与主机防火墙均拒绝公网 SSH 和所有监控/备份端口；唯一入站管理和 vault
   传输来自受控 Tailscale 身份。
6. Mac 专用 ARM64 runner 与本机开发/CWA 容器不迁移到阿里云。该 runner 当前保持 offline；
   本迁移不能被解释为恢复 macOS runner 容量，也不接管 CWA 的书库数据或应用生命周期。

### 2026-09-07 内存事故与约束

一次运维侧只读检查错误地使用 Python `read_bytes()` 将约 1.349 GB backup archive 整体载入
内存，在这台约 1.6 GiB 主机上触发 global OOM，并使 SSH 暂时不可用。journal 与调用路径确认
它不是 canary、vault receiver、backup source 或 retention service 发起；vault 操作没有删除、
覆盖或损坏任何数据，也没有 P0/P1 生产事故。主机随后通过正常 reboot 恢复，当前 boot ID 为
`3c82a323-4bd1-4804-9bc9-842ed8b4458f`，Tailscale、Docker、forward、canary timers 和
retention timer 均自动恢复，failed unit 为零。

`/etc/sysctl.d/99-z-boost-gateway-memory.conf` 已固定 `vm.swappiness=10`。这是辅助缓冲，不是
允许高内存诊断的理由：所有 archive copy、checksum、解密验证、清单生成和差异复核都必须使用
流式 I/O 或有界缓冲；禁止对大型文件调用 `read_bytes()`、无界 `read()` 或等价的整文件内存读取。

## 身份与证据连续性

三个身份不可混用：

- production host identity：仍由 `miniserver` 的 `/etc/machine-id` 派生，迁移中不改变。
- external canary host identity：改为 `aliyunserver` 的 `/etc/machine-id` SHA-256。
- vault identity：在阿里云 active vault 上新建并保持稳定，只向 `miniserver` 安装其摘要。

不得把 Mac 的 `.vault-identity` 或 age private identity 当成阿里云新身份复制过去。新 vault
使用新的 vault identity 和新的 age identity/recipient；Mac 冷档保留原身份和解密材料，
用于验证历史 archive。`miniserver` 只持有新 recipient 的 public material，不持有任何
新旧 age private identity。

当前迁移把解密能力进一步物理分栏：阿里云 active identity 位于受保护的
`/etc/boost-gateway-vault/age-identity.txt`，历史验证专用 legacy identity 位于
`/etc/boost-gateway-vault/mac-legacy-age-identity.txt`；两者都只能由 root 读取。legacy
identity 只能验证 `/srv/boost-gateway-archive/` 下的历史冷档，不能用于 active backup；新
identity 也不能改写旧 receipt。active `/srv/boost-gateway-vault` 使用新生成的 32-byte
`.vault-identity`，只有其 SHA-256 才安装到 `miniserver`。

external canary host identity 是正式窗口声明的一部分。从 Mac 切换到 `aliyunserver` 必然
改变该身份，因此任何正在累计的 `TODO-0017` 窗口必须标记为 `superseded`：

- Mac 已产生的分钟和历史 PASS 原样保留，不删除、不改写，也不拼接到新窗口。
- `TODO-0011` 至 `TODO-0016` 已完成证据不会因此失效。
- 只有阿里云预检、连续自然分钟、告警投递、异机备份和恢复验收全部通过后，才能选择一个
  尚未采样的自然 UTC 分钟声明新的 `TODO-0017` Day 0。
- 新窗口必须重新累计完整 30 天；Mac 时间、迁移维护时间和预检样本均不计入。

截至本快照，新的 `TODO-0017` Day 0 **尚未开始**。切换前持续 PASS 只证明阿里云预检路径
可用，不能追溯升级成正式窗口。

只在 evidence 中记录 identity digest、文件 digest、UTC 时间、节点角色和检查结果。不要
记录 `/etc/machine-id` 原文、vault identity 原文或任何 credential 值。

## Phase 0：冻结和备份迁移输入

在改变任一 scheduler 前：

1. 在 `TODO-0017` 的受治理记录中声明迁移维护区间、操作者、旧/新角色、停止条件和回滚条件。
2. 记录 `miniserver` 当前 deployment、release、config、production host identity、容器、
   Prometheus targets/rules、backup timer 和最近 verified remote receipt 的 digest。
3. 在 Mac 固定最后一个 canary sample、watchdog 状态、vault inventory、两份独立 known-good
   attestation、最终 evidence package 和各自 SHA-256。当前 Mac vault 约 43 GB、42 份
   backup，迁移过程中不得原地裁剪。
4. 为 Mac 冷档生成只读 inventory/manifest，另存一份 manifest；不要移动、重命名或删除
   archive 来制造“迁移完成”。当前阿里云 partial cold archive 固定在
   `/srv/boost-gateway-archive/mac-vault-partial-20260907`：逻辑 regular-file 总量为
   `2,108,033,646` bytes、70 个文件且 checksum 全部通过。两份完整目录只能作为历史应急
   continuity，第三份未完成传输必须明确标记 invalid/incomplete。
5. 保留 Mac 的 LaunchAgent、receiver 和配置；数据面、恢复、retention、重启恢复、最终 evidence
   sync 与纯云连续样本通过后，可以 disable/unload scheduler 并让 Mac 断电，但不得删除 plist、
   配置或历史 vault。首个完整 weekly 周期作为迁移后持续观察里程碑，不要求 Mac 为此持续通电。

若当前 `TODO-0017` 已有声明窗口，先保留该窗口所有原始数据并将其 supersede。不得等迁移
结束后追溯修改起点。

## Phase 1：加固 `aliyunserver`

所有命令通过当前仍可用的阿里云 console 和经 Tailscale 的 OpenSSH 双路径执行。关闭入口前必须从
第二个会话验证替代入口。

最低安全基线：

1. 创建独立的 sudo operator；canary 使用 installer 创建的 `boost-gateway-canary` nologin
   身份，vault receiver 使用专用 forced-command 身份。应用身份不得属于 `sudo` 或 `docker`。
2. SSH 关闭 password 和 keyboard-interactive authentication。root 登录只有在非 root sudo
   和 console recovery 验证后才能关闭；过渡期最多保留 key-only root。
3. UFW default incoming deny、outgoing allow，只允许 `tailscale0` 上的 SSH。阿里云安全组
   同时删除 `0.0.0.0/0` 和 `::/0` 的 TCP 22；安全组不能替代主机防火墙证据。
4. tunnel、canary、vault receiver 和 verifier 都由 root-owned systemd unit 管理；配置、
   `EnvironmentFile`、known_hosts 和 private key 为 root-owned regular non-symlink file，
   mode 不宽于 `0600`/`0640`。
5. 为 canary tunnel、vault upload 和管理登录分别使用独立 key。vault source key只能执行
   receiver 的 `store`/`receipt` surface，必须包含 source Tailscale 限制、`restrict` 和固定
   forced command，不允许 shell、PTY、agent/X11/TCP forwarding 或远端删除。
6. 固定 SSH host key。backup/restore SSH 调用使用 `BatchMode=yes`、
   `StrictHostKeyChecking=yes`、`IdentitiesOnly=yes` 和 `ClearAllForwardings=yes`；canary
   tunnel 不使用 `ClearAllForwardings=yes`，而是使用仓库 unit 固定的 `ssh -F /dev/null`
   和唯一 `-L 127.0.0.1:19093:127.0.0.1:9093`。host-key checking 不得改为
   `accept-new` 或 `no`。
7. Tailscale ACL 只允许指定 operator 管理该节点，并只允许 `miniserver` 对 vault receiver
   建立 SSH。确认节点 key expiry 覆盖整个新 30 天窗口及收口期。

验证时至少保留以下无 secret 输出：

```bash
sudo ufw status verbose
sudo sshd -T | grep -E \
  '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication|pubkeyauthentication) '
sudo ss -lntup
systemctl --failed --no-pager
timedatectl
tailscale status
tailscale ping miniserver
```

除 SSH 外不应存在 wildcard TCP listener。canary Alertmanager 入口必须仅为
`127.0.0.1:19093`。

## Phase 2：建立新的 active vault

active vault 固定在：

```text
/srv/boost-gateway-vault
```

独立数据盘或 filesystem quota 可以加强故障隔离，但不是当前 20 GB 声明的依据。仓库的
[`aliyun-backup-vault-retention-policy.json`](../../deploy/operations/aliyun-backup-vault-retention-policy.json)
把应用层容量口径固定为该 active root 下所有 regular file 的 `logical_regular_file_bytes`；
directory entry、filesystem allocation 和 `/srv/boost-gateway-archive` 不属于这一数字。

使用 `install_backup_vault_host_units.sh` 把 receiver、known-good attestation 和 retention
脚本安装到 root-owned 固定
目录，不从 Git checkout 直接执行。installer 建立 root-controlled `0750` vault root、32-byte
`.vault-identity`、不可替换的 `.vault.lock` 和 service-owned `0700` 数据目录；非空 legacy
layout 默认 fail closed，不能把旧 identity 的数据静默接入 active root。新 age private
identity 只存在于受保护的阿里云 vault/verifier 侧，public recipient 才能复制到
`miniserver`。生成过程不得把 private identity 输出到终端。

在 `miniserver`：

1. 为新 vault 生成专用 source key，不复用管理、旧 Mac vault 或 canary tunnel key。
2. 通过独立渠道比对并安装 `aliyunserver` 的 ED25519 host key。
3. 安装新 vault identity digest 和新 age recipient；先保留旧 Mac 文件的受保护副本以便回滚。
4. 暂不改变 daily timer 的正式 target。先用一次 create-only 测试备份验证 upload、remote
   readback、archive/manifest digest 和 receipt identity。
5. 分别用不同 backup ID、restore ID 和隔离 target volume 完成两次恢复与业务 full-flow，
   在新 vault 形成至少两份有效 known-good attestation。

本次执行已经完成上述 active-vault 和 source-cutover 步骤。实际结果为：

| 对象 | 独立标识 | 验证结果 |
|---|---|---|
| backup A | `todo0012-scheduled-20260906T200739Z-87a4e10e` | source summary、upload、remote readback 和 create-only receipt PASS |
| backup B | `todo0012-scheduled-20260906T201746Z-7959d44d` | source summary、upload、remote readback 和 create-only receipt PASS |
| backup C（修复后） | `todo0012-scheduled-20260907T012921Z-0ac0e17c` | archive `1,364,936,232` bytes；source summary SHA-256 `662b17f154cb8ffb3406c9a2a5ef5bed21a23610b370ba706519267aaefedfbc`；remote receipt SHA-256 `7a163b0f36004ab2db23124b9d77c210955f4038ec1418560ea839c00dcc7539`；upload/readback PASS |
| backup D（首个自然调度） | `todo0012-scheduled-20260907T022120Z-e2fa52e1` | `2026-09-07 10:21:20–10:30:34 CST` 由 source timer 自动运行；archive `1,364,966,952` bytes / SHA-256 `151cefbe45a131f6cb7e6edde5f152d23cf35ee593314310f066d1eed8d5a966`；manifest SHA-256 `81c58c5cb443f6d9873b915115068227372615040f066dc28de1606ae156363a`；source summary SHA-256 `369646371dbd935e3e548e3a4c425f233b426e521bb6f46e41107c4e801159f5`；receipt SHA-256 `735312aa35132a9ac0f48bd56d383b749bed67fc668a57aca4497dc97e54283e`；PASS |
| restore/business A | target volume `boost-gateway-recovery-todo0017-aliyun-a`；target identity `a61db9e89119016de4c136b25b81d449872b0d3e1379c23a155609962a349295` | restore summary SHA-256 `9dcf73867f2290cf2e5d27f9f25e306d8ae18c55d19de025a86d0cc43f478e24`；business summary SHA-256 `de20b0a7a08a34e8d8a590954005ee3a7570f78eb63d666611d4aecde04a57bf`；PASS |
| restore/business B | target volume `boost-gateway-recovery-todo0017-aliyun-b`；target identity `a06a7e7a8ca0343d12066d5e4dc31cd80138f19b55f71808981487a40becd80d` | restore summary SHA-256 `07ad3e88592db35af5d7319dd8d143f3255e9e4d5f7fa25f920456497d0b6f39`；business summary SHA-256 `253a41eaded4fa44b2afb7a27e56b776b09c16658e285792eb298aa796bdc90d`；PASS |

两份 known-good 的 backup、restore、target volume 和 target identity 均不同；生产 Redis volume
identity 保持为
`a1c6b9745a162488e14f45fdcc92624d26418ea741290d1d12b8c9f07bab4d4d`。共锁/容量修复已经
重部署，修复后的 backup C 证明 receiver 写入/readback 路径在线。随后 policy-bound retention
以 deletion ID `prune-20260907T014058Z-c5dd7494` PASS，保留 3 份 backup 和 2 份
known-good、`deleted_backup_ids=[]`，完成后 active vault 为 `4,063,038,013` logical bytes，
filesystem free 为 `28,906,655,744` bytes。intent SHA-256 为
`93dabc2c2bd7a0e525109b50c1075c50f3e84c96242b0aa81b611862bd23f132`，completion
SHA-256 为 `5de3f2c8559c72d3b98e56e7482aa7a10230e641a814a992e9ace8818c0a32c4`。retention timer 已
enabled/active，下一次计划运行时间为 `2026-09-07 12:04:53 CST`。这些数值是 retention run
完成时的历史快照。此后 backup D 自然完成，`overall_pass/create_only=true`、
`off_host_copy_verified/remote_readback_verified=true` 且 source/cloud receipt byte-identical；active vault 当前为 4 份
backup、2 份 known-good、`.incoming=0`、`.trash=0`，logical bytes `5,428,028,032`、free
`27,361,124,352`，source backup timer 下一次为 `2026-09-08 10:21:07 CST`。目标端 delivery
由下文最终 create-only attestation 独立证明，不与 backup/retention 证据混用。

forced-command receiver、receipt、解密验证和 known-good 的详细契约继续服从
[`backup-recovery-policy-runbook.md`](backup-recovery-policy-runbook.md)。任何仅传输成功、
同机复制、无 readback、无隔离恢复或共享 restore target 的结果都不能称为 known-good。

### 20,000,000,000-byte rolling policy

阿里云 active vault 的 scoped override 固定以下两个容量条件：

- regular file logical bytes 不超过十进制 `20,000,000,000` bytes（约 `18.63 GiB`）；
- upload、known-good attestation 或 retention 完成后的 filesystem free 不少于十进制
  `5,000,000,000` bytes。

保留目标为：

- 最新 7 份 `daily`；
- 最新 4 份 `weekly`；
- 至少 2 份由不同 restore ID 和不同 target volume identity 证明的 `known-good`。

同一 backup 可以同时属于 daily、weekly 或 known-good，因此三类数量不能简单相加。receiver、
known-good attestation writer 和 retention runner 通过同一个 root-controlled `.vault.lock`
的 exclusive `flock` 串行化，upload、attestation 和 prune 不能并发修改 vault。

该行为只适用于阿里云 active vault 的受治理布局。known-good attestation writer 在写入前、
峰值和完成态都执行 20 GB logical cap 与 5 GB free-floor 检查。它先预留每个 source evidence
file 的 size，再在持有同一 vault lock 时按预留 size 流式读取并绑定内容；文件增长、size/digest
不匹配或任一容量阶段失败都会 fail closed，不留下可用 attestation。这防止验证后到复制间的
源文件增长竞态，也确保 receiver、attestation 和 retention 使用同一容量预算。

兼容边界由 vault layout 自动决定，调用者和 SSH forced command 都没有降级开关：如果
`<vault>/.vault.lock` 存在，receiver、store、receipt 和 known-good attestation 都必须验证完整
secure layout，再使用该固定锁；store/attestation 同时执行 20 GB/5 GB 门禁。如果该路径是
symlink、目录、其它异常 entry，或 lock/identity/layout 的权限、owner 不安全，则 fail closed，
不得静默降级。只有 vault root 中完全没有 `.vault.lock` 的 legacy Mac vault 才保持原 receiver、
store、receipt、attestation 和本机 `remote-prune` 流程，且不套用 active vault 的 20 GB/5 GB
门禁。Mac 原约 43 GB vault 与阿里云 partial cold archive 因此不宣称具备新门禁，也不应为获得
该声明而原地创建 lock 或改写 layout。

secure-layout receiver 在读取 payload 前按
`archive_size + manifest_size + 4,096-byte receipt reservation` 预留容量，并同时要求：

- `current logical bytes + reservation <= 20,000,000,000`；
- `current filesystem free - reservation >= 5,000,000,000`。

随后它把数据写入 `.incoming`，逐文件 remote readback SHA-256、验证 manifest binding，最后才
生成 `stored_at` 和 create-only receipt；完成阶段再次检查 20 GB/5 GB 条件，通过后原子 rename
到 `backups/`。任一步失败都会删除该临时 incoming directory，不形成可用 receipt。`du` 可用于
观察 filesystem allocation，但不能替代上述 logical-byte 口径。

retention 应通过 policy-bound systemd service 手工触发：

```bash
sudo systemctl start boost-gateway-backup-vault-retention.service
sudo systemctl show boost-gateway-backup-vault-retention.service \
  -p Result -p ExecMainStatus -p ActiveState -p SubState
sudo journalctl -u boost-gateway-backup-vault-retention.service -n 50 --no-pager
```

该 unit 以 `boost-gateway-vault:boost-gateway-vault` 身份执行、绑定准确 policy，并由 runner 在
root-controlled `.vault.lock` 上取得 exclusive `flock`。它是 `Type=oneshot`；成功完成后显示
`inactive/dead` 是预期状态，应以 `Result=success`、`ExecMainStatus=0` 和 JSON summary 判断
PASS。不要以 root 直接运行 runner，也不要用通用 `remote-prune` 代替这个入口；前者绕过
systemd 的 service UID/group 与 sandbox，后者不绑定阿里云 scoped 7/4/2、20 GB/5 GB policy。
兼容 CLI 只允许对完全没有固定 lock 的 legacy vault 执行 `remote-prune`；只要固定 lock 路径存在
任何 entry（包括异常 entry），CLI 就在调用 prune 前拒绝并要求使用 policy-bound service。内部
runner 持有固定锁后仍直接调用 guarded prune，不经过这条兼容 CLI 拒绝路径。

runner 只接受当前 `.vault-identity` 的 readback-verified receipt，并自动选择最新 verified
receipt 为 anchor。它在任何删除前构造精确 keep/delete plan、验证两份不同 restore ID 和
target volume identity 的 known-good、计算待删除 logical bytes 及 deletion evidence 自身的
bytes。如果 mandatory keep set 加 deletion evidence 仍超出 20 GB，则以 **zero deletion**
失败；只有预检通过才调用 guarded prune，随后再次验证容量上限、free floor、intent 和
completion 记录。旧 Mac identity 的 receipt 不能进入 plan。

retention timer 在 bootstrap 安装后必须先保持 disabled/inactive。两份**阿里云新 identity**
的独立 known-good 全部通过，并且 active-vault 共锁/容量实现已部署和复验后，才允许启用
`boost-gateway-backup-vault-retention.timer`；其基准计划为每日 `04:00 UTC`，systemd 最多
增加 10 分钟稳定随机延迟。启用前和每次策略变化后都应先通过上述 systemd service 手工运行
一次，并保存 create-only intent/completion 的 digest。本次两份 known-good 已通过；修复后的
policy-bound service 已以 `Result=success`、`ExecMainStatus=0` 和
`prune-20260907T014058Z-c5dd7494` 实跑 PASS。空删除计划 `deleted_backup_ids=[]` 是当前只有
mandatory keep 数据的合法结果；timer 已重新 enabled/active，下一次为
`2026-09-07 12:04:53 CST`。oneshot 完成后 `inactive/dead` 仍是正常状态。

若 7 daily、4 weekly、2 known-good 的合法保留集合或下一份候选无法容纳在十进制
20,000,000,000 bytes 内：

- fail closed；上传 reservation 不满足时拒收候选，mandatory keep set 不满足时 retention 在
  删除前退出，同时停止 Day 0 声明；
- 扩大独立数据盘/配额或增加另一个受验证的远端副本；
- 不得降低两份 known-good、删除未验证副本、删除 Mac 冷档或绕过 guarded prune。

Mac 冷档不参与上述滚动删除。旧 vault identity、legacy age 解密能力、历史 receipt 和
evidence manifest 必须作为一个整体离线保留；Mac 在最终切换验收后不需要继续通电，但冷档
不得成为没有第二份 inventory/manifest 的唯一副本。当前阿里云 partial cold archive 的
`2,108,033,646` bytes 也不计入 active cap，必须单独监控系统盘总 free space。

## Phase 3：安装 tunnel 并暂存 external canary

使用与当前 candidate 声明一致的已发布 Linux x86-64 SDK wheel；当前外部协议基线为 SDK
4.2.0。先按 `SHA256SUMS.txt` 验证资产，再安装到
`/opt/boost-gateway-canary/venv`，不得从 repository checkout 导入 SDK 或在阿里云编译
production runtime。

从 `miniserver` 通过认证通道导出当前不可变 deployment record。在阿里云以 root-owned
`0600` 文件暂存 canary environment。先只验证 asset checksum、venv 中 SDK 的实际版本、
environment schema、deployment record 和新旧 host identity，不启动周期任务。

目标态先在 `miniserver` 安装专用 target account：

```bash
sudo ./deploy/operations/install_alertmanager_forward_target.sh \
  --source-address '<aliyunserver-exact-tailscale-address>' \
  --source-public-key-file <staged-forward-public-key>
```

installer 创建 nologin 的 `boost-gateway-alert-forward`，root 持有其 home、`.ssh` 和
`authorized_keys`。key 同时绑定 exact Tailscale source、forced `/bin/false`、`restrict`、
`port-forwarding` 和唯一 `permitopen="127.0.0.1:9093"`；sshd `Match` 再次固定 publickey-only、
local forwarding、`MaxSessions 0`，并禁止 shell、PTY、agent/X11、remote/reverse/streamlocal
forward、user rc 和其它目标。核心授权为：

```text
restrict,port-forwarding,permitopen="127.0.0.1:9093"
```

然后在阿里云运行仓库 installer；它会安装并启动 root-owned
`boost-gateway-canary-alertmanager-forward.service` unit（进程使用独立非 root
`boost-gateway-canary-forward` UID，key/known_hosts 由 systemd credential 注入），并在返回
PASS 前验证固定 forward 的 Alertmanager readiness：

```bash
sudo ./deploy/operations/install_external_canary_alertmanager_forward.sh \
  --ssh-target 'boost-gateway-alert-forward@<miniserver-tailscale-host>' \
  --ssh-identity-file <root-owned-forward-identity> \
  --ssh-known-hosts <root-owned-pinned-known-hosts>
```

该 service 明确使用 `ssh -F /dev/null`、独立 credential、固定
`-L 127.0.0.1:19093:127.0.0.1:9093` 和 publickey-only authentication，不继承 root 或
service account 的 SSH config。不得给它添加 `ClearAllForwardings=yes`，否则固定 local
forward 也会被清除。

本次 live tunnel 已切换到专用 `boost-gateway-alert-forward@miniserver`，不再以
`honeybury@miniserver` 作为 live target。9093 local forward 允许，interactive shell、9090
local forward 和 reverse forward 全部拒绝的正/负权限测试均已 PASS。最终收口仍应核对旧
`honeybury` 临时 key 行不存在；若需要清理，必须按精确 key 匹配删除，不得覆盖该用户其它
`authorized_keys` 记录。

forward installer 必须先于 canary installer 成功。现有 canary installer 会无条件
enable/start 两个 timer；规范上的 production authority 只有在 Phase 5 停止 Mac scheduler 后
才转移。当前 Mac scheduler 已停止，两个阿里云 timer 是唯一日常 external scheduler，且已通过
重启恢复与至少 11 个连续纯云自然分钟；但在邮件目标端门禁通过和正式声明前，这些样本仍只是
preflight，不能算入 `TODO-0017` 正式窗口。不得重复手工 `run`，也不得把 timer 启动时间误记成
Day 0：

```bash
sudo ./deploy/operations/install_external_canary_host_units.sh \
  --environment-file <root-owned-environment-file> \
  --deployment-record <current-deployment-record> \
  --run-now
```

credential 值不得出现在命令参数、shell history 或 systemd unit；installer 只接收受保护文件。
详细安装和 aggregation 规则见
[`external-business-canary-runbook.md`](external-business-canary-runbook.md)。

安装后的 root-owned systemd tunnel 为：

```text
aliyunserver 127.0.0.1:19093
  -> dedicated SSH local forward over Tailscale
  -> miniserver 127.0.0.1:9093
```

`miniserver` 侧账号只能转发到 `127.0.0.1:9093`，不能取得 shell。仓库 unit 固定
`ExitOnForwardFailure=yes`、pinned known_hosts、keepalive、restart-on-failure 和 loopback
bind；不得发布 19093 或 9093。最终 synthetic drill
`todo0017-aliyun-final-20260907T014854Z` 的 firing/resolved 分别于
`2026-09-07T01:48:54Z`、`2026-09-07T01:49:06Z` 提交，email notification 总数从 23 增至
25，failed 总数保持 3。用户于 `2026-09-07T02:14:12Z` 确认目标端 FIRING `Message-Id`
`<1788745742320233140.8084824043311999187@e5fc8f56f7e9>` 和 RESOLVED `Message-Id`
`<1788745802303693894.9284426330481812095@e5fc8f56f7e9>`。最终 create-only attestation
SHA-256 为 `7f25b5f568be428eea44a653ff0ee2348ce2315ded997f5bb7acc6062e68ccf9`；它在
`miniserver` 与 `aliyunserver` 的相同 raw evidence 路径以 `root:root 0640` 保存，schema、
preflight 与 delivery 均 PASS：

```text
/var/lib/boost-gateway-evidence/observability/raw/7f25b5f568be428eea44a653ff0ee2348ce2315ded997f5bb7acc6062e68ccf9-todo0017-aliyun-final-delivery-attestation.json
```

`miniserver` canonical 在先把旧 SHA-256 前缀 `545635…` 快照 create-only 归档到 raw evidence
后原子刷新为 `7f25b5f…`；标准 preflight PASS，新 preflight summary SHA-256 为
`d8a208a83833ea30cc142df2c8c201f5ceba0fe2753280566ccd8ef14f1aed23`，旧 summary
SHA-256 前缀 `48223…` 已 raw create-only 归档。首版 attestation SHA-256 前缀 `a769…` 因包含
不必要的受保护环境文件派生哈希而被最终版明确 supersede；两机保留首版供审计但不把它作为
最终证据，且没有 secret 正文泄露。

## Phase 4：影子验证

Mac scheduler 已停止。切换前的影子验证必须避免两个 host 在同一自然分钟使用同一 synthetic
identity，也不得把结果写进任一正式窗口。本次执行状态如下：

1. **已通过**：staged canary `validate`、SDK 4.2.0、endpoint
   `tcp://100.65.71.117:9201` 和独立 canary host identity 均已验证；样本仍标为 preflight。
2. **已通过**：维护区间的 canary full-flow 与 `miniserver` 当前 candidate/production identity
   一致，独立 evidence root 未并入 Mac 或新 Day 0。
3. **已通过**：tunnel 在重启后自动恢复；最终 firing/resolved 的机器侧 notification 计数
   23→25 且 failed 保持 3，两条 destination-side `Message-Id` 已由用户确认并写入两机
   create-only attestation `7f25b5f…`；标准 preflight PASS。
4. **已通过**：新 vault 的四份 upload/readback receipt 及两份独立 known-good 均 PASS；第三份
   `todo0012-scheduled-20260907T012921Z-0ac0e17c` 在共锁/容量修复重部署后形成，第四份
   `todo0012-scheduled-20260907T022120Z-e2fa52e1` 是迁移后首个自然 timer run，再次证明
   自动路径。
5. **已通过**：production evidence package `todo0017-aliyun-cutover-20260906T205500Z` 已在
   阿里云完成 checksum-bound verify，create-only receipt 已返回 `miniserver` 受保护 raw
   evidence。package SHA-256 为
   `58665c7147dfd9a5ad92dd19f3e3c4c6d78048d0b3f3a040c742fe0795c3660e`，manifest SHA-256
   为 `55c595631d7eaf31caf2eeb8ee92f93a463581900d9819f5db641df8890f84df`，entry count 160、
   verified objects 161、logical total `84,258,167` bytes，
   `overall_pass/off_host_copy_verified/create_only=true`；原始包未改写。云端 receipt 的
   `verified_at` 为 `2026-09-06T21:01:53Z`，SHA-256 为
   `caa3b5365bf3b5af0a52ac5c07fc5497cc67bbff9991b234e6f5bca856430c4d`，并已 create-only
   回传到 `miniserver`：
   `/var/lib/boost-gateway-evidence/observability/raw/caa3b5365bf3b5af0a52ac5c07fc5497cc67bbff9991b234e6f5bca856430c4d-todo0017-aliyun-cutover-20260906T205500Z-verification-receipt.json`。
6. **已通过**：OOM 后对 `aliyunserver` 执行正常 reboot，当前 boot ID
   `3c82a323-4bd1-4804-9bc9-842ed8b4458f`；Tailscale、firewall、tunnel、vault receiver、
   canary timers、retention timer 和持久日志均在无人交互时恢复，failed unit 返回零。
7. **已通过但仅属预检**：重启后的 `20:49Z`、`20:50Z`、`20:51Z` 三个连续自然分钟
   run/watchdog 均 PASS；它们不计入 Day 0。
8. **已通过**：Mac 停止后至少 11 个连续纯云自然分钟 run/watchdog PASS 且无 incident；
   `2026-09-07 09:27 CST` 前的失败已确认是两个 host 使用相同 synthetic identity 的并发竞态，
   不是 production runtime、endpoint 或业务异常。

所有 summary 必须为 create-only 并记录 SHA-256。影子样本只证明新主机可用，不进入新的
`TODO-0017` 正式窗口。

## Phase 5：原子切换

原设计把全部动作放在一个自然 UTC 分钟边界完成；实际执行为降低 backup 数据风险，先完成
独立的 vault/source cutover 和验证，再停止 Mac scheduler 并固定最终 archive。因此数据面切换
已完成，但这仍不是 `TODO-0017` Day 0。逐项状态为：

1. **已完成**：最后一个 Mac sample SHA-256 为
   `8fd648193380676aa1911e910f416be8e4548d95a8e520953349c27b035569ac`；canary
   run/watchdog/forward 于 `2026-09-07T01:32:08Z` 停止，keep-awake 于
   `2026-09-07T01:46:19Z` 停止，四个 LaunchAgent disabled/unloaded，plist/config 保留。
2. **已完成但仅属预检**：专用 forward installer 与 canary installer PASS，阿里云 run/watchdog
   timers enabled/active，受控重启后三个自然分钟 PASS；不得再次手工执行 `run`。
3. **已完成**：`miniserver` backup timer 在受控区间原子替换 remote host、new vault identity
   digest、recipient、dedicated key 和 known_hosts，验证后重新 enabled/active。
4. **已完成**：四个 backup ID 的 encrypted upload、remote readback、receipt 和新 vault
   identity 全部 PASS，其中前两份支撑独立恢复/known-good，第三份验证修复后的写入路径，
   第四份是迁移后的首个自然 scheduled backup；Redis profile、active volume 和 production
   Compose 未改变。
5. **已完成**：两个不同 restore ID 和隔离 target volume 的解密、Redis validation、业务
   full-flow 和 known-good 均 PASS；共锁/容量修复已重部署，修复后第三份 backup PASS；
   policy-bound retention `prune-20260907T014058Z-c5dd7494` PASS，retention timer 已
   enabled/active；随后首个自然 backup timer run 也 PASS。
6. **已完成**：production evidence package 的阿里云 checksum-bound verify、create-only
   receipt 和回传源端 round trip PASS；最终 Mac canary evidence 已同步到
   `/srv/boost-gateway-archive/mac-canary-evidence-final-20260907T014427Z`，40,594 files、
   `70,130,774` bytes，manifest SHA-256
   `38eba44d907b1847d1b984f42f7e76533afeab57436bcbb9defdd756d19d4656`，receipt SHA-256
   `b59cbb1ac3775ffae8afa2a4c1dceb6aa3dae90cddcf925a2df34350c9a21701`，`rsync -nrc`
   为空且云端 root-only。
7. **已完成**：lifecycle `status`/`verify`、observability preflight、SMTP relay、13 个 governed
   production containers、backup status 和至少 11 个纯云自然分钟均 PASS；Alertmanager 的两个
   机器侧发送动作与两条目标端 `Message-Id` attestation 均 PASS。

原有四项正式前置条件为：两条邮件 `Message-Id` 已写入 attestation、evidence package round
trip PASS、Mac scheduler 已停止、最终 canary evidence checksum sync PASS，现已全部满足；本轮
active-vault 共锁/容量修复重部署、第三份 backup、policy-bound retention 复验与迁移后首个
自然 scheduled backup 也已 PASS。
Mac→Aliyun 日常职责迁移正式签收完成。`TODO-0017` 仍保持 open，正式 Day 0 尚未声明；必须
另选签收之后、尚未采样的未来自然 UTC 分钟，并记录 production/canary/vault identity digest、
deployment/config digest、验收 summary digest 和 maintenance disposition。不得追溯使用 backup
cutover、reboot、迁移或 preflight 的分钟。

## 验收门禁

以下项目共同记录迁移与正式 Day 0 的验收边界；其中四项迁移门禁现已全部满足，因此
Mac→Aliyun 日常职责迁移已正式签收，Mac 已退出日常通电职责。列表末尾的 Day 0 是下一阶段
状态。括号中的状态是 2026-09-07 当前快照，不是对 `TODO-0017` 的完成声明，也不代表新
Day 0 已声明：

- **已验证**：`miniserver` deployment、production host identity、governed Compose container set、Redis
  persistence、45 天 Prometheus retention 和 SMTP receiver 均未改变且 verify PASS。
- **已验证**：阿里云无公网 management、monitoring、vault 或 Docker listener；SSH password authentication 关闭，
  UFW 与阿里云安全组边界一致。
- **已验证但仅属预检**：canary 和 watchdog systemd timer enabled/active，Mac 停止后至少
  11 个连续纯云自然分钟 PASS、无 incident。
- **已验证**：tunnel 只监听 `127.0.0.1:19093`，真实 firing/resolved 已在机器侧发送成功；
  两条目标端 `Message-Id` 已由最终 create-only attestation `7f25b5f…` 绑定并通过 preflight。
- **已验证**：backup source key 无 shell/forward/delete 权限；最新 remote receipt 证明 readback，且新 vault
  至少有两份由不同 backup/restore/target volume identity 形成的独立 known-good attestation。
- **已验证**：active vault 的 receiver、known-good attestation 和 retention 共用
  `.vault.lock`，并在写入前/峰值/完成态保持 regular-file logical bytes 不超过十进制
  `20,000,000,000`、filesystem free 不低于 `5,000,000,000`；容量异常 fail closed，且 active
  runner 不会删除 `/srv/boost-gateway-archive` 冷档；修复后第三份 backup PASS。
- **已验证**：当前 identity 以外的历史 receipt 不参与 anchor、keep 或 delete 选择；retention
  `prune-20260907T014058Z-c5dd7494` 以 3 backup / 2 known-good、空删除集、
  `4,063,038,013` logical bytes 和 `28,906,655,744` free bytes PASS，retention timer
  enabled/active；这是 retention run 当时的快照。随后自然 backup 完成后的当前值为 4 backup /
  2 known-good、incoming/trash 均为 0、`5,428,028,032` logical bytes 和
  `27,361,124,352` free bytes，backup timer 下一次为 `2026-09-08 10:21:07 CST`。
- **已验证**：Mac 历史 vault 与阿里云 partial cold archive 均独立于 active retention；Mac
  scheduler stop 和最终 canary evidence checksum sync 均 PASS，Mac 可以断电。
- **已验证**：当前 production evidence package 的阿里云 checksum-bound round trip 与
  create-only receipt 回传。
- **待开始**：新 `TODO-0017` 从所有迁移门禁之后的未来自然 UTC Day 0 开始，不累计任何 Mac
  或影子验证分钟。

## 独立 dead-man 残余

external canary、watchdog 和 tunnel 同驻 `aliyunserver`。仓库实现能在 tunnel/Alertmanager
readiness 失败时把 create-only incident 留在阿里云本地，并且受控 forward 中断/恢复已经
证明该路径；但整台阿里云主机宕机、Tailscale identity 离线或阿里云同时失去出网时，同机
watchdog 无法把“自己消失”报告出去。

当前尚未部署独立的 dead-man consumer。这不否定已经形成的影子 PASS，也不阻止受控切换
backup/canary 数据面，但在完成以下任一方案并演练前，不能宣称监控链路零盲区：

- 由 `miniserver` 或另一独立主机轮询阿里云 freshness/check-in，并经现有 SMTP relay 告警；
- 使用阿里云 CloudMonitor/SLS 或外部 heartbeat 服务检查 `aliyunserver` 的定时 check-in。

验收记录必须把该项标为 pending 或记录显式风险接受、owner 和截止时间；它不能被静默写成
PASS，也不能使用 Mac 持续通电来伪装已经完成迁移。

## 回滚

以下任一情况立即停止切换或触发回滚：

- 阿里云出现公网未授权 listener、credential/identity 泄露或 host key 漂移；
- canary 超过两分钟无有效样本、出现 duplicate/invalid 或持续业务失败；
- tunnel 无法投递 firing/resolved、错误地发布 19093/9093 或失去 host-key pinning；
- backup upload/readback、receipt identity、解密校验、隔离恢复或业务 full-flow 任一失败；
- 不能维持两份独立 known-good，或合法保留集合超过十进制 20,000,000,000 bytes；
- Tailscale/DERP 路径、memory、disk 或 reboot recovery 不满足正式窗口要求。

回滚步骤：

1. 保留所有阿里云失败样本、receipt、incident、journal 摘要和 digest，不删除或覆盖。
2. 停止阿里云 canary timers 和 tunnel；vault 改为只读调查状态，不删除已接收 archive。
3. 在 `miniserver` 停止 backup timer，恢复受保护的 Mac remote host、vault identity digest、
   recipient、dedicated key 和 known_hosts，验证一份新 backup/readback 后再恢复调度。
4. 重新启用 Mac LaunchAgent，观察三个自然分钟和一次 firing/resolved 投递。
5. 重新执行生产 verify、backup、evidence copy 和 canary aggregation，把迁移窗口和新失败窗口
   标记为 superseded。

回滚到 Mac 同样改变 external host identity，不能恢复或续接原 `TODO-0017` 时间。修复后仍需
从新的 Day 0 开始完整 30 天。回滚完成前 Mac 配置和冷档不得删除；阿里云新 vault 也不得
并入 Mac 历史 retention 或以删除失败证据的方式“恢复干净状态”。

## 收尾与长期运行

production backup cutover、两次独立 restore、阿里云受控重启和 production evidence checksum
round trip 已经通过；active-vault 共锁/容量修复、修复后第三份 backup、policy-bound retention、
迁移后首个自然 scheduled backup、Mac scheduler stop、最终 Mac canary evidence checksum sync
和最终邮件投递 attestation 也已
通过。四项迁移门禁全部 PASS，Mac→Aliyun 日常职责迁移正式签收完成；Mac 现在可以关机，其
配置继续作为回滚材料保留，约 43 GB 的历史 vault 按独立冷档管理。`TODO-0017` 仍 open，新
Day 0 尚未声明，必须选择未来自然 UTC 分钟。长期每日至少检查 canary
freshness、tunnel、delivery、最近 verified receipt、active vault logical bytes、filesystem
free、UFW、Tailscale expiry 和 systemd failed units；retention timer 每日执行，另按周复验
evidence package checksum 和 restore-ready inventory。首个完整 weekly
周期仍是迁移后的持续
观察里程碑，但不把 Mac 保持通电作为替代监控机制。

任何 candidate、deployment、production host、canary host、endpoint、SDK、vault identity、
recipient、tunnel target 或关键安全配置变化都必须形成新变更记录，并根据
[`72-hour-production-shakedown-runbook.md`](72-hour-production-shakedown-runbook.md) 和
[`external-business-canary-runbook.md`](external-business-canary-runbook.md) 判断是否再次
supersede 当前窗口。
