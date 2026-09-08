# 当前项目事实源

更新时间：2026-09-07

本文档只记录当前仍成立的实现、发布和规划事实。历史候选、已关闭清单和逐 run 交付记录
位于 [`docs/archive/`](archive/README.md)，不再混入当前执行优先级。

## 当前结论

- 仓库当前发布版本是 v3.6.7 / SDK 4.2.1，已按 Linux x64-only patch manifest 发布并
  独立复验；`miniserver` 已通过受控 upgrade 激活不可变 deployment
  `v3.6.7-fb5f6bfb2626-fa8b69b36dec`，阿里云外部 canary 使用线协议兼容的 SDK 4.2.0。
- v3.6.2 三平台 runtime、SDK 4.2.0、symbols/dSYM 和供应链资产保持不可变历史事实；
  Linux ARM64 与 macOS ARM64 不进入 v3.6.7 新资产集合。
- 阿里云 external canary 使用线协议兼容的 SDK 4.2.0 访问 v3.6.7 服务端；历史 Mac
  样本也使用 4.2.0。canary deployment identity 必须记录实际 SDK 版本，不得伪装为 4.2.1。
- 当前主线不是继续增加 demo 或协议表面积，而是执行 Ubuntu 24.04 x64 单节点自动
  部署、观测、追溯、备份恢复、72 小时预演和 30 天不可变运行计划。
- external canary、Alertmanager tunnel、异机 backup vault 和 evidence archive 的日常职责已从
  Mac 受控切到 Tailscale 节点 `aliyunserver`，四项迁移门禁全部 PASS 并已正式签收；这不迁移
  `miniserver` 生产栈，也不代表新的 `TODO-0017` Day 0 已开始。

发布过程和逐平台 run 记录已归档到
[v3.6 实现状态](archive/releases/v3.6-implementation-status.md)。当前任务和完成定义见
[主线执行计划](mainline-execution-plan.md)与
[单节点运营计划](single-node-enterprise-validation-plan.md)。

项目待办以 `docs/todos/tasks.json` 为版本化事实源，`docs/todos/BOARD.md` 提供生成视图。
`TODO-0008` 已在 Ubuntu 24.04 x64 目标主机真实完成：baseline、SMART/thermal、端口与
权限准入通过；boot ID 从 `347f0099-eaa5-4f0e-a0e8-7a93803e0f6d` 变为
`3872f22c-1b67-4d29-8c04-280a58619c6e` 后，systemd 在无交互登录条件下恢复 Compose、
11 个 healthy 容器、监控和端口拓扑，正式 `verify-reboot` summary 为 PASS。

`TODO-0009` 也已在同一目标机真实完成：v3.6.2 的 tag/commit/checksum/SLSA/SPDX/ELF
校验、固定 Ubuntu digest 的六个 runtime-only image、不可变项目 image ID 的生产 Compose
和 release SDK full-flow 均通过，全程没有源码构建或公共 Conan 访问。目标机证据位于
`/var/lib/boost-gateway-evidence/release/`。

`TODO-0010` 已在同一目标机真实完成。历史 v3.6.2/v3.6.0 演练中，同 release 重复
install/deploy 返回相同 identity 和
`idempotent=true`，`todo0010-20260725T152655Z` data/backup/evidence 哨兵、Redis key 和
Compose volume 清单均保留。前向 upgrade 和真实 rollback 分别在约 55–57 秒内完成，rollback
记录恢复了 runtime asset、image environment 和 configuration digest。受控 Prometheus pause
使 transaction `20260725T161238-upgrade-257dce6b1f94` 的候选验证真实失败，独立 recovery
summary 随后 PASS 并自动恢复 v3.6.0；transaction
`20260725T163615-upgrade-db4ab2d7639d` 将运行版本恢复到 v3.6.2。2026-08-01 的正式
v3.6.5 Release 随后通过同一治理入口完成 install 和 upgrade：transaction
`20260801T193531-upgrade-242675750f37` 在 65.254 秒内 PASS，当时 `current` 是
`v3.6.5-b6d0c8554223-8a1afcfd58dd`，`previous` 是
`v3.6.2-faf2d03ff1b9-8a1afcfd58dd`，受保护状态未改变。目标机证据位于
`/var/lib/boost-gateway/deployment-transactions/`。

v3.6.6 annotated tag 已固定到
`d0db2cfd2efaffca55522a58402a48015b39d091`。受治理 main 演练 `31019859848`、正式
Release `31020678952` 和独立 aoi Linux x64 published-asset verification
`31021854876` 均 PASS；发布包含 11 个 Linux x64-only 治理资产，runtime archive
SHA-256 是 `17d88d752931fb57a07fb1c0b28517ad326bbcb69c3c3626e10007e7e544ac7d`。
该事实只关闭发布和异机复验；v3.6.6 从未进入生产，当时仍运行上述 v3.6.5 deployment，
随后由 v3.6.7 直接接管。

v3.6.6 发布后，主线继续合入 cross-core MPSC mailbox、实例生命周期并发、Login session
回收、process/write-behind shutdown race、Battle/ECS 热路径和生产 SMTP relay 修复。这些
变更已由 v3.6.7 新候选承载：annotated tag 固定到
`db0f905d0421b2052b9de7f49d9bf71787915e23`；受治理 main 演练 `31616669960`、正式
Release `31617730727` 和独立 aoi Linux x64 published-asset verification
`31618651955` attempt 2 均 PASS。发布包含 11 个 Linux x64-only 治理资产，runtime archive
SHA-256 是 `fb5f6bfb2626c15a5cd31c7bdd8d06a963192b09132d55e0a387250bdf92fbd0`。
独立复验确认 checksum、归档布局、runtime/symbol/SDK、SPDX 语义、无网络 Ubuntu 24.04
consumer、provenance 和 SBOM attestation。attempt 1 的 GitHub HTTP/2 `GOAWAY` 下载中断
保留在同一 run 历史中，attempt 2 完整重跑通过。生产 deployment 已在 W35 收口后通过
受控 upgrade 固定为 `v3.6.7-fb5f6bfb2626-fa8b69b36dec`。

`TODO-0012` 已于 2026-07-28 完成：production-validation Redis 已实际启用 AOF `everysec` +
RDB，声明并验证不高于 60 秒的 RPO；加密 daily backup 已复制到异机 vault，至少两份独立
backup/restore/target volume 通过 leaderboard submit/top/rank 和 release SDK full-flow。仓库的
example policy、初始 activation decision 和单轮恢复 summary 仍故意保留
`formal_todo0012_claim=false`，用于阻止单个候选或单轮产物越权声明完成；它们不是当前目标机
是否已激活的事实源，最终任务状态以 `docs/todos/tasks.json` 的聚合验收为准。

`TODO-0011` 已完成：生产 Compose 形成 45 天 Prometheus、
node-exporter、cAdvisor、Redis persistence 和 Docker restart-count 指标契约；目标机已经形成
真实 Alertmanager receiver 的 firing/resolved 投递证据、完整 host/container/application/Redis
指标样本和异机 bootstrap 包复验。生产预检继续拒绝默认 Grafana 凭据、占位 receiver、过期或
单边投递声明；ledger 可生成 create-only daily/weekly/incident/final record 及带 `SHA256SUMS` 的
异机包。W31 的历史 gap 已被不可变保留；W32
（`2026-08-03T00:00:00Z` 至 `2026-08-10T00:00:00Z`）周报已在
`2026-08-10T00:45:00Z` 以 `coverage_complete=true`、`gap_count=0` 自然通过。生产 SMTP
relay 随后于 `2026-08-10T03:13:52Z` 激活并改变最终通知配置，因此 W32 只保留为通过的历史
观测证据。W33（`2026-08-10T00:00:00Z` 至 `2026-08-17T00:00:00Z`）周报也在
`2026-08-17T00:45:00Z` 以 `coverage_complete=true`、`gap_count=0` 自然通过，但收口投递
演练真实发现通用代理节点超时及 relay IP 的 TLS 主机名校验错误。两次失败和恢复已写入
`smtp-relay-delivery-20260817T111202Z` incident；`tls_config.server_name` 修复于
`2026-08-17T11:20:49Z` 激活，新的 firing/resolved 目标端回执和生产 preflight 随后通过。
该变更使 W33 继续保留为历史 metrics/ledger PASS，但不得关闭任务；W34 周一开始后的前 11
小时也不是最终冻结配置。最早完整自然周期改为 W35
（`2026-08-24T00:00:00Z` 至 `2026-08-31T00:00:00Z`），周报计划在
`2026-08-31T00:45:00Z` 自然通过。最终 ledger、最新 firing/resolved 目标端回执以及 candidate/
final 两阶段 evidence package 均已在 Mac 异机校验；final package SHA-256 为
`6e5a76dd7d07b4ca4dca78a26073c7eeae6d694fad54649b98acdd37b8d611ea`。

`TODO-0013` 的 v3.6.2 诊断窗口完整记录 4,320 个分钟，但五次真实 gateway-to-backend
timeout 使结果为 FAIL；原始样本和 incident 均保留。#73 修复进入 v3.6.5 后，Mac 外部主机
通过 Tailscale 真实路径完成权威窗口
`[2026-08-01T19:38:00Z, 2026-08-04T19:38:00Z)`：4,320 个预期分钟全部记录且全部
成功，coverage、recorded success、inclusive availability 均为 100%，没有 gap、duplicate、
invalid sample 或 candidate/endpoint 漂移。固定结束聚合
`72h-20260804T1938Z.json` 的 SHA-256 是
`89071385fba47501ada771a2e02109b30f1228fdb036151ae70fd1fae17ff63f`，绑定 v3.6.5
deployment、commit `94f0c5d12d29839bed1598c17f661550c28d84f0` 和 runtime digest
`b6d0c8554223e78d81c9da314256d31883b91fe7aacd8a7f9504840db524c487`。该证据关闭
`TODO-0013` 的外部 canary 能力任务，但不是 `TODO-0016` Day 0；v3.6.5 Battle RSS
线性增长仍要求新的不可变 runtime 候选和独立正式窗口。

该 v3.6.5 诊断窗口同时确认 Battle backend working set 约以 0.48–0.50 MiB/h 线性增长。
Issue #78 的 RCA 定位到完成 battle 的 runtime/per-battle/replay 状态没有完整释放；修复已由
PR #79 合入主线，并在 aoi Linux x64 runner 通过完整 CI 与 ASan/UBSan/LSan 资源专项。
v3.6.5 因此不得成为 `TODO-0016` Day 0。v3.6.6 已完成 Release 和独立资产复验，但未在
tag 后运行时正确性修复完成前进入生产；v3.6.7 已完成发布、独立资产复验和受控生产升级。
六项 pre-Day-0 恢复演练均通过，异机演练包 SHA-256 为
`aa05158ccaff209f9cedcee04ec994d107285ec9de8138164d85d662fb95ef72`。正式 72 小时窗口
`[2026-08-31T19:45:00Z, 2026-09-03T19:45:00Z)` 以 4,320/4,320 样本、100% coverage/
availability、零 gap/invalid/duplicate/restart/OOM 通过；final package SHA-256 为
`d62e368bd1457588e9dfb6c1248f0fecb7878916d49a02a5c04dabf5f0940bb0`，Mac 异机复验
91/91 文件通过，`TODO-0016` 已完成。最初声明的独立 30 天窗口
`[2026-09-05T10:30:00Z, 2026-10-05T10:30:00Z)` 已 supersede，因为 SMTP relay 自窗口
开始前的主机重启起一直启动失败，Alertmanager 准入不成立。事件
`smtp-multi-client-recovery-20260905T215241Z` 已记录恢复结果；SMTP 受治理修复、外部运营职责
迁移、自然分钟样本复验和 controller 对齐均已完成。新的 30 天窗口仍须等待独立 dead-man
正式部署与演练、治理化 fixed-end finalizer 全部通过后重新声明。截至 2026-09-07，新的
`TODO-0017` Day 0 尚未开始；阿里云 preflight 与 Mac 历史分钟都不能追溯计入。任何 runtime、关键配置、
deployment、endpoint 或 host identity 变化仍会重置 Day 0，旧窗口时间不会累计。

## 阿里云异机迁移状态

当前 `miniserver` 仍是唯一生产服务主机，Boost Gateway、Redis、Prometheus、Grafana、
Alertmanager、SMTP relay、生产 backup producer 与本机 evidence timer 均保留原位。
`aliyunserver` 仅承载 external canary/watchdog、loopback Alertmanager SSH forward、加密
backup vault 与异机 evidence archive；完整边界和切换步骤见
[Mac 外部运营职责迁移到阿里云 Runbook](deployment/aliyun-offhost-migration-runbook.md)。

截至 2026-09-07 的已验证事实（日常职责迁移已正式签收，Day 0 尚未声明）：

- 阿里云 Ubuntu 24.04 x86-64 主机已完成 key-only SSH、UFW/Tailscale、持久 journal、NTP、
  swap、Docker/Compose 与 age 基线；至 `miniserver` 的 Tailscale 路径为 direct。
- live forward 已使用专用 `boost-gateway-alert-forward@miniserver` target account，只监听
  `127.0.0.1:19093`。允许的 9093 local forward 已通过；interactive shell、9090 local
  forward 和 reverse forward 均已被拒绝，未继续依赖临时 `honeybury` target。
- `miniserver` backup source 已原子切换到阿里云新 vault identity。两份不同 backup
  `todo0012-scheduled-20260906T200739Z-87a4e10e` 和
  `todo0012-scheduled-20260906T201746Z-7959d44d` 的 upload/readback 均 PASS；两个不同
  restore、业务 full-flow 和 known-good 也独立 PASS。restore summary SHA-256 分别为
  `9dcf73867f2290cf2e5d27f9f25e306d8ae18c55d19de025a86d0cc43f478e24`、
  `07ad3e88592db35af5d7319dd8d143f3255e9e4d5f7fa25f920456497d0b6f39`，business summary
  SHA-256 分别为
  `de20b0a7a08a34e8d8a590954005ee3a7570f78eb63d666611d4aecde04a57bf`、
  `253a41eaded4fa44b2afb7a27e56b776b09c16658e285792eb298aa796bdc90d`。两个恢复目标卷均与
  生产卷隔离，生产卷 identity 未改变。两份 known-good attestation 在本轮锁/容量竞态修复部署
  前形成，但当时 active vault 仅约 2.70 GB，远低于 20 GB 上限且保有约 30.3 GB filesystem
  free；其恢复和业务结果仍有效。修复部署后的第三份 backup
  `todo0012-scheduled-20260907T012921Z-0ac0e17c` 也已 PASS，archive 为
  `1,364,936,232` bytes，source summary SHA-256 为
  `662b17f154cb8ffb3406c9a2a5ef5bed21a23610b370ba706519267aaefedfbc`，remote receipt
  SHA-256 为 `7a163b0f36004ab2db23124b9d77c210955f4038ec1418560ea839c00dcc7539`。
- 迁移后的首个自然 scheduled backup 由 `miniserver` timer 在
  `2026-09-07 10:21:20–10:30:34 CST` 自动运行 PASS，并非手动触发。backup ID 为
  `todo0012-scheduled-20260907T022120Z-e2fa52e1`，archive `1,364,966,952` bytes、SHA-256
  `151cefbe45a131f6cb7e6edde5f152d23cf35ee593314310f066d1eed8d5a966`，manifest SHA-256
  `81c58c5cb443f6d9873b915115068227372615040f066dc28de1606ae156363a`，source summary
  SHA-256 `369646371dbd935e3e548e3a4c425f233b426e521bb6f46e41107c4e801159f5`，receipt SHA-256
  `735312aa35132a9ac0f48bd56d383b749bed67fc668a57aca4497dc97e54283e`。
  `overall_pass/create_only/off_host_copy_verified/remote_readback_verified=true`，source/cloud
  receipt byte-identical，再次证明迁移后的自动生成、上传、回读和回执链路。backup timer 下一次
  为 `2026-09-08 10:21:07 CST`。
- 对阿里云 active vault 的受治理布局，仓库当前实现已让 receiver、store/receipt、known-good
  attestation 和 retention 共用同一个 root-controlled `.vault.lock`；store 与 attestation 的预检、
  峰值/完成态强制十进制 `20,000,000,000` logical-byte cap 与 `5,000,000,000` free-floor，
  attestation 还按预留 size 流式绑定源文件以拒绝增长竞态。`.vault.lock` 存在时必须走安全
  layout、共锁和容量校验，异常 entry 必须 fail closed，CLI/SSH 均没有降级参数；兼容
  `remote-prune` 也会拒绝任何带固定 lock entry 的 vault，要求改用 policy-bound service。只有
  完全没有该 lock 的 legacy Mac vault 保持旧 receiver/store/receipt/attestation/local-prune 流程，
  约 43 GB 的 Mac 冷档不套用也不宣称具备新锁或新容量门禁。修复已在阿里云重部署，第三份
  backup 验证了修复后的写入路径；随后 policy-bound retention service 以 deletion ID
  `prune-20260907T014058Z-c5dd7494` PASS，保留 3 份 backup、2 份 known-good，
  `deleted_backup_ids=[]`，该 retention run 完成当时 logical bytes 为 `4,063,038,013`、filesystem free 为
  `28,906,655,744`。intent SHA-256 为
  `93dabc2c2bd7a0e525109b50c1075c50f3e84c96242b0aa81b611862bd23f132`，completion
  SHA-256 为 `5de3f2c8559c72d3b98e56e7482aa7a10230e641a814a992e9ace8818c0a32c4`。timer 已重新
  enabled/active，下一次计划运行时间为 `2026-09-07 12:04:53 CST`。上述 3 份/4.063 GB 是
  retention 执行时的历史快照；首个自然 scheduled backup 完成后，active vault 当前为 4 份
  backup、2 份 known-good、`.incoming=0`、`.trash=0`，logical bytes `5,428,028,032`、
  filesystem free `27,361,124,352`。
- 运维检查中错误地用 Python `read_bytes()` 一次性读取约 1.349 GB archive，造成阿里云全局
  OOM 和 SSH 暂时不可用；该动作不是 production service、receiver 或 retention 发起，也没有
  删除、覆盖或损坏 vault 数据。实例通过正常 reboot 恢复，当前 boot ID 为
  `3c82a323-4bd1-4804-9bc9-842ed8b4458f`，重启后 Tailscale、forward、canary timers、
  retention timer 与 Docker 均健康，failed unit 为零。主机已安装
  `/etc/sysctl.d/99-z-boost-gateway-memory.conf`，固定 `vm.swappiness=10`；此后所有大型
  archive、digest 和检查操作必须使用流式 I/O，禁止将整文件读入内存。
- 历史受控重启后的 `20:49Z`、`20:50Z`、`20:51Z` 三个连续自然分钟 canary 均 PASS；Mac
  scheduler 停止后，阿里云又连续至少 11 个纯云自然分钟 PASS 且没有 incident。`2026-09-07
  09:27 CST` 前的失败来自 Mac 与阿里云使用相同 synthetic identity 的并发竞态，不是生产
  runtime、endpoint 或业务异常。这些结果仍属于 migration preflight，不是正式 30 天窗口。
- 阿里云迁移验收时，`miniserver` 的 lifecycle `status`/`verify`、observability preflight、
  SMTP relay 和 13 个 governed production containers 均 PASS；这是当时 controller 下的历史
  事实。合并后的 controller `40a1ef147ff19707821b0825e17895a1baeb2938` 首次对齐时，严格
  verifier 发现不可变 v3.6.7 Compose 只缺显式 `boost-net` IPAM，而实际 Docker network 仍精确为
  `172.18.0.0/16` / `172.18.0.1`，其余验证全部 PASS；该次切换按 fail-closed 条件原子回滚且没有
  改变 production。随后 current-only 精确兼容 verifier 经 PR #107 独立审批、required CI 和合并，
  `/home/honeybury/boost-gateway-controller` 已原子对齐到 clean
  `801fb5f37927c0622c038185493d8cff3dc31163`。真实 bridge verify transaction
  `20260907T132949-verify-9218fb5a10e6` PASS，summary SHA-256 为
  `99d02227449ad208a47a51a03eb2d15d789b67b8323a0eb649d17b3576438bab`；阿里云异机归档
  `/srv/boost-gateway-archive/production-evidence/controller-cutover-801fb5f37927-20260907T132502Z`
  的 result receipt SHA-256 为
  `1fd504724c5de6a274e8dae35baf2c23980e70f338586e0f1e236b2798f6e804`。production
  current/previous、Redis volume identity 和服务仍未改变，临时 sudo 授权已撤销。兼容旗标仍只
  允许验证这一精确 current v3.6.7 deployment，不能进入 install/deploy/upgrade/rollback/recovery。
- 最终 firing/resolved drill `todo0017-aliyun-final-20260907T014854Z` 分别于
  `2026-09-07T01:48:54Z` 和 `2026-09-07T01:49:06Z` 提交，Alertmanager email notification
  计数从 23 增至 25，failed 计数保持 3。用户于 `2026-09-07T02:14:12Z` 确认目标端
  FIRING `Message-Id` 为
  `<1788745742320233140.8084824043311999187@e5fc8f56f7e9>`，RESOLVED `Message-Id` 为
  `<1788745802303693894.9284426330481812095@e5fc8f56f7e9>`。最终 create-only attestation
  SHA-256 为 `7f25b5f568be428eea44a653ff0ee2348ce2315ded997f5bb7acc6062e68ccf9`，在
  `miniserver` 与 `aliyunserver` 同路径
  `/var/lib/boost-gateway-evidence/observability/raw/7f25b5f568be428eea44a653ff0ee2348ce2315ded997f5bb7acc6062e68ccf9-todo0017-aliyun-final-delivery-attestation.json`
  以 `root:root 0640` 保存，schema、preflight 与 delivery 均 PASS。首版 SHA-256 前缀
  `a769…` 因包含不必要的受保护环境文件派生哈希而被最终版明确 supersede；两机保留首版供
  审计但不把它作为最终证据，且没有 secret 正文泄露。
- production evidence package `todo0017-aliyun-cutover-20260906T205500Z` 已在阿里云完成
  checksum-bound verify，并把 create-only receipt 回传到 `miniserver` 受保护 raw evidence。
  package SHA-256 为
  `58665c7147dfd9a5ad92dd19f3e3c4c6d78048d0b3f3a040c742fe0795c3660e`，manifest SHA-256
  为 `55c595631d7eaf31caf2eeb8ee92f93a463581900d9819f5db641df8890f84df`，manifest
  `entry_count=160`、verified objects 为 161，`overall_pass`、`off_host_copy_verified` 和
  `create_only` 均为 true。云端 verified logical total 为 `84,258,167` bytes，receipt 于
  `2026-09-06T21:01:53Z` 生成，SHA-256 为
  `caa3b5365bf3b5af0a52ac5c07fc5497cc67bbff9991b234e6f5bca856430c4d`。
- Mac 约 43 GB、42 份 backup 的原 vault 未移动、未裁剪、未删除。阿里云独立 cold partial
  archive 为 `2,108,033,646` bytes、70 个文件且 checksum 全部通过；其中两份历史 backup
  完整匹配 Mac，第三份传输不完整且不具备恢复资格。旧 age/vault identity 只用于冷档验证，
  不与 active vault 的 receipt 或 retention 混用。
- Mac canary run/watchdog/forward 于 `2026-09-07T01:32:08Z` 停止，keep-awake 于
  `2026-09-07T01:46:19Z` 停止；四个 LaunchAgent 均已 disabled/unloaded，plist 与配置保留
  作为回滚材料。最后一个 Mac sample SHA-256 为
  `8fd648193380676aa1911e910f416be8e4548d95a8e520953349c27b035569ac`。最终 evidence 冷档
  `/srv/boost-gateway-archive/mac-canary-evidence-final-20260907T014427Z` 包含 40,594 个文件、
  `70,130,774` logical bytes；manifest SHA-256 为
  `38eba44d907b1847d1b984f42f7e76533afeab57436bcbb9defdd756d19d4656`，receipt SHA-256 为
  `b59cbb1ac3775ffae8afa2a4c1dceb6aa3dae90cddcf925a2df34350c9a21701`，`rsync -nrc`
  复核为空且云端为 root-only。Mac 的 ARM64 runner、本地 Docker/CWA 与原 vault 均未触碰；
  Mac 现在可以断电。

正式 Day 0 原有四项前置条件固定为：邮件 `Message-Id` attestation、production evidence package
round trip、Mac scheduler stop 和最终 canary evidence checksum sync，现已全部 PASS；active-vault
修复重部署、第三份 backup、policy-bound retention 复验与迁移后首个自然 scheduled backup 也已
通过，且当前无 P0/P1。`miniserver`
canonical 在先把旧 SHA-256 前缀 `545635…` 快照 create-only 归档到 raw evidence 后，已原子刷新
为最终 attestation `7f25b5f…`；标准 preflight PASS，新 preflight summary SHA-256 为
`d8a208a83833ea30cc142df2c8c201f5ceba0fe2753280566ccd8ef14f1aed23`，旧 summary SHA-256
前缀 `48223…` 已 create-only 归档。Mac→Aliyun 日常职责迁移至此正式签收完成，Mac 可以断电。
但 `TODO-0017` 仍保持 open，新的 Day 0 尚未声明；只能选择一个尚未采样的未来自然 UTC 分钟，
不得追溯使用迁移或 preflight 分钟。

另有一项明确保留的监控残余：当前同机 watchdog 能记录 tunnel failure，但无法自报整台
阿里云/Tailscale 消失。处置方案已经固定为 Healthchecks.io 独立 dead-man：自然分钟
watchdog 成功或失败分别触发 provider success/failure，整机、timer 或出网消失则由缺失
heartbeat 触发外部通知；provider email 不经过 `aliyunserver`、Tailscale、生产
Alertmanager/SMTP relay 或 Mac。截至本快照，该 provider check、主机激活和真实
missing-heartbeat `Down`→`Up` 演练均**尚未完成**，两条目标端 `Message-ID` 和 create-only
attestation 也不存在，因此该项保持 pending，不能宣称零监控盲区或准入 Day 0，更不能用 Mac
持续通电替代。

## 默认生产链路

默认生产主链仍是 SDK + TCP gateway + `BackendEnvelope` + Login/Room/Battle/
Matchmaking/Leaderboard 五个 backend，并按部署需要使用 Redis、TLS 和观测组件。

```text
C++ / Python / C# SDK
          |
          | length-prefixed TCP
          v
Gateway :9201 ---- management HTTP :9080
   |---- Login         :9202
   |---- Room          :9302
   |---- Battle        :9303
   |---- Matchmaking   :9304
   `---- Leaderboard   :9305
```

当前默认构建和依赖选项：

| 选项 | 默认值 | 当前边界 |
|---|---|---|
| `BOOST_DEPENDENCY_PROVIDER` | `conan` | 严格使用 Conan 2.8.1 profile/lockfile，不隐式回退 |
| `BOOST_BUILD_RAFT_PROTOBUF` | `ON` | 内部 codec 可用，writer 激活仍受 capability 和回滚门禁控制 |
| `BOOST_BUILD_GRPC` | `OFF` | gRPC 已有 PoC 与专项证据，但不进入默认生产链路 |
| `BOOST_BUILD_SQLITE` | `OFF` | SQLite storage 是显式可选能力 |
| `BOOST_BUILD_TANK_DEMO` | `OFF` | 业务 demo 不进入默认生产构建 |

## 已稳定交付的能力

- Gateway session、Actor runtime、后端路由、熔断、限流、HTTP health/metrics 和配置治理。
- Login、Room、Battle、Matchmaking、Leaderboard 六服务闭环及 SDK full-flow。
- C++ SDK、稳定 C ABI、Python ctypes wrapper、C# P/Invoke wrapper 和 4.2.1 Linux x64 分发资产。
- schema-backed typed contract；五个业务域的 handler 已纳入 typed envelope 治理。
- Redis leaderboard/event store、Raft state/command/wire codec、恢复和 mixed-binary 门禁。
- TLS/mTLS profile、JWT/JWKS 轮换验证、OTel exporter/collector 对账。
- Docker Compose、Kubernetes、Operator、发布包、SBOM、provenance 和符号验证入口。
- Linux x64、Linux ARM64、macOS ARM64 的原生 Conan、运行时和发布消费证据。

P0-P6 的仓库内实现现已完成；具体历史交付见归档状态文档。这句话不表示所有可选能力
都已默认激活：已接受的 ADR 仍分别约束 Raft protobuf writer、公共 package registry、
Apple notarization 和实验 gRPC。对这些能力，默认生产链路和 manifest 阻断状态不变。

## 框架与业务边界

- `include/v2/`、`src/v2/` 承载公共连接、路由、协议、runtime、观测、持久化和 SDK
  支撑，不承载具体游戏规则。
- `include/v3/`、`src/v3/`、`proto/v3/` 是协议、Raft、Redis 和持久化演进层。
- 坦克大战及其它业务样例位于 `demo/games/`。`TankBattlePlugin` 是 SPI 验证实现，
  不属于默认生产 battle 主链。
- legacy raw JSON 只保留在明确的兼容窗口；新增业务消息必须使用 typed/schema contract。
- `BoostAsioDemo` 只作为历史仓库名和兼容标识保留，对外名称统一为 `BoostGateway`。

## 平台与证据边界

| 平台 | 当前状态 | 不能推导的结论 |
|---|---|---|
| Linux x64 | runtime/SDK/symbol 发布和独立复验完成 | 单次本地 smoke 不代表固定 runner 容量 |
| Linux ARM64 | 原生 Release、R0、R4、2h、runtime/SDK/symbol 发布复验完成 | 不能用 x64 package 或镜像代替 ARM64 证据 |
| macOS ARM64 | 原生 Release、R0、R4、2h、runtime/SDK/dSYM 发布复验完成 | 当前发布未声明 Apple notarization |

机器可读平台契约见 [platform-production-boundaries.json](platform-production-boundaries.json)，
runner 当前状态见 [`docs/runner-inventory.md`](runner-inventory.md)。

Mac 专用 ARM64 runner 当前保持 offline，本次阿里云迁移不会把 macOS runner、Mac 本地开发
Docker 或 CWA 书库容器迁到 x86-64 云主机。历史 macOS 发布证据继续有效，但不能据此宣称
当前有可调度的 macOS runner 容量。

性能事实必须绑定 workload、候选 SHA、runner、lockfile、CPU 约束和原始 summary。
历史 2h/8h、capacity 和单变量轴证明对应候选与环境下的行为，不构成任意部署规模的容量
承诺。当前有效测量口径见 [performance-baseline.md](performance-baseline.md)。

## 当前主任务

当前两个月工作由 `TODO-0007` 至 `TODO-0018` 管理，目标是：

1. 已在服务器不编译源码的前提下，以不可变 release asset 完成幂等安装、升级和回滚。
2. 已完成最终 SMTP relay 配置下的 W35 自然 metrics/ledger 周期、`TODO-0011` 收口和
   v3.6.7 独立 72 小时预演；此前声明的正式 30 天窗口已 supersede，新 Day 0 尚未开始。
3. 历史异机备份、Redis/host/runtime 恢复演练已满足 5/10 分钟 RTO 边界；阿里云新
   vault identity 的 source cutover、两份 upload/readback 和两次独立 restore/business/
   known-good 及 evidence round trip 均已通过；attestation lock/容量修复已重部署，第三份
   backup、policy-bound retention、迁移后首个自然 scheduled backup、Mac scheduler stop、最终
   evidence sync 和邮件目标端 `Message-Id` create-only attestation 均已通过，日常职责迁移已
   正式签收。
4. 收口 required checks、review、CODEOWNERS、SECURITY 和 Action SHA pinning。
5. 已关闭 `TODO-0011`/`TODO-0013`，完成独立 72 小时上线预演、阿里云异机迁移验收及
   controller 对齐；独立 dead-man 正式部署与演练、治理化 fixed-end finalizer 完成后，以单一
   tag/SHA/digest 和新的 external host identity 从新 Day 0 连续运行至少 30 天。

30 天验证要求连续时长不少于 `2,592,000s`，availability/canary success 与证据覆盖率
均不低于 99.9%；runtime 变化会重置 Day 0。完整口径见
[single-node-enterprise-validation-plan.md](single-node-enterprise-validation-plan.md)。

## 当前阻断和非目标

- P3 数据恢复与 P4 可观测性仍由
  `scripts/gates/production/verify_data_recovery_gate.py` 和
  `scripts/gates/production/verify_observability_gate.py` 作为当前发布能力验证。
- `admin_service` 仅属于 `legacy-v1 / demo-only` 历史管理面，不进入默认 gate，不能据此
  声明当前 v2 主线提供正式 admin 控制面。

- 不把 v3.6.2 的三平台发布解释为多节点 HA、任意规模容量或所有云环境支持。
- 不因 PoC 完整而把 gRPC 升级为默认传输。
- 不在当前运营主线中扩大业务 demo、公共协议或 SDK 表面积。
- 不把不同 SHA、不同 runner 或不同平台的证据拼接为同一冻结候选。
- PyPI/NuGet.org trusted publishing 和 Apple notarization 仍是独立工作，不由 GitHub
  Release 资产自动解除。
- 性能优化必须由长期指标或 incident 驱动，并保留 RCA、前后基线、回归和回滚方案。

## 当前验证入口

```bash
python3.12 scripts/gates/governance/check_current_docs_install.py
python3.12 scripts/check_mainline_readiness.py
python3.12 scripts/gates/governance/check_config_source_layout.py
python3.12 scripts/gates/transport/check_transport_config_governance.py
python3.12 scripts/gates/governance/check_next_minor_decisions.py
python3.12 scripts/verify_release_candidate.py \
  --skip-release-baseline --soak-profile smoke
```

开发构建和分层测试见 [ONBOARDING.md](ONBOARDING.md)。生产证据入口、前置依赖和
fixed-runner 操作见 [release-governance.md](release-governance.md)与
[fixed-runner-playbook.md](fixed-runner-playbook.md)。
