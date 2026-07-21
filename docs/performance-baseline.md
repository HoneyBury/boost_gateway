# BoostGateway v3.5.x 性能基线

更新时间：2026-07-21

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

这些结果是 v3.5.0 的历史发布事实。v3.5.3 已在其冻结 SHA 上刷新高风险证据；发布后的隔离矩阵属于新候选事实，不回写已发布 tag。

## 当前状态

| 维度 | 状态 |
|---|---|
| Linux capacity / business-capacity | v3.5.3 历史发布产物保留；候选 `37897e8` 已完成真实 lifecycle 的闭环饱和曲线，生产容量上限仍须按部署 SLO 单独声明 |
| 2h soak | v3.5.3 冻结 SHA 已通过 |
| 8h soak | v3.5.3 冻结 SHA run `29711044558` 已连续通过 28801.652 秒 |
| 1/2/4 service CPU 轴 | 候选 `37897e8` 三轮单变量矩阵已通过聚合；结论为 `partial_cpu_scaling`，不自动调整默认值 |
| TLS on/off | 阶段性 fixed-runner 对照已通过；最终冻结 SHA 待刷新 |
| `io_cores=1/2/4` 轴 | 候选 `37897e8` 三轮单变量矩阵已通过聚合；结论为 `no_material_io_core_gain`，保持当前默认值 |
| OTel on/off | 候选 `37897e8` 的 fresh Gateway/Battle Backend 三轮对照与 exporter/collector/backend 对账已通过 |
| Matchmaking 专项 | 并发 join/status/leave 阶段性 fixed-runner 数据已形成；最终冻结 SHA 待刷新 |
| Leaderboard 专项 | submit/top/rank 与可信 Redis on/off 阶段性数据已形成；最终冻结 SHA 待刷新 |

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
  --cpu-set 0-1 \
  --loadgen-cpu-set 4-7 \
  --loadgen-io-threads 4

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

# OTel off/on 专项；两个模式分别使用 fresh Gateway/Battle Backend 和相同 battle-100 负载
python3 scripts/collect_v2_perf_baseline.py \
  --build-dir build/release \
  --run-preset business-capacity \
  --repetitions 3 \
  --cpu-set 0-1 \
  --loadgen-cpu-set 4-7 \
  --loadgen-io-threads 4 \
  --otel-comparison
```

固定 runner 的 2h/8h 与容量应通过 `long-soak-capacity.yml` 运行。8h 使用 `run_8h_soak=true`；业务专项使用 `run_business_operation_perf=true`；Redis 对照同时使用 `leaderboard_redis_comparison=true`，workflow 会创建 run 独占的临时 Redis 7 容器并在结束时清理；OTel 对照使用 `otel_comparison=true`。CPU 专项使用 `cpu_set=0`、`cpu_set=0-1` 和 `cpu_set=0-3` 分别 dispatch，同时三档都显式固定相同的 `loadgen_cpu_set=4-7` 和 `loadgen_io_threads=4`。service/loadgen CPU 集合必须不重叠；采集器会在进程启动后回读两侧 affinity，并按每轮相邻快照计算资源差值。CPU 编号必须属于 runner 当前允许集合。

Redis 对照的两个准确模式名是 `in_memory_only` 与 `redis_primary_with_memory_shadow`。后者的写入同时保留内存影子、查询优先 Redis，不能表述为 Redis-only。每种模式至少执行三轮；证据必须包含双方完整 runs、启动日志证明、Redis 前后 PING、隔离 key 的 ZCARD 下限以及 submit/top/rank 的吞吐和 P50/P99 median delta。R4 在 opt-in 时对这些真实性字段设硬门禁，但在形成历史基线前不设置任意性能回退百分比。

OTel 对照固定使用会经过 backend route 的 `battle-100-30s`，不能用只走 Gateway echo 的流量替代。off/on 每种模式均使用 fresh Gateway 和 fresh Battle Backend、相同 CPU affinity 和至少三轮负载，避免前置 capacity 消耗 Battle Instance 上限污染对照；on 组由采集器内置 loopback collector 接收 OTLP/HTTP JSON。证据必须同时对齐进程 PID、启动日志、collector request/span/invalid/status、runtime exporter enqueued/exported/batch/buffered counters 与 backend routed requests，并输出 P99、吞吐、Gateway CPU/RSS median delta。当前只保留既有 battle 绝对性能门槛，百分比 delta 标记为 `observed_not_thresholded`，待同硬件积累历史样本后再制定回退阈值。

饱和曲线与单变量轴使用 `scripts/aggregate_saturation_axis_evidence.py` 聚合。聚合器会验证选点来自完整曲线，并检查同一候选 SHA、run ID、fixed runner/lockfile、service/loadgen affinity 隔离、相同 loadgen 核数与线程数、逐轮 before/after 资源差值、三轮真实 lifecycle 和非实验变量一致。聚合输出只提供决策证据，不会自动修改 runtime 默认值。

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

## 发布后矩阵纠偏

| 专项 | 最低实验设计 | 验收输出 |
|---|---|---|
| CPU 线性 | 1/2/4 核各三轮；使用 affinity 或 cgroup quota，保持其他配置不变 | 吞吐、P99、CPU、RSS、相对扩展效率 |
| Matchmaking | 多档并发 join；持续采集队列深度和完成时间 | 成功率、完成时间 P50/P99、积压峰值 |
| Leaderboard | submit/top/rank 分开压测；Redis on/off 各三轮 | 每操作吞吐、P99、错误率、Redis 资源 |
| TLS | 同一候选、同一负载、TLS off/on 各三轮 | P99 增量、吞吐衰减、CPU/RSS 增量 |
| OTel | exporter off/on 各三轮；记录 collector 状态 | P99 增量、吞吐衰减、export error/drop |
| 8h soak | 冻结 SHA；实际持续时间 >= 28800 秒 | 轮次、失败、CPU/RSS/fd 趋势和宿主快照 |

`--io-cores 1/2` 只改变应用 I/O 线程配置，不能证明机器被限制为 1/2 核。`v3.5.3` 的旧矩阵把服务和 load generator 放在同一 affinity 集合，只能作为整栈受限资源边界；新的线性实验必须分别记录 `service_resource_constraint`、`loadgen_resource_constraint`、逐轮原始快照与 quiescence 证据。

纠偏后的首个 focused run `29742852766`（`29fc4cff`）在 AOI runner 上固定 service CPU `0`、loadgen CPU `4-7`、loadgen I/O threads 4 和 Gateway `io_cores=4`。实际发出请求的 echo-5K/10K P99、吞吐和两侧 CPU 快照可用于确认资源隔离及共核干扰，但该 run 仍使用旧 pressure client lifecycle，不能证明所有 5K/10K 目标客户端已启动、连接、认证并完成稳态。

候选 `375910f3ecc96bf9c6c4981818a3262a8387d15c` 的 1/2/4 CPU runs `29790072882`、`29791850363`、`29793036782` 曾按旧聚合器得到 `evidence_complete=true`。复核代码后确认旧 pressure client 会在固定总时长结束时强制完成尚未启动或认证的客户端，且 `connected_clients` 不是独立记录的 TCP/authenticated/peak-active 事实。新聚合器会因缺少 `case_manifest` 与完整 lifecycle 拒绝这三份产物；下表仅保留实际发出负载的历史诊断，不能证明目标连接数达到稳态。

| Capacity case | 1 CPU 吞吐 / P99 | 2 CPU 吞吐 / P99 | 4 CPU 吞吐 / P99 | 2/4 CPU speedup |
|---|---:|---:|---:|---:|
| echo-1K | 18228.19 / 1ms | 18231.27 / 1ms | 18226.61 / 1ms | 1.0002x / 0.9999x |
| echo-5K | 57982.04 / 2ms | 57875.15 / 5ms | 58030.58 / 1ms | 0.9982x / 1.0008x |
| echo-10K | 59231.55 / 10ms | 59697.76 / 1ms | 59736.21 / 1ms | 1.0079x / 1.0085x |
| battle-100 | 2773.38 / 10ms | 2780.60 / 10ms | 2778.64 / 5ms | 1.0026x / 1.0019x |
| battle-500 | 6916.11 / 10ms | 6921.77 / 10ms | 6899.14 / 20ms | 1.0008x / 0.9975x |

旧产物中的 0 rejected/failed 和吞吐/P99 只描述实际发出的请求，不能补足未启动、未连接或未认证客户端。它既不能证明 1 CPU 满足原目标，也不能据此宣称线性扩展或调整默认 `io_cores`。

## 候选 `37897e8` 饱和曲线与单变量轴

2026-07-21，分支 `codex/v36-capacity-evidence` 的运行时候选提交 `37897e8f3e2e4d231a1e9736e06709907cfee11b` 完成主线 CI run `29822268701`，并在 AOI Linux/X64 fixed runner、Release、lockfile SHA-256 `cd92f4c0cc579a066cf15cda76b4ffc63ade521bf5477ba6749c6a8187dc97d1` 上完成以下证据。证据采集时该提交尚未合入 `main`，结果不回写 `v3.5.3` tag；随后承载这些结果的最终文档提交仅包含文档变更，不改变运行时证据与 `37897e8` 的绑定。

完整 closed-loop 曲线 run `29822268782` 的 6 个点、每点三轮共 18/18 轮均满足真实客户端 lifecycle、资源窗口和零客户端错误约束，结论为 `knee_found`。客户端采用 one-in-flight 模型，曲线从 5K 到 200K **配置请求率上限**；该上限只是客户端 timer ceiling，不是 open-loop offered QPS。最后一点使用 2,000 个客户端和 10ms 间隔，不是 200K 客户端。

| 配置请求率上限 | 客户端 / 间隔 | 响应率中位数 | P99 | Gateway CPU 配额占用 | Loadgen CPU 配额占用 |
|---:|---:|---:|---:|---:|---:|
| 5K ops/s | 500 / 100ms | 4,979.33 ops/s | 2ms | 9.88% | 9.16% |
| 10K ops/s | 1,000 / 100ms | 9,948.29 ops/s | 5ms | 17.94% | 13.95% |
| 20K ops/s | 1,000 / 50ms | 19,880.59 ops/s | 2ms | 34.25% | 26.35% |
| 50K ops/s | 1,000 / 20ms | 49,765.00 ops/s | 1ms | 47.28% | 34.11% |
| 100K ops/s | 1,000 / 10ms | 99,197.91 ops/s | 1ms | 65.16% | 47.18% |
| 200K ops/s | 2,000 / 10ms | 131,470.36 ops/s | 20ms | 95.98% | 60.37% |

因此后续轴实验固定选用 `echo-sat-c2000-i10-60s`。它是用于比较的饱和膝点，不等于生产容量承诺。两条轴的单点输入按设计不会各自声明完整曲线或独立饱和结论；轴的完成性来自验证完整曲线来源和三轮单变量身份后的聚合产物。以下曲线和轴数值均为三轮中位数，结论只适用于该 runner、affinity 和运行时候选。

Service CPU 轴 runs `29823733478` / `29823736393` / `29823739153` 分别固定 1/2/4 个 service CPU，loadgen 始终为 CPU `4-7`、4 个 I/O 线程，`io_cores=4`。三档均为三轮有效证据，聚合产物 `service-cpu-axis-37897e8.json` 为 `overall_pass=true`、`evidence_complete=true`。

| Service CPU | 响应率中位数 | P99 | Gateway CPU / 配额 | 相对 1 CPU 加速比 |
|---:|---:|---:|---:|---:|
| 1 | 145,292.20 ops/s | 10ms | 94.38% / 94.38% | 1.0000x |
| 2 | 195,749.49 ops/s | 2ms | 173.94% / 86.97% | 1.3473x |
| 4 | 198,267.39 ops/s | 1ms | 204.19% / 51.05% | 1.3646x |

聚合结论为 `partial_cpu_scaling`：2 CPU 已接近该负载的配置上限，4 CPU 相对 2 CPU 只再增加约 1.29%，不能按此曲线宣称线性扩展，也不自动调整部署规格。

`io_cores` 轴 runs `29823742465` / `29823745289` / `29823733478` 分别固定 `io_cores=1/2/4`，service 始终限制为 CPU `0`。三档均为三轮有效证据，聚合产物 `io-cores-axis-37897e8.json` 为 `overall_pass=true`、`evidence_complete=true`。

| `io_cores` | 响应率中位数 | P99 | Gateway CPU 配额占用 | 相对 `io_cores=1` |
|---:|---:|---:|---:|---:|
| 1 | 132,338.66 ops/s | 10ms | 94.37% | 1.0000x |
| 2 | 142,426.29 ops/s | 10ms | 94.38% | 1.0762x |
| 4 | 145,292.20 ops/s | 10ms | 94.38% | 1.0979x |

聚合结论为 `no_material_io_core_gain`，因此保持当前默认值，不根据本次采集自动改配置。

OTel run `29823748288` 使用 fresh Gateway/Battle Backend，对 off/on 各执行三轮 `battle-100-30s`，顶层和绝对门禁均通过。on 相对 off 的吞吐中位数变化为 `+0.103%`，P99 保持 5ms，Gateway CPU 时间中位数无变化，RSS 中位数从 9.53MiB 增至 13.98MiB（`+46.695%`）。on 组 backend routed/enqueued 为 46,069，collector/exported 为 45,824，剩余 245 spans 明确记录为 buffered；179 个 batch 全部成功，invalid payload、HTTP/status error 和 failed batch 均为 0。相对百分比仍按 `observed_not_thresholded` 报告，不在采集后补设回退阈值。

## 历史数据边界

2026-05 的 Windows/MSVC baseline、Windows 高精度定时器优化和本机 DLL 部署记录属于已停止支持平台的历史资料。它们不再出现在当前容量表、待办或部署说明中；原始数据仍可从 `runtime/perf/20260523-165827/` 与 `runtime/perf/p0-*-local/` 审计。

当前 Linux/Conan 构建不存在需要在 Release 目录手动复制 `hiredis.dll` 的步骤，也没有未解决的 `cluster_router_e2e_test.cpp` 编译阻断。若这些问题再次出现，应以新的失败日志和 issue 记录，而不是沿用历史已知问题。

## SLO 解释边界

实验室性能 gate 用于候选间回归比较，不等同于线上 30 天 SLO。可用性、错误预算和告警有效性必须来自生产监控时间序列；一次 baseline 或 soak 不能证明 99.9% 月度可用性。
