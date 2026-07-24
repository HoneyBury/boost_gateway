# v3.6.2 发布后企业运营主线

更新时间：2026-07-24

## 目标

当前两个月不扩大业务或默认协议面，而是把 v3.6.2 不可变资产交付为可重复运营的
Ubuntu 24.04 x64 单节点系统：自动部署、观测、追溯、备份、恢复、回滚和仓库强制
治理完成后，执行 72 小时预演，并让同一 tag/SHA/runtime digest 连续运行至少 30 天。

完整 SLI/SLO、RTO/RPO 和 Day 0 规则见
[单节点运营计划](single-node-enterprise-validation-plan.md)。版本化执行状态由
`TODO-0007` 至 `TODO-0018` 和对应 GitHub Issues 管理。

## 当前优先级

| 优先级 | 工作项 | 完成标准 |
|---|---|---|
| P0 | release-driven 部署与 host admission | 全新主机不编译源码，一条治理入口安装并通过 full-flow；失败可在 10 分钟内回滚 |
| P0 | 观测、canary 和证据 ledger | 45 天 metrics、外部 SDK canary、部署/变更/告警可追溯 |
| P0 | 备份和恢复 | gateway/backend 5 分钟，Redis/host/rollback 10 分钟 RTO 演练通过 |
| P0/P1 | 仓库强制治理 | required checks/review、CODEOWNERS、SECURITY、Action SHA pinning 不可绕过 |
| P1 | 72 小时上线预演 | 无未知 restart/OOM/无界增长；网络、Redis、备份、恢复和回滚证据完整 |
| P1 | 30 天不可变验证 | 连续 `>=2,592,000s`，availability/canary/coverage 均 `>=99.9%` |
| P2 | 运营评审与优化 | readiness report 区分已证明单节点能力与未证明容量/HA；优化有 RCA 和前后基线 |

## 执行顺序

1. **冻结部署输入**：只接受 v3.6.2 release archive、checksum、SBOM、provenance 和明确
   的配置版本，不在服务器上构建源码。
2. **实现 host preflight**：校验 OS、磁盘、端口、Docker/Compose、时钟、ulimit、目录
   权限、secret/config 和备份目标。
3. **完成幂等生命周期**：install、upgrade、rollback 重复执行结果一致，失败不会留下
   半配置状态。
4. **建立外部观测**：每分钟 SDK canary、metrics retention、告警通知和 deployment/
   evidence ledger 同时上线。
5. **完成恢复演练**：覆盖单进程、gateway、backend、Redis、host replacement、备份损坏
   和 rollback，记录 RTO/RPO。
6. **收紧仓库治理**：PR、review、required checks、安全披露和第三方 Action pinning 全部
   进入可验证门禁。
7. **执行 72 小时预演**：在拟生产配置上运行重启、网络、Redis、恢复和回滚场景；所有
   缺陷必须有 Issue/RCA。
8. **冻结 Day 0**：确认唯一 tag/SHA/digest/config，开始 30 天连续验证。runtime 或关键
   配置变化必须重新开始 Day 0。
9. **形成运营结论**：输出 readiness report、未证明边界和下一版本计划，不以降低门槛
   换取通过。

## 已完成基线

- v3.6.2 tag 固定在 `ac99ae353a2a6e846f934c8d81c78a07f420f683`。
- Release run `30063021104` 发布三平台 runtime、SDK 4.2.0、symbols/dSYM、SPDX、
  provenance 和 checksum。
- Linux x64、Linux ARM64、macOS ARM64 线上复验 runs `30063950242`、`30063441646`、
  `30063444082` 全部通过。
- 三平台 Release/R0、原生基线、容量/R4 和 2h 能力证据已形成；Linux 历史 8h 和隔离
  性能矩阵仍按其候选 SHA 和 runner 边界使用。
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

只有当 72 小时预演和 30 天不可变验证的全部门槛通过、恢复记录可复算、证据 provenance
完整且所有严重缺陷关闭时，本阶段才结束。最终报告必须同时列出已证明能力、未证明
边界、遗留风险和下一版本入口。
