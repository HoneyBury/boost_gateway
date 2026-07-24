# Enterprise Single-Node Operations And 30-Day Validation Plan

更新时间：2026-07-24

## 目标

在不扩大默认协议和业务范围的前提下，用两个月把 BoostGateway 从具备企业级
发布证据的框架候选，推进为可以持续维护、自动部署、监控、追溯和恢复的工程平台。
目标环境是 Ubuntu 24.04 x64 单节点，6 核 12 线程、16 GiB 内存、512 GiB 磁盘。

本阶段最终必须交付以下事实：

1. 服务器只消费不可变发布资产，不执行完整 Conan/CMake 编译。
2. 安装、部署、升级、回滚、备份、恢复和发布后验证均有幂等自动入口。
3. 每次运行都绑定 release tag、commit、asset/image digest、配置 digest、主机身份和操作者。
4. 指标、日志、外部业务 canary、告警和每日证据可以覆盖完整运行窗口。
5. 冻结 runtime 版本连续运行至少 30 天，不拼接不同版本或中断片段。
6. 真实故障能够被发现、告警、恢复并形成结构化演练或事件记录。
7. 性能优化只由长期数据或事件驱动，保留优化前后证据且不放宽既有门槛。

本计划验证的是单节点可运营性、长期资源稳定性和部署恢复能力，不声明多节点高可用、
跨故障域灾备或生产容量上限。

## 时间边界

完整阶段按八周规划。准备工作不计入连续 30 天：

| 阶段 | 建议窗口 | 目标 | 退出条件 |
|---|---|---|---|
| A 契约与治理 | Week 1 / Day 1-3 | 冻结拓扑、SLI/SLO、RTO/RPO、证据和安全边界 | 计划、TODO、Issue、机器契约和文档入口一致 |
| B 自动部署 | Week 1-2 / Day 3-10 | 从不可变 release 自动部署到 Ubuntu x64 | 全新主机可幂等安装、升级、验证和回滚，服务器不编译项目 |
| C 观测与恢复 | Week 2 / Day 8-14 | 补齐 host/container/app/canary、备份和恢复闭环 | 真实告警、异机备份和恢复演练通过 |
| D 上线预演 | Week 3 / 连续 72 小时 | 暴露部署、资源和运营缺陷 | 无未知重启或无界资源增长，故障矩阵达到 RTO/RPO |
| E 冻结验证 | Week 3-7 / 连续 30 天 | 形成单一 runtime 候选长期事实 | 持续时间、覆盖率、可用性、canary、资源和恢复标准全部通过 |
| F 运营评审 | Week 8 / Day 3-5 | 形成平台成熟度结论和下一版本输入 | 最终报告、风险、容量边界、性能 RCA 和下一阶段 TODO 完整 |

阶段可以在不破坏依赖关系时并行。仓库治理从 Week 1 开始，可与主机准入、部署和
观测实现并行；30 天冻结窗口只有在 A-D 全部通过后才开始。

## TODO 阶段和依赖

| 阶段 | TODO | 前置依赖 | 并行边界 |
|---|---|---|---|
| A 契约 | `TODO-0007` | 无 | 首先完成，冻结全部后续验收口径 |
| B 主机与部署 | `TODO-0008`、`TODO-0009`、`TODO-0010` | `TODO-0007`；`0009` 依赖 host preflight，`0010` 依赖可部署资产 | host admission 和 release consumer 可分机开发，目标机联调时汇合 |
| C 运营闭环 | `TODO-0011`、`TODO-0012`、`TODO-0013` | 可部署服务和持久目录 | observability、recovery、external canary 可并行，必须在预演前全部通过 |
| G 治理轨道 | `TODO-0014`、`TODO-0015` | `TODO-0007` | 与 B/C 并行；影响发布安全的门禁必须在 Day 0 前生效 |
| D 预演 | `TODO-0016` | `TODO-0008` 至 `TODO-0015` 的正式环境阻断项全部完成 | 严格串行，72 小时内允许修复后重跑 |
| E 长期验证 | `TODO-0017` | `TODO-0016` 成功并冻结 tag/SHA/digest | 严格串行；runtime 变更重置 Day 0 |
| F 最终评审 | `TODO-0018` | `TODO-0017` 完整结束 | 独立复算证据后才能关闭整个计划 |

## 单节点部署契约

当前目标路径为 Docker Compose。单节点 Kubernetes 不增加故障域，且会消耗额外资源，
本阶段不作为默认部署入口。

自动部署模块至少应包含：

- Ubuntu 24.04 x64 host preflight 和安全基线检查。
- 直接下载 GitHub Release linux-x64 runtime、SBOM、provenance metadata 和 checksum。
- checksum、tag/commit、asset attestation 和 ELF 架构校验。
- 从发布 archive 生成 runtime-only Docker context，不在目标机编译 C++ 或解析 Conan 图。
- 固定 Ubuntu base image digest、项目镜像 tag/digest 和生产 Compose overlay。
- 独立的 secrets/env、release、current、backup、evidence 和 persistent data 目录。
- `install`、`deploy`、`upgrade`、`rollback`、`status`、`backup`、`restore`、`verify`
  幂等命令。
- systemd 管理 Compose 生命周期，宿主机重启后自动恢复。
- 发布后 health、ready、Prometheus targets、Redis 和 SDK full-flow 验证。
- 失败时保留现场、写出失败 summary，并自动恢复上一已验证版本。

任何自动部署不得把 GitHub token、TLS private key、Redis password 或 Grafana password
写入仓库、release archive、部署 provenance 或日志。

## SLI 与 30 天门槛

正式计时使用单一 release tag、commit 和 runtime asset digest。环境或监控代码可以在
不改变 runtime subject 的前提下修复，但必须记录；部署新的 runtime 代码后从 Day 0
重新计时。

| 领域 | 指标 | 30 天验收标准 |
|---|---|---|
| 连续性 | frozen runtime elapsed | `>= 2,592,000s`，不得拼接不同 runtime 或中断段 |
| 可用性 | external canary / ready | `>= 99.9%`，并同时报告包含和排除批准维护窗口的两种口径 |
| 业务 | SDK full-flow canary | 每分钟至少一次；login/room/battle/settlement/leaderboard/reconnect 总成功率 `>= 99.9%` |
| 延迟 | backend route P99 | 正常稳态低于现有 `200ms` 告警线；超线必须关联事件或负载变化 |
| 资源 | OOM / disk pressure / unknown restart | 均为 0；任何 restart 必须能关联部署、演练或 incident |
| 内存 | host/container RSS | host 长期低于 80%；无连续 7 天不可解释的单调增长 |
| 文件与线程 | fd/thread/queue trend | 无无界增长；接近已配置上限前必须告警 |
| CPU/温度 | host/container CPU、thermal throttle | 无持续 throttle；稳态有余量，压力流量不与服务同机运行 |
| 磁盘 | filesystem usage | 长期低于 75%，预测 30 天内达到 85% 时提前告警 |
| 观测 | Prometheus/canary/evidence coverage | 覆盖率 `>= 99.9%`；非维护最大采样空洞 `<= 2min` |
| 追溯 | deployment/incident/drill records | 100% 记录 tag、SHA、digest、config、host、时间、操作者和结果 |

Prometheus 本地 retention 至少设为 45 天，并每日生成可异机保留的 evidence/export。
Alertmanager 必须连接真实通知通道，默认占位 receiver 不能进入正式计时环境。

## 恢复和数据边界

恢复目标继承现有 production runbook，并在真实单节点执行：

| 场景 | RTO | RPO | 必须验证 |
|---|---:|---:|---|
| gateway restart | `<= 5min` | 0 | ready、metrics、external SDK full-flow |
| single backend restart | `<= 5min` | 按该服务状态边界声明 | RED counters 恢复且业务 canary 通过 |
| Redis restart/restore | `<= 10min` | 生产验证 profile 目标 `<= 60s` | ping、submit/top/rank、恢复记录 |
| release rollback | `<= 10min` | 0，数据卷不回滚 | previous digest 恢复、canary 和 checksum 通过 |
| host reboot | `<= 10min` | 按 Redis 策略声明 | systemd/Compose 自动恢复、监控和 canary 恢复 |
| network/backend outage | `<= 10min` | 0 | 告警状态、熔断/恢复、错误计数和 full-flow |

Redis 当前默认 RDB-only 无法承诺低变更场景下的 60 秒 RPO。正式长期验证 profile
必须显式评审 AOF `everysec`，验证其性能影响，并同时保留每日 RDB/volume 备份。
备份必须加密并复制到目标主机之外；同磁盘副本不算灾难恢复备份。

## 观测、追溯和证据

正式环境至少采集：

- host CPU、load、memory、swap、filesystem、disk I/O、network、temperature、throttle。
- container CPU、RSS、pids、restart、health、network 和 volume 使用量。
- gateway ready、session、backend RED、route latency、rate limit、OTel 和业务成功计数。
- Redis availability、memory、eviction、RDB/AOF、last-save 和 exporter 状态。
- 每分钟 `external SDK canary` 的步骤、延迟、错误类别和候选身份。

每次部署生成 immutable deployment record；每日生成 daily checkpoint；每周生成 trend
report；故障生成 incident record；演练生成 recovery drill record；最终生成 30-day
readiness report。所有记录引用原始 summary，而不是只保留人工结论。

## 性能和变更政策

Week 1-3 的建设/预演阶段可以修复缺陷和性能问题。每项优化必须包含：

1. 来自指标、canary、profile 或 incident 的可复现问题。
2. 不改变负载身份和阈值的优化前基线。
3. 聚焦测试、全量回归和同环境优化后基线。
4. 对 CPU、内存、P99、吞吐、错误率和恢复边界的影响说明。
5. 独立 commit、Issue 和回滚方案。

正式 30 天窗口内，文档、告警说明或不影响 runtime subject 的证据工具可以受控更新。
runtime/config/data schema 变更只有在 P0 安全或数据风险下才允许紧急部署；部署后旧窗口
降级为诊断证据，新版本重新从 Day 0 计时。不得为了维持连续时间隐瞒版本变化。

## 企业级仓库治理并行轨道

以下治理工作与 A-D 并行，必须在正式 30 天窗口开始前完成会影响发布安全的部分：

- 普通 PR 自动执行有界 build/test/governance，main 设置 required status checks。
- 至少一人 review、conversation resolution、管理员保护和明确的紧急变更流程。
- 增加 `CODEOWNERS`、`CONTRIBUTING.md`、`SECURITY.md` 和支持/治理边界。
- GitHub Actions 固定到完整 commit SHA，限制允许的第三方 action 和默认 token 权限。
- 清理 current-state、architecture、Compose、monitoring 和 deploy 文档中的版本漂移。
- planned 能力必须映射到开放 TODO/Issue；完成状态必须有可复核证据。
- 建立自动 sanitizer/fuzz/dependency vulnerability 的定期入口，但不占用部署服务器。

## 阶段完成定义

本阶段只有同时满足以下条件才可关闭：

1. 自动部署、回滚、备份、恢复、监控、canary 和 evidence 全部由仓库入口驱动。
2. 72 小时预演和完整恢复矩阵通过。
3. 单一不可变 runtime 连续运行满 30 天并满足全部硬门槛。
4. 期间所有失败、人工操作和例外都有可追溯记录。
5. 性能修复均有 RCA、前后证据和回归，不存在事后放宽门槛。
6. 仓库治理、文档事实源和 GitHub TODO/Issue 没有漂移。
7. 最终报告明确区分已证明的单节点能力、未证明的容量/HA 能力和下一阶段风险。

对应版本化任务为 `TODO-0007` 至 `TODO-0018`，以 `docs/todos/tasks.json` 为状态
事实源；只有满足各任务全部验收标准后才能勾选完成。
