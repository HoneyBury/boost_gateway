# 脚本与 Workflow 主动减量计划

更新时间：2026-08-12

## 目标

上一轮治理已经冻结未经评审的脚本、CLI、Workflow 依赖和跨 CLI 导入增长。本轮在不改变
公共命令、证据 schema、发布边界或 fixed-runner 语义的前提下，把治理重点从“阻止新增”推进到
“降低修改耦合”。

本轮不是按文件数做一次性重写。拆分只有在形成单一职责、直接测试和稳定兼容入口时才成立；
不得通过压缩格式、动态导入、复制实现、把 Python 改名为非受治理文件或刷新 maximum 来制造减量。

## 基线与退出指标

基线来自 `docs/tooling-metrics-baseline.json` 和
`scripts/gates/governance/check_tooling_metrics.py`：

| 指标 | 2026-08-12 基线 | 本轮退出目标 |
|---|---:|---:|
| public entrypoint | 23 | 不高于 23 |
| canonical CLI | 127 | 不高于 127 |
| 未直接测试 CLI | 0 | 保持 0 |
| 超过 800 行的脚本 | 15 | 不高于 10 |
| 超过 500 行的脚本 | 31 | 不高于 24 |
| 跨 CLI 导入边 | 15 | 不高于 8 |
| workflow 到脚本依赖边 | 122 | 不高于 105 |
| 三处以上重复 workflow 片段 | 11 | 不高于 5 |

脚本和 workflow 总行数继续作为观察值，不作为单独验收条件。共享库可能在迁移期增加，但每个
新增模块必须登记 reviewed growth exception、直接测试、消费者和退役条件；当迁移结束时删除
例外或把稳定事实纳入下一次人工评审基线。

## 实施顺序

### R1：热点脚本职责拆分

优先处理三个最高维护热点：

1. `collect_v2_perf_baseline.py`：分离进程/资源采集、业务负载、聚合判定、报告和 CLI 编排。
2. `manage_release_deployment.py`：分离布局与文件 I/O、恢复对账、事务激活和命令分发。
3. `verify_preprod_recovery_drill.py`：分离 Compose/image preflight、故障执行、恢复验证和记录渲染。

原路径、参数、退出码、summary 字段和可导入兼容符号保持不变。拆分前后的 fixture 输出必须等价；
已有测试对原模块的 patch 路径在一个兼容周期内继续有效，或者在同一变更中迁移到明确的新模块。

### R2：消除 CLI 之间的实现复用

备份恢复、容量证据、Compose 校验、SBOM 校验和观测调度的共享实现下沉到无 `main()`、无
`argparse` 的 `scripts/lib/` 模块。CLI 只导入共享库，不再把另一个 CLI 当作库使用。迁移期间
原 CLI 可以重新导出历史符号，但新消费者只能引用共享库。

### R3：Workflow 编排收敛

Workflow 只保留触发、权限、runner、cache、artifact 和 job 顺序。三处以上重复的 Conan、构建、
summary 和 artifact 准备逻辑进入现有 composite action；同一 workflow 中多个仓库治理脚本通过
一个稳定聚合入口执行。禁止为了减少静态依赖边而在 shell 中动态拼接或隐藏脚本路径。

### R4：生命周期收口

每次发布评审以下对象：零活动消费者的根兼容 shim、过期 workflow input、只剩历史用途的工具、
重复 summary renderer 和 reviewed exception。删除必须满足 inventory 中的 retirement condition，
并通过脚本清单、文档、release consumer 和对应行为测试。

## 阶段提交和验证

每个阶段独立提交，至少执行：

```bash
.venv/dev/bin/python -m pytest -q tests/python
.venv/dev/bin/python scripts/dev.py check
```

脚本拆分还要执行原 CLI 的 `--help`、相关 fixture/失败路径测试；Workflow 变更还要执行 catalog、
CLI contract 和一次对应 `workflow_dispatch` rehearsal。fixed-runner、secret、ruleset 和生产主机
状态仍需外部验证，本地 PASS 不替代这些事实。

## 暂不处理

- 不新增产品、协议、SDK 或生产支持面。
- 不把实验 gRPC、Raft protobuf writer 或新的平台声明带入本轮。
- 不合并仍有独立触发、权限、runner 或证据生命周期的 Workflow。
- 不在 v3.6.6 的 72 小时和 30 天验证期间改变生产 runtime 候选。
