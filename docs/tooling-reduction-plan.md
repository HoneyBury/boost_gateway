# 脚本与 Workflow 主动减量计划

更新时间：2026-08-13

## 目标

在不改变公共命令、证据 schema、发布边界或 fixed-runner 语义的前提下，降低脚本与 Workflow
的修改耦合。治理以入口、依赖边、副作用和可验证边界为准，不以压缩格式或单纯减少行数制造成果。

## 当前冻结线

基线由 `docs/tooling-metrics-baseline.json` 和
`scripts/gates/governance/check_tooling_metrics.py` 执行：

| 指标 | 当前上限 |
|---|---:|
| public entrypoint | 23 |
| canonical CLI | 127 |
| `scripts/tools` 文件 | 55 |
| 内部 library 文件 | 55 |
| 其余受治理脚本 | 25 |
| 未直接测试 CLI | 0 |
| 超过 500/800 行脚本 | 20/5 |
| Workflow 唯一脚本依赖/依赖边 | 47/103 |
| 跨 CLI 导入 | 0 |
| 三处以上重复 Workflow 片段 | 5 |
| Windows 兼容片段 | 0 |

新增脚本必须登记 owner、消费者、测试、替代对象和退役条件。现有数量与耦合可以下降；不得只为
通过门禁提高 maximum。完整历史轮次和指标变化见
[治理归档](archive/governance/tooling-reduction-rounds-2026-08-12.md)。

## 当前旁路保护

剩余五个超过 800 行的热点直接操作 Docker volume、Redis 数据、备份保留集、外部业务环境或
一次性验证拓扑。在正式 72h/30 天候选期间不拆分这些入口，也不改变生产证据语义。

| 热点 | 必须保持的边界 |
|---|---|
| `backup_recovery.py` | 保留失败进入隔离区；临时容器尽力清理；证据与备份 create-only |
| `isolated_restore.py` | 已存在目标拒绝执行；只清理本次新建目标；清理失败写入摘要 |
| `benchmark_redis_persistence.py` | 活动卷身份不变；失败或模糊创建时只回收本次资源 |
| `external_business_canary.py` | 失败路径尽力退出 room；外部证据 create-only |
| `verify_restored_business_isolated.py` | 保留卷只读；工作拓扑一次性；清理失败不得误报 |

后续结构减量必须复用现有失败注入与 ownership fixture，每次只迁移一个无副作用纯契约。

## 下一步

1. Redis persistence benchmark 的 CSV 解析、相对变化和 external canary 的插值百分位已在
   现有 failure-injection fixture 下迁入 `perf_statistics`；下一次只选择另一个无资源副作用的
   纯判定契约。
2. 恢复业务验证已用 ownership transcript 记录 disposable 资源创建、只读源挂载和删除尝试，
   retained-seed/release 身份判定已迁入现有 `recovery_evidence`；active/retained volume 不得进入
   删除集合，Docker cleanup、maintenance window 和正式证据阈值继续留在原领域入口。
3. 保持 Workflow 数量、直接依赖和重复片段不增长。
4. 每月从 GitHub Actions 记录工具/Workflow 变更的失败原因和可复现性，不把 runner 环境失败
   与代码回归混为同一结论。
5. 每个阶段运行 `.venv/dev/bin/python scripts/dev.py check`；涉及真实 runner、secret、
   ruleset 或生产主机的结论继续通过外部 rehearsal 验证。

## 非目标

- 不新增产品、协议、SDK 或生产支持面。
- 不恢复 Windows 名义兼容。
- 不合并仍有独立触发、权限、runner 或证据生命周期的 Workflow。
- 不在正式候选窗口改变 runtime、配置身份、指标口径或生产证据语义。
