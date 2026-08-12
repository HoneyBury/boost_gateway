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

## 2026-08-12 执行结果

本轮退出指标已全部达到，并已写回评审基线：

| 指标 | 执行前 | 执行后 |
|---|---:|---:|
| public entrypoint | 23 | 23 |
| canonical CLI | 127 | 127 |
| 未直接测试 CLI | 0 | 0 |
| 超过 800 行的脚本 | 15 | 10 |
| 超过 500 行的脚本 | 31 | 20 |
| 跨 CLI 导入边 | 15 | 8 |
| workflow 到脚本依赖边 | 122 | 103 |
| 三处以上重复 workflow 片段 | 11 | 5 |

具体收口如下：

1. 发布部署 CLI 拆为布局、执行器、安装、事务、激活、恢复和命令分发边界；原 CLI、参数、测试
   patch 路径和退出语义保留。
2. 备份恢复实现进入 CLI-free 库，7 个消费者不再导入备份 CLI；独立 forced-command 安装同步
   携带共享库。
3. 仓库治理检查由 `check_mainline_readiness.py --repository-suite` 聚合，构建时长与 sccache 证据
   由一个 composite action 复用；CI 和 Release 不再重复展开同一治理清单。
4. 11 个超过阈值的 gate/release 脚本按契约、运行时帮助函数和编排职责拆分，并用直接模块契约
   测试锁定仓库根、稳定常量与关键 callable。
5. 迁移期 21 个 reviewed library exception 已在最终评审中纳入新的稳定 library 基线并清零；
   后续新增文件仍必须逐项登记，不能复用本轮例外。

`collect_v2_perf_baseline.py` 和 `verify_preprod_recovery_drill.py` 已分别具有 `perf_statistics` 与
`recovery_evidence` 边界，但主体仍是 4,707/2,069 行。它们涉及 fixed-runner 性能采集和真实恢复
副作用，本轮不为了行数目标继续机械拆分；下一轮应先建立录制 fixture 与失败注入，再按进程资源、
业务负载、故障执行和证据渲染继续拆解。

## 2026-08-12 第二轮主动减量

第二轮以第一轮冻结值为基线，处理尚未完成的两个最大热点和全部剩余跨 CLI 实现依赖：

| 指标 | 第二轮基线 | 退出目标 |
|---|---:|---:|
| public entrypoint | 23 | 不高于 23 |
| canonical CLI | 127 | 不高于 127 |
| `scripts/tools` 文件 | 55 | 不高于 55 |
| 未直接测试 CLI | 0 | 保持 0 |
| 超过 800 行的脚本 | 10 | 不高于 8 |
| 跨 CLI 导入边 | 8 | 清零 |
| workflow 唯一脚本依赖 | 47 | 不高于 47 |
| workflow 到脚本依赖边 | 103 | 不高于 103 |
| 三处以上重复 workflow 片段 | 5 | 不高于 5 |

内部 library 数量不是减量成果，允许增长仅用于替换既有大文件职责或跨 CLI 复用。每个新增模块
必须同时满足：没有 `argparse`/`main()`、只有一个维护领域、具有直接测试或录制 fixture、原入口
兼容、登记唯一消费者和退役条件。第二轮结束时将稳定模块纳入新人工基线并清空迁移例外。

实施顺序：

1. 为性能采集的 CPU/进程、连接预算、业务操作、OTel、资源分析和报告判定建立可导入契约，
   保持原模块的兼容导出；CLI 只保留参数、拓扑生命周期和阶段编排。
2. 为恢复演练的子进程失败注入、Compose/image preflight、SDK 探针和记录渲染建立无副作用
   fixture；CLI 只保留真实演练状态机。
3. 将剩余 8 条 peer-CLI 导入改到既有或新建的 CLI-free contract：容量证据、SBOM、备份 vault、
   restore transport、隔离恢复、观测证据、Compose preflight 和恢复策略。
4. 运行原 CLI `--help`、领域测试、完整 Python suite、治理 suite 与已有 CTest 单元入口；生产
   Compose、Redis、fixed runner 和 secret 仍需目标环境 rehearsal，本地验证不冒充外部证据。
