# BoostGateway v3.5.x 性能基线

更新时间：2026-07-18

## 当前口径

发布容量、长稳和专项性能结论只接受 Linux fixed runner 上的 Release + Conan lockfile 构建。GitHub-hosted 或开发机 smoke 用于快速发现回归，不用于声明生产容量；历史 Windows 数据已退出支持范围，不与当前结果横向比较。

当前性能入口均为手动 workflow：

| 入口 | 用途 | 触发方式 |
|---|---|---|
| `.github/workflows/perf-regression.yml` | smoke / baseline / capacity | `workflow_dispatch` |
| `.github/workflows/long-soak-capacity.yml` | 2h/8h soak、capacity、business-capacity | `workflow_dispatch`，Linux fixed runner |
| `.github/workflows/release.yml` | 发布构建及所选 baseline | `v*` tag 或 `workflow_dispatch` |
| `.github/workflows/nightly-stability.yml` | bounded smoke/short/medium soak | `workflow_dispatch`；名称为历史沿用，不是定时 nightly |

主线严格使用 Conan 2.8.1、仓库 profile 和 `conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock`。证据必须记录 commit、runner、构建目录、profile、lockfile 和原始 summary 路径。

## 已确认的 Fixed-Runner 事实

`v3.5.0` 候选 `eed73cc` 的 `long-soak-capacity.yml` run `29509769283` 已在 Linux AOI fixed runner 上完成真实 2h、capacity、business-capacity 和 R4，核心 summary provenance 一致且 `overall_pass=true`。

同一轮中的容量结果：

| Profile | 关键结果 | 状态 |
|---|---|---|
| capacity | echo 5K/10K 最终 P99 分别为 5ms/5ms；battle-500 最终 P99 150ms；0 failed/rejected | PASS |
| business-capacity | 500 connected；3 个 SDK full-flow 客户端完成（6.087s） | PASS |
| 2h soak | 7202.597 秒、1624 轮；无 failure event、violating check 或 failed check | PASS |

候选 `80cc5cf` 的 run `29478926992` 完成 7204.569 秒，但有一轮 `multi_battle_tick_100_entities.p99=2141.51us` 超限。该运行保留为失败事实，不与后续成功运行拼接。

这些结果是 v3.5.0 的历史发布事实。v3.5.3 修改采集器或门禁后，必须在新的冻结 SHA 上刷新全部高风险证据。

## 当前状态

| 维度 | 状态 |
|---|---|
| Linux capacity / business-capacity | v3.5.0 候选已测定；v3.5.3 冻结 SHA 待刷新 |
| 2h soak | v3.5.0 候选已通过 |
| 8h soak | 待在 v3.5.3 同一冻结 SHA 实测 |
| 1/2/4 核资源线性 | 待实测；不得使用线性推算值 |
| TLS on/off | 待 fixed runner 对照实测 |
| OTel on/off | 待 fixed runner 对照实测 |
| Matchmaking 专项 | 并发 join/status/leave 采集入口已实现；fixed-runner 数据待采集 |
| Leaderboard 专项 | 并发 submit/top/rank 与可信 Redis on/off 对照入口已实现；fixed-runner 数据待采集 |

## 测量拓扑

```text
v2_gateway_pressure --TCP--> v2_gateway_demo (:9201)
                              |-- login (:9202)
                              |-- room (:9302)
                              |-- battle (:9303)
                              |-- matchmaking (:9304)
                              `-- leaderboard (:9305)
```

主要采集组件：

| 组件 | 位置 | 用途 |
|---|---|---|
| `v2_gateway_pressure` | `examples/v2_gateway_pressure/` | echo/battle 并发负载 |
| `collect_v2_perf_baseline.py` | `scripts/producers/`（兼容入口位于 `scripts/`） | 启动拓扑、重复执行、聚合结果和资源快照 |
| `collect_release_baseline.py` | `scripts/producers/`（兼容入口位于 `scripts/`） | 发布基线聚合 |
| `run_long_soak_capacity.py` | `scripts/gates/production/` | fixed-runner 2h/8h 和容量编排 |
| `v2_arch_baseline_gates.json` | `config/perf/` | 架构 microbenchmark 阈值 |

## 标准命令

本地命令要求已经按 `conan/README.md` 完成 Release 构建。默认构建目录为 `build/release`。

```bash
# 快速 smoke
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset smoke

# baseline，至少三轮
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset baseline \
  --repetitions 3

# capacity
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset capacity \
  --repetitions 3 \
  --cpu-set 0-1

# capacity + SDK 业务闭环
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset business-capacity \
  --repetitions 3 \
  --include-business-flow \
  --business-flow-clients 3

# Matchmaking + Leaderboard 并发专项
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset business-capacity \
  --repetitions 3 \
  --business-operation-scenario matchmaking \
  --business-operation-scenario leaderboard \
  --business-operation-clients 16 \
  --business-operation-iterations 10
```

固定 runner 的 2h/8h 与容量应通过 `long-soak-capacity.yml` 运行。8h 使用 `run_8h_soak=true`；业务专项使用 `run_business_operation_perf=true`；Redis 对照同时使用 `leaderboard_redis_comparison=true`，workflow 会创建 run 独占的临时 Redis 7 容器并在结束时清理。CPU 专项使用 `cpu_set=0`、`cpu_set=0-1` 和 `cpu_set=0-3` 分别 dispatch。采集器会通过 Linux `sched_setaffinity` 应用并回读约束，结果写入 `summary.resource_constraint` 和各进程快照。CPU 编号必须属于 runner 当前允许集合。

Redis 对照的两个准确模式名是 `in_memory_only` 与 `redis_primary_with_memory_shadow`。后者的写入同时保留内存影子、查询优先 Redis，不能表述为 Redis-only。每种模式至少执行三轮；证据必须包含双方完整 runs、启动日志证明、Redis 前后 PING、隔离 key 的 ZCARD 下限以及 submit/top/rank 的吞吐和 P50/P99 median delta。R4 在 opt-in 时对这些真实性字段设硬门禁，但在形成历史基线前不设置任意性能回退百分比。

1/2/4 核三轮 workflow 完成后，下载各自 artifact，并用 `scripts/aggregate_cpu_capacity_evidence.py --source 1:<run-id>:<dir> --source 2:<run-id>:<dir> --source 4:<run-id>:<dir>` 生成 `cpu-capacity-matrix-summary.json`。聚合器会验证同一候选 SHA、run ID、requested/effective affinity、三轮 case 集合及 R4 契约。`evidence_complete` 表示矩阵来源可信完整；`all_workload_gates_passed` 单独表示三档均满足性能门槛，避免把可信的 1 核容量边界误写成无效证据。

## 输出与判定

每次 baseline 采集输出独立目录：

| 产物 | 说明 |
|---|---|
| `summary.json` | commit、平台、preset、case 聚合、release gates、资源与进程快照 |
| `report.md` | 人工可读报告 |
| `results/*.result.json` | 每轮压力工具原始输出 |
| `results/*.gateway.diagnostics.json` | case 后的 Gateway diagnostics |
| `results/business-operation-perf.json` | 每轮 Matchmaking/Leaderboard 操作结果、跨轮聚合、time-to-match、吞吐、P50/P99 和错误分布 |
| `summary.leaderboard_persistence_comparison` | Redis 对照双方原始结果、日志/PING/ZCARD 证明与每操作 median delta |
| `logs/*.stdout.log` / `logs/*.stderr.log` | Gateway 和 backend 日志 |

发布证据还必须包含 workflow/run、实际 checkout SHA、runner 标签、Conan profile/lockfile 摘要。失败运行和确认复测分别归档，不覆盖原产物。

### 应报告的指标

- 延迟：P50、P90、P99 和最大值。
- 流量：connected、rejected、failed、总消息和 msg/s。
- 资源：Gateway/后端 CPU、RSS、fd、线程数和宿主机负载。
- 长稳：实际持续时间、完成轮次、P99 漂移、RSS/fd 趋势和全部失败事件。
- 业务专项：match 完成时间、leaderboard submit/top/rank 吞吐与错误率。

### 现有 release case 阈值

`collect_v2_perf_baseline.py` 的 `evaluate_release_gates()` 当前按以下 case 判定：

| Case | 条件 |
|---|---|
| echo-100 | rejected=0、failed=0、P99 <= 50ms |
| echo-1000 | rejected=0、failed=0、P99 <= 50ms |
| battle-20 | rejected=0、failed=0、消息数 >= 1000、P99 <= 100ms |
| battle-100 | rejected=0、failed=0、消息数 >= 5000、P99 <= 250ms |
| battle-500 capacity | rejected=0、failed=0、P99 <= 500ms |

架构 microbenchmark 的 warning/critical 阈值由 `config/perf/v2_arch_baseline_gates.json` 管理，不能用该微秒级阈值替代端到端 case gate。

## v3.5.3 待补矩阵

| 专项 | 最低实验设计 | 验收输出 |
|---|---|---|
| CPU 线性 | 1/2/4 核各三轮；使用 affinity 或 cgroup quota，保持其他配置不变 | 吞吐、P99、CPU、RSS、相对扩展效率 |
| Matchmaking | 多档并发 join；持续采集队列深度和完成时间 | 成功率、完成时间 P50/P99、积压峰值 |
| Leaderboard | submit/top/rank 分开压测；Redis on/off 各三轮 | 每操作吞吐、P99、错误率、Redis 资源 |
| TLS | 同一候选、同一负载、TLS off/on 各三轮 | P99 增量、吞吐衰减、CPU/RSS 增量 |
| OTel | exporter off/on 各三轮；记录 collector 状态 | P99 增量、吞吐衰减、export error/drop |
| 8h soak | 冻结 SHA；实际持续时间 >= 28800 秒 | 轮次、失败、CPU/RSS/fd 趋势和宿主快照 |

`--io-cores 1/2` 只改变应用 I/O 线程配置，不能证明机器被限制为 1/2 核。当前 `--cpu-set` 入口已经实现，但尚未形成 v3.5.3 fixed-runner 实测结论；CPU 线性实验必须记录 `resource_constraint.effective_cpu_set`。

## 历史数据边界

2026-05 的 Windows/MSVC baseline、Windows 高精度定时器优化和本机 DLL 部署记录属于已停止支持平台的历史资料。它们不再出现在当前容量表、待办或部署说明中；原始数据仍可从 `runtime/perf/20260523-165827/` 与 `runtime/perf/p0-*-local/` 审计。

当前 Linux/Conan 构建不存在需要在 Release 目录手动复制 `hiredis.dll` 的步骤，也没有未解决的 `cluster_router_e2e_test.cpp` 编译阻断。若这些问题再次出现，应以新的失败日志和 issue 记录，而不是沿用历史已知问题。

## SLO 解释边界

实验室性能 gate 用于候选间回归比较，不等同于线上 30 天 SLO。可用性、错误预算和告警有效性必须来自生产监控时间序列；一次 baseline 或 soak 不能证明 99.9% 月度可用性。
