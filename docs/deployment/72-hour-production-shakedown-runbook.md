# 72 小时生产预演 Runbook

更新时间：2026-08-05

本文档是 `TODO-0016` 在 Ubuntu 24.04 x64 单节点生产主机上的 maintained 执行入口。
它不负责关闭 `TODO-0011` 或 `TODO-0013`，也不把诊断性 canary 时间自动升级为正式
Day 0。机器可读的预声明模板位于
[`../production/production-shakedown-plan-template.json`](../production/production-shakedown-plan-template.json)。

## 当前候选和边界

当前没有完成生产准入的不可变候选。已发布并独立复验的替代候选是：

- tag：`v3.6.6`
- commit：`d0db2cfd2efaffca55522a58402a48015b39d091`
- runtime archive SHA-256：`17d88d752931fb57a07fb1c0b28517ad326bbcb69c3c3626e10007e7e544ac7d`
- Release run：`31020678952`
- published-asset verification run：`31021854876`
- deployment/configuration identity：等待 W32 后受控 upgrade

当前 v3.6.5 生产身份是：

- tag：`v3.6.5`
- commit：`94f0c5d12d29839bed1598c17f661550c28d84f0`
- runtime digest：`b6d0c8554223e78d81c9da314256d31883b91fe7aacd8a7f9504840db524c487`
- deployment：`v3.6.5-b6d0c8554223-8a1afcfd58dd`
- production host identity：`8600b239b110e1e5afc69a8705a366d006563e4d7293a45d9c5e3fbbbfdd3a23`
- external endpoint：`tcp://100.65.71.117:9201`

该身份完成 `TODO-0013` 的 4,320/4,320 外部 canary 窗口，但 Battle working set 约以
0.48–0.50 MiB/h 线性增长，因此已拒绝作为 `TODO-0016` Day 0。v3.6.6 的发布身份已按
真实证据回填；runtime asset 只有在安装时验证并形成 runtime tree/deployment digest 后才可写入
deployment identity。任何 runtime、关键配置、host 或 canary endpoint 变化都必须创建新计划并
重新执行准入。

Mac 上
`[2026-08-01T19:38:00Z, 2026-08-04T19:38:00Z)` 是已通过的 `TODO-0013` 权威诊断
窗口，不是本任务 Day 0。W32
`[2026-08-03T00:00:00Z, 2026-08-10T00:00:00Z)` 是 `TODO-0011` 所需的自然干净
ISO 周。在这两个窗口结束前禁止注入计划故障、重启主机、rollback 或修改生产配置。

## Day 0 硬准入

仅当以下项目全部成立时才能继续：

1. `TODO-0011`、`TODO-0012`、`TODO-0013`、`TODO-0014`、`TODO-0015` 在
   `docs/todos/tasks.json` 和对应 GitHub Issue 中均为 completed/closed。
2. v3.6.6 Release workflow `31020678952` 与 published-asset verification
   `31021854876` 均 PASS，tag 仍是 annotated tag 并 peel 到计划中的 commit。
3. lifecycle `status` 和 `verify` PASS，current/previous、六个 image ID、配置摘要、数据卷和
   受保护状态没有未解释漂移。
4. 五个 Prometheus targets 为 up，规则 health 全部为 ok，45 天 retention 生效；最近 daily
   和完整 W32 weekly report 均为 `coverage_complete=true`、`gap_count=0`。
5. Alertmanager firing/resolved 回执在七天有效期内，Grafana 使用非默认生产凭据；最新
   evidence package 已在异机通过全部 `SHA256SUMS`。
6. 外部 canary 固定结束聚合 PASS，Mac 与 production host 身份不同，run/watchdog 已连续
   三个自然分钟 PASS。
7. 所有影响 Linux x64 候选的 P0 缺陷均已关闭或有 reviewed disposition；Issue #30 中没有
   未回答的 blocker。

仓库状态检查：

```bash
python3 scripts/manage_todos.py show TODO-0011
python3 scripts/manage_todos.py show TODO-0013
python3 scripts/manage_todos.py show TODO-0016
python3 scripts/manage_todos.py check
```

目标机准入检查：

```bash
sudo python3 /home/honeybury/boost-gateway-controller/scripts/manage_release_deployment.py status
sudo python3 /home/honeybury/boost-gateway-controller/scripts/manage_release_deployment.py verify
sudo systemctl list-timers --all 'boost-gateway-*'
sudo python3 /home/honeybury/boost-gateway-controller/scripts/tools/check_observability_preflight.py
```

准入输出必须复制进 evidence ledger；终端显示 PASS 但没有不可变 summary 不算完成。

## 计划内演练阶段

计划演练在 W32 weekly/final ledger 和 `TODO-0013` 关闭之后执行，并在正式 Day 0 之前结束。
原因是 72h 聚合的 inclusive availability 会把批准维护分钟也计为失败，不能用 maintenance
排除来隐藏计划内停机。演练必须使用最终 candidate/config/host，全部恢复并重新 verify 后才可
开始稳定窗口。

执行顺序和硬上限：

| 顺序 | 场景 | RTO | RPO | 必须验证 |
|---:|---|---:|---:|---|
| 1 | gateway restart | 300s | 0 | ready、Prometheus、SDK full-flow、restart attribution |
| 2 | single backend restart | 300s | 服务状态边界 | RED/transport counters、业务恢复、restart attribution |
| 3 | network/backend outage | 600s | 0 | 告警 firing/resolved、typed error、retry/circuit recovery |
| 4 | Redis restart/restore | 600s | <=60s | PING、AOF/RDB、submit/top/rank、SDK full-flow |
| 5 | release rollback and upgrade-back | 600s each | 0 | previous/current digest、data volume、canary identity |
| 6 | host reboot | 600s | <=60s | systemd/Compose、监控、timers、canary、boot identity |

每个场景必须：

1. 在 Issue #30 预先记录计划 UTC 半开区间、操作者、审批人、注入方式和停止条件。
2. 注入前保存 deployment/status、container restart/OOM、Redis persistence、canary 最新分钟和
   Alertmanager 状态摘要。
3. 使用已有 lifecycle 或 recovery runbook 的受控入口；不得直接删除 volume、evidence、
   backup 或 transaction。
4. 记录真实 started/recovered 时间、RTO/RPO、告警投递 ID、业务验证和所有原始 summary
   SHA-256。
5. 失败时立即停止后续场景，创建 Issue/RCA；不得重跑后覆盖失败记录。
6. 全部场景结束后重新执行 lifecycle verify、observability preflight、backup status 和三个
   自然分钟 canary。只有这一最终恢复点可以作为 Day 0 候选。

Redis/backup/host recovery 使用
[`backup-recovery-policy-runbook.md`](backup-recovery-policy-runbook.md)；release
rollback 使用 [`release-lifecycle-runbook.md`](release-lifecycle-runbook.md)。每个执行结果从
[`../production/production-recovery-drill-record-template.json`](../production/production-recovery-drill-record-template.json)
派生独立 create-only record，不能修改模板冒充结果。

## 正式窗口声明

演练全部通过后，选择下一个尚未采样的自然 UTC 分钟作为 start，并固定
`end = start + 72h`。在 Issue #30 先写入：

- 精确 UTC 半开区间 `[start, end)`；
- candidate tag/commit/runtime/config/deployment/host identity；
- external endpoint、SDK 实际版本和 Mac host identity；
- 已完成演练 record/summary digest；
- maintenance plan（没有则明确为空）；
- 当前 open P0 Issue 清单（必须为空）；
- 终止和 supersede 条件。

Issue comment 成功写入后才允许把第一个自然分钟称为 Day 0。run/watchdog 已加载时不得手工
执行 `run`，否则同一分钟重复样本会使聚合失败。

窗口开始后观察至少三个连续自然分钟和 watchdog freshness，再确认声明。声明前的样本仍保留，
但不属于正式半开区间。

## 72 小时运行规则

窗口内只允许只读观测和已经声明、不改变 runtime subject 的 evidence 操作：

- 不升级、rollback、重启、重新创建 container 或修改 runtime/config/data schema；
- 不盒盖或注销 Mac，不卸载 run/watchdog，不改变 endpoint/deployment record；
- 不在 `miniserver` 编译、测试、压测或运行 load generator；
- 不删除、修复、重写或合并失败样本、incident、daily/weekly record；
- 不事后添加 maintenance window；
- 每日核对 targets/rules、restart/OOM、host memory/disk、container working set、Redis
  persistence、transport timeout/retry 和 canary gap/duplicate；
- 所有异常立即创建 incident 和 Issue，保留旧窗口并根据终止条件决定是否 supersede。

以下任一条件使窗口失效并要求新的完整 72h：

- candidate、endpoint、host 或关键配置改变；
- run/watchdog 被卸载、Mac 系统睡眠或出现超过两分钟的非维护 gap；
- invalid/duplicate sample；
- 未知 restart、OOM、磁盘压力或无法解释的持续资源增长；
- 为修复 runtime 缺陷部署新二进制或 image；
- Issue #30 中出现未处置 P0 缺陷。

短暂业务失败不会被删除。最终是否通过由固定窗口聚合、资源趋势和 incident disposition 共同
决定，而不是由人工忽略分钟。

## 固定结束与收口

在 `end + 45s` 后生成 create-only 72h aggregate：

```bash
"$HOME/.local/share/boost-gateway-canary/venv/bin/python" \
  "$HOME/.local/libexec/boost-gateway-canary/external_business_canary.py" \
  --evidence-root "$HOME/.local/share/boost-gateway-canary/evidence" \
  --deployment-record "$HOME/.config/boost-gateway-canary/deployment-record.json" \
  --environment-file "$HOME/.config/boost-gateway-canary/environment" \
  aggregate --window 72h --end <exact-window-end-Z>
```

正式 PASS 至少要求：

- 4,320 expected/recorded samples、单一 candidate/endpoint、零 invalid/duplicate；
- coverage 与 inclusive availability 均不低于 99.9%，最大非维护 gap 不超过两分钟；
- lifecycle/current、13 个 governed containers 和五个 Prometheus targets 保持一致；
- unknown restart、OOM、disk pressure、sustained thermal throttle 为零；
- backend/container/host memory、FD、thread、queue 没有无界趋势；
- 所有 pre-Day0 drill、窗口 incident、daily/weekly、Alertmanager、backup 和 off-host evidence
  可由摘要 digest 复算。

创建 final shakedown record，明确选择或拒绝 v3.6.6 作为 `TODO-0017` Day 0 candidate。
随后才可完成 `TODO-0016`、关闭 Issue #30，并为 30 天窗口写入新的独立声明。72h 时间不能
直接累计到 30 天窗口。
