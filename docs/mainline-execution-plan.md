# v3.6.7 Linux x64 企业运营收口与云试运行主线

更新时间：2026-10-08

## 阶段状态

`TODO-0007` 至 `TODO-0019` 已结束。最终窗口的原始自动聚合保持 FAIL，用户批准的家庭
网络边界下软件结果为 43,154/43,154 PASS；完整差异和证据哈希见
[企业运营就绪结论](enterprise-operations-readiness-20261008.md)。`TODO-0018` 最终审计已完成，
当前由 `TODO-0020` 至 `TODO-0023` 推进云服务器 Linux x64 单节点试运行，不把本轮结论扩大为
HA、跨区 DR 或任意容量声明。

## 目标

保持 v3.6.7 Linux x64 不可变资产与已经证明的单节点运营能力，先完成两个月计划的最终
审计，再把同一治理模型迁移到 Ubuntu 24.04 x64 云目标。云阶段必须重新准入主机、网络、
容量、恢复和安全边界；家庭环境结果只作为软件与运营能力输入。

完整 SLI/SLO、RTO/RPO 和 Day 0 规则见
[单节点运营计划](single-node-enterprise-validation-plan.md)。版本化执行状态由
`TODO-0007` 至 `TODO-0018` 和对应 GitHub Issues 管理。

## 当前优先级

| 优先级 | 工作项 | 完成标准 |
|---|---|---|
| P0 | 云目标准入 | IaC、Ubuntu x64、持久盘、时钟、防火墙、私网管理和 secret 边界通过 |
| P0 | 安全部署与切流 | 不在服务器编译；TLS/mTLS 或私网入口；full-flow、告警、回滚通过 |
| P0 | 云备份和恢复 | 不同故障域副本、真实 restore、host replacement 与 rollback 达到 RTO/RPO |
| P1 | 云端容量与试运行 | 选定实例形成容量边界，连续 7 天低流量试运行并记录全部云端 incident |
| P2 | HA/DR 架构决策 | 单独评审多节点、跨可用区和区域灾备，不由单节点证据推导 |

## 执行顺序

1. **冻结云目标合同**：确定区域、实例、磁盘、网络、DNS/TLS、secret、RTO/RPO 和回退条件。
2. **准入云主机**：通过 IaC 与 host policy 建立 Ubuntu 24.04 x64 环境，禁止目标机源码构建。
3. **部署并验证**：使用 immutable release 和治理 lifecycle，完成 SDK full-flow、metrics、
   Alertmanager、external canary、backup 和 evidence round trip。
4. **演练恢复**：执行容器、Redis、主机替换、备份恢复和 release rollback，保留当前
   `miniserver` 作为切流前回退环境。
5. **容量与灰度**：在选定 SKU 上测量容量边界，执行 7 天低流量试运行后再作正式切流决策。
6. **评审 HA/DR**：依据业务 SLO 决定是否进入多节点或跨区设计，不把它混入单节点试运行。

## 已完成基线

- v3.6.7 已作为新的 Linux x64 release 发布。它保持 SDK 4.2.1 和默认 TCP/五后端生产链路
  不变，纳入 v3.6.6 后合入的 MPSC mailbox、实例生命周期、Login session 回收、shutdown
  race、Battle/ECS 热路径和 SMTP relay 修复。annotated tag 固定到
  `db0f905d0421b2052b9de7f49d9bf71787915e23`；main 演练 `31616669960`、正式 Release
  `31617730727` 和独立 aoi Linux x64 published-asset verification `31618651955` attempt 2
  全部通过。runtime archive SHA-256 是
  `fb5f6bfb2626c15a5cd31c7bdd8d06a963192b09132d55e0a387250bdf92fbd0`；生产
  deployment 已通过受控 production upgrade 固定为
  `v3.6.7-fb5f6bfb2626-fa8b69b36dec`，配置 SHA-256 为
  `0692efc4119bac78469672b6fee061fe0dfc7ad68265765da8b36b6e10399777`。
- v3.6.6 annotated tag 固定到
  `d0db2cfd2efaffca55522a58402a48015b39d091`；main 演练 `31019859848`、正式
  Release `31020678952` 和独立 aoi Linux x64 published-asset verification
  `31021854876` 全部通过。runtime archive SHA-256 是
  `17d88d752931fb57a07fb1c0b28517ad326bbcb69c3c3626e10007e7e544ac7d`。该版本未进入
  miniserver；tag 后的运行时正确性修复使生产候选前移并最终激活为 v3.6.7。
- v3.6.5 annotated tag 固定到 governed main commit
  `94f0c5d12d29839bed1598c17f661550c28d84f0`；Release run `30708242109` 和独立
  Linux x64 published-asset verification run `30708591962` 全部通过。
- v3.6.5 按 Linux x64-only patch manifest 发布 runtime、SDK 4.2.1、symbols、SPDX、
  provenance、attestation 和 checksum。v3.6.2 的 Linux ARM64/macOS ARM64 资产保持历史
  支持边界，不进入 v3.6.5 manifest。
- `miniserver` 未编译源码；历史受控 upgrade transaction
  `20260801T193531-upgrade-242675750f37` PASS，current 为
  `v3.6.5-b6d0c8554223-8a1afcfd58dd`。2026-08-31 的 v3.6.7 upgrade、rollback 和
  upgrade-back 均通过；current 为 `v3.6.7-fb5f6bfb2626-fa8b69b36dec`，previous 为
  `v3.6.5-b6d0c8554223-8a1afcfd58dd`。
- Mac 外部 canary 的历史 v3.6.5 诊断窗口是
  `[2026-08-01T19:38:00Z, 2026-08-04T19:38:00Z)`；固定结束聚合以 4,320/4,320
  成功、100% coverage 和 inclusive availability 通过，作为 `TODO-0013` 收口证据。
- 同一窗口确认 v3.6.5 Battle RSS 约以 0.48–0.50 MiB/h 增长，因此拒绝其作为
  `TODO-0016` Day 0。PR #79 的资源释放修复已在 aoi 通过完整 CI 和 sanitizer 专项，
  v3.6.6 随后已发布并复验，但因 tag 后运行时正确性修复未被选择为正式预演候选；
  v3.6.7 接管候选冻结。
- `TODO-0011` 的 W32、W33、W35 周报均以 `coverage_complete=true`、`gap_count=0` 自然通过。
  W33 收口投递演练随后真实发现代理超时和 relay IP TLS 主机名校验缺陷；失败、修复和目标端
  firing/resolved 回执已形成 create-only incident。修复后的最终通知配置在
  `2026-08-17T11:20:49Z` 激活，因此 W33 只保留为历史 metrics/ledger PASS，W34 也不是完整
  冻结周。正式 closure 使用 W35 `[2026-08-24T00:00:00Z, 2026-08-31T00:00:00Z)`；final
  ledger、最新邮件回执和两阶段异机 package 复验已经完成，`TODO-0011` 已关闭。
- v3.6.7 上的 gateway、单 backend、网络/backend outage、Redis、rollback/upgrade-back 和
  host reboot 六项恢复演练均通过。正式 72 小时半开窗口
  `[2026-08-31T19:45:00Z, 2026-09-03T19:45:00Z)` 以 4,320/4,320 canary、100% coverage/
  availability、零 gap/invalid/duplicate/restart/OOM 通过，`TODO-0016` 已关闭。最终异机包
  SHA-256 为 `d62e368bd1457588e9dfb6c1248f0fecb7878916d49a02a5c04dabf5f0940bb0`。
- `TODO-0017` 最初声明的
  `[2026-09-05T10:30:00Z, 2026-10-05T10:30:00Z)` 已 supersede：主机重启时 SMTP relay
  在 Docker bridge 恢复前绑定失败，并从 `2026-08-31T19:30:25Z` 保持 failed，说明原 Day 0
  的告警准入条件并未成立。事件
  `smtp-multi-client-recovery-20260905T215241Z` 已记录 FreeBind、多 bridge 受限 relay、CWA
  STARTTLS/实际投递和 Alertmanager firing/resolved 恢复验证。受治理修复和 Mac 至阿里云的
  external canary、告警转发、异机备份及 evidence 职责迁移已于 `2026-09-07` 完成，Mac 不再需要
  为日常监控保持通电。controller 已对齐到 clean
  `801fb5f37927c0622c038185493d8cff3dc31163` 并通过精确 v3.6.7 bridge verify；Healthchecks.io
  独立 dead-man 随后完成正式部署、missing-heartbeat Down→Up 演练和 create-only
  attestation。替代窗口 `[2026-09-08T03:09:00Z, 2026-10-08T03:09:00Z)` 已结束：原始聚合
  因 44 个缺失分钟和 2 个失败分钟保持 FAIL；用户批准的家庭网络边界下软件结果为
  43,154/43,154 PASS。主机、lifecycle、告警、备份、retention 和阿里云异机包验证均 PASS，
  supplemental final record 保持 `formal_30_day_claim=false`。
- v3.6.2 三平台 Release/R0、原生基线、容量/R4 和 2h 能力证据仍按其历史候选 SHA 和
  runner 边界使用，不能替代 v3.6.7 Linux x64 生产证据。
- Conan 2.8.1、平台 profile/lockfile、SBOM semantic gate、debug-symbol/dSYM verifier、
  SDK clean consumer 和 published-asset verifier 已进入治理链。
- 五项 v3.6 ADR 已接受，P0-P6 仓库内实现完成。仓库内实现不代表默认激活或发布资产已经交付；
  每项能力仍服从其 activation、migration 和 rollback 条件。

逐 run 历史已迁入
[v3.6 实现状态归档](archive/releases/v3.6-implementation-status.md)。

## 证据约束

- summary 必须记录 candidate/checkout SHA、workflow/run、runner、平台、构建配置和 Conan
  lockfile；引用外部 artifact 时同时记录来源 run 和 digest。
- 30 天时长、canary 和 metrics coverage 必须来自连续时间线，不合并中断片段。
- checkpoint 只保留诊断事实；取消或中断结果必须 `overall_pass=false`。
- 性能数据记录 service/loadgen CPU set、实际 lifecycle、before/after 资源快照和 workload
  identity；配置请求率上限不是客户端数或生产容量。
- 三个平台的 binary、container、R5、性能、SDK 和 symbol 证据不可互相替代。

## 当前边界

- 当前目标是单节点运营闭环，不声明多节点 HA、任意规模容量或任意云平台支持。
- gRPC 继续保持 `experimental_only` / `defer_default_transport`。
- Raft protobuf writer 只允许显式配置且全 peer capability 成立；能力撤销时回落 legacy
  writer。
- PyPI/NuGet.org trusted publishing 与 Apple notarization 是独立工作，不因 GitHub
  Release 完成而自动解除。
- 不移动 v3.6.2 tag，不覆盖同名资产，不把发布后的代码或文档改绑到已发布 SHA。
- demo 只验证框架 SPI，不把坦克大战等业务规则写入公共框架或 SDK。

## 阶段退出

本阶段以 TODO-0018 最终报告、事实源一致、遗留风险和下一阶段任务全部可复核为退出条件。
原始 30 天门禁结果不会因阶段退出而改写；云阶段只按受控单节点试运行准入。
