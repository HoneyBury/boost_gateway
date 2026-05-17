# v2.0.2 性能基线报告

> 基准版本: `develop` (v2.0.1 + v2.0.2 B1-B3 测量基础设施)
> 测量日期: 2026-05-12
> 测量工具: `v2_gateway_pressure` (新增), `LatencyHistogram`, `ThroughputTracker`
>
> `R1` 正式 Windows baseline 结果与 gate 判定见：
> [Windows R1 基线结果](./performance-baseline-windows-r1.md)
>
> `2026-05-16` 之后，R1 collector 已补齐两项关键修复：
> - baseline 模式下通过环境变量放宽 v2 runtime ingress rate limit，避免 echo 吞吐被默认保护阈值污染
> - `battle` baseline 支持按房间分组并行生成持续战斗流量，不再把所有客户端压到单房路径

## 1. 测量方法

### 1.1 测量工具

| 组件 | 位置 | 用途 |
|---|---|---|
| `v2_gateway_pressure` | `examples/v2_gateway_pressure/` | 多客户端并发负载生成器，支持 echo/battle/stability 场景 |
| `LatencyHistogram` | `include/v2/benchmark/latency_histogram.h` | 指数分桶延迟直方图，14 桶 (1ms→30s) |
| `ThroughputTracker` | `include/v2/benchmark/throughput_tracker.h` | 滑动窗口吞吐量计数器 (5s 窗口, 10 子桶) |
| `BackendMetrics::record_latency()` | `include/v2/gateway/backend_metrics.h` | 服务端后端路由延迟记录 |
| `DiagnosticsSnapshot` | `include/v2/diagnostics/diagnostics_manager.h` | 聚合诊断快照 (含 `messages_per_second`) |

### 1.2 拓扑

```
v2_gateway_pressure ──TCP──▶ v2_gateway_demo (:9201)
                                   │
                                   ├──▶ v2_login_backend (:9202)
                                   ├──▶ v2_room_backend  (:9302)
                                   └──▶ v2_battle_backend (:9303)
```

### 1.3 测量环境

- **CPU**: Apple M-series (macOS), 性能核心 × N
- **内存**: ≥ 16 GB
- **OS**: macOS 26 / Linux (内核 ≥ 5.15)
- **编译器**: Clang 17+ / GCC 13+, C++20, `-O2` (Release)
- **Boost**: 1.90+
- **构建配置**: `cmake --preset release`

### 1.4 运行基准测试

```bash
# 1. 编译 release 构建
cmake --preset release && cmake --build --preset release

# 2. 启动 v2 后端服务（3 个终端）
./build/release/examples/v2_login_backend/v2_login_backend
./build/release/examples/v2_room_backend/v2_room_backend
./build/release/examples/v2_battle_backend/v2_battle_backend

# 3. 启动 gateway
./build/release/examples/v2_gateway_demo/v2_gateway_demo \
    --io-cores 4 --login-port 9202 --room-port 9302 --battle-port 9303

# 4. 运行基准
./build/release/examples/v2_gateway_pressure/v2_gateway_pressure \
    --scenario echo --clients 1000 --duration 30 --port 9201

# 5. 查看吞吐/延迟 JSON 输出 → 填入下方表格
```

### 1.5 标准采集入口

从 `v3.x` 生产就绪阶段开始，跨平台基线采集统一通过：

```bash
python ./scripts/collect_v2_perf_baseline.py \
  --build-dir ./build/release \
  --run-preset smoke
```

或完整基线：

```bash
python ./scripts/collect_v2_perf_baseline.py \
  --build-dir ./build/release \
  --run-preset baseline \
  --repetitions 3
```

容量专项（固定机器执行，覆盖 5K/10K echo 与 500 battle）：

```bash
python ./scripts/collect_v2_perf_baseline.py \
  --build-dir ./build/release \
  --run-preset capacity \
  --repetitions 3
```

发布候选的聚合入口会同时执行 R4 release contract 与多进程性能基线：

```bash
python ./scripts/collect_release_baseline.py \
  --build-dir ./build/release \
  --configuration Release \
  --perf-preset baseline \
  --perf-repetitions 3
```

Windows 也可通过 PowerShell 包装器调用同一份 Python 主逻辑：

```powershell
pwsh ./scripts/collect_v2_perf_baseline.ps1 `
  -BuildDir D:\Program\boost-github\BoostAsioDemo\build\windows-ninja-release `
  -RunPreset baseline `
  -Repetitions 3
```

脚本职责：

- 启动 `v2_login_backend` / `v2_room_backend` / `v2_battle_backend` / `v2_gateway_demo`
- 运行标准 `echo` / `battle` 压测场景；`capacity` profile 额外覆盖 5K/10K 连接容量样本
- 抓取 `GET /metrics/diagnostics/json`
- 记录进程资源快照
- 记录空载与每个 case 后的进程资源快照
- 记录 `git commit`、平台、构建目录、重复次数等元数据
- 对同一 case 输出 `min / median / max` 聚合结果
- 输出 `resource_analysis`，按 case/service 聚合 RSS、fd/handles、线程、CPU 快照和每连接边际成本
- 输出 `release_gates` 判定结果，作为 `R1-4` 的自动化基础
- 将结果落盘到 `runtime/perf/<timestamp>/`
- 同步生成 `report.md`，包含 release gate、case 聚合和 gateway 资源聚合，便于直接归档到 release evidence

当前主入口已经切换为 Python，目标是统一 Windows / Ubuntu / macOS 的采集流程；平台差异仅保留在进程资源快照实现上。

### 1.6 输出产物结构

每次采集输出目录固定包含：

| 产物 | 说明 |
|---|---|
| `summary.json` | 机器可读事实源，包含原始 case、聚合结果、release gates、资源分析和进程快照 |
| `report.md` | 人工可读性能报告，可直接贴入发布记录或 GitHub Step Summary |
| `results/*.result.json` | 每次压测工具输出的原始 JSON |
| `results/*.gateway.diagnostics.json` | 每次 case 后抓取的 gateway diagnostics 快照 |
| `logs/*.stdout.log` / `logs/*.stderr.log` | gateway/backend 进程日志 |

`resource_analysis.case_aggregates` 按 case 聚合每个服务的资源指标：

- `working_set_mb` / `working_set_mb_delta`
- `handles` / `handles_delta`，在 Linux/macOS 表示 fd/open files，Windows 表示 process handles
- `threads` / `threads_delta`
- `cpu_percent`，来自系统快照
- `cpu_percent_from_cpu_seconds`，按采样前后 CPU 时间差估算
- `rss_kb_per_connected_client` 与 `handles_per_connected_client`

`collect_release_baseline.py` 在启用性能采集时会把 `performance_summary_path` 和 `performance_report_path` 写入 release summary，方便从 `runtime/validation/release-baseline-summary.json` 直接追溯性能证据。

### 1.7 P1 性能优化实验口径

P1 阶段优先保证默认基线稳定，不把实验性优化直接推入默认路径。`collect_v2_perf_baseline.py` 支持：

```bash
python ./scripts/collect_v2_perf_baseline.py \
  --build-dir ./build/release \
  --run-preset baseline \
  --repetitions 1 \
  --backend-pool-size 1
```

`--backend-pool-size` 会设置 gateway 进程的 `V2_BACKEND_CONNECTION_POOL_SIZE` 并写入 `summary.json.topology.backend_connection_pool_size` 与 `report.md`。当前稳定默认值为 `1`；显式放大连接池属于性能实验项，必须单独记录报告，不得作为 release baseline 默认值。

本机 P1 实验结论：

- `backend_pool_size=1`：baseline 单轮通过，`echo-1000` p99 20ms，`battle-100` p99 200ms，rejected/failed/forced_timeout 均为 0。
- `backend_pool_size=8`：smoke 中 battle 场景出现 backend_error/rejected，说明多连接池会放大当前 backend connection 生命周期和熔断交互的波动，不进入默认优化。

### 1.8 首轮 smoke 数据（2026-05-16，Windows）

首轮基于 `python ./scripts/collect_v2_perf_baseline.py --build-dir build/windows-ninja-release --run-preset smoke`
得到的结果如下，原始产物保存在：

- `runtime/perf/20260516-015931/`：修正 echo 统计口径后的首轮有效 smoke
- `runtime/perf/20260516-020616/`：battle smoke 已推进到 `BattleStartRequest` / `BattleStatePush`
- `runtime/perf/20260516-022121/`：battle smoke 已完成 3 帧推进与结算收口

| 场景 | 结果 | 说明 |
|---|---|---|
| `echo-20-10s` | 20 客户端，3095 消息，309.36 msg/s，P99 2.0ms | 说明跨平台采集脚本、Windows Release 构建、gateway/login 主链和统计口径已经打通 |
| `battle-2-10s` | 2 客户端，3 条有效消息，完整推进 3 帧并收到 `battle_finished`，P99 2.0ms | 说明房间/开战/battle backend 路由、bridge 模式重连与 battle smoke 状态机已经打通；下一步可继续扩展到更长时长和更高并发 |

当前结论：

- `echo` smoke 已可作为 R1 的最小可用基线样本。
- `battle` smoke 已从“完全失败”推进到“可开战、可推进帧、可结算结束”，可以作为 R1 的首个 battle 级 smoke 样本；
  下一步应继续提升时长、并发和每局消息量，再进入 baseline 场景。

---

## 2. 吞吐量基线 (B2)

### 2.1 Echo 吞吐量 vs 核心数

| 核心数 | 并发连接 | 持续时间 | 总消息数 | 吞吐量 (msg/s) | 线性扩容系数 |
|---|---|---|---|---|---|
| 1 | 100 | 30s | _待测定_ | _待测定_ | 1.00× |
| 2 | 100 | 30s | _待测定_ | _待测定_ | _待测定_ |
| 4 | 100 | 30s | 55,298 | 1,836.23 | _待测定_ |
| 1 | 1000 | 30s | _待测定_ | _待测定_ | — |
| 2 | 1000 | 30s | _待测定_ | _待测定_ | _待测定_ |
| 4 | 1000 | 30s | 535,536 | 17,802.35 | _待测定_ |
| 4 | 10000 | 30s | 694,193 | 23,072.81 | — |

> `P1` 状态：macOS Release baseline 三轮已通过，结果来自
> `runtime/perf/release-baseline/summary.json`；10K 行来自容量专项
> `runtime/perf/p1-capacity-local/summary.json`，该专项出现 8,701 个连接失败，
> 只作为退化点记录，不作为生产通过线。

**线性扩容系数** = `吞吐量(N核) / (N × 吞吐量(1核))`

### 2.2 战斗广播吞吐量

| 房间数 | 每房间人数 | 输入间隔 | 总消息数 | 吞吐量 (msg/s) | 广播 fan-out 系数 |
|---|---|---|---|---|---|
| 10 | 2 | 100ms | 8,855 | 542.91 | _待测定_ |
| 50 | 2 | 100ms | 39,876 | 1,812.63 | _待测定_ |
| 250 | 2 | 100ms | 34,773 | 1,158.37 | _待测定_ |

> `P1` 状态：`battle-20` 与 `battle-100` 三轮 baseline 通过；`battle-500`
> 容量专项实际连接 361/500、rejected=139、P99=500ms，记录为当前退化点。

**广播 fan-out 系数** = `(总出站消息) / (总入站消息)`

### 2.3 消息吞吐量上限

| 场景 | 峰值吞吐量 (msg/s) | 瓶颈组件 | 备注 |
|---|---|---|---|
| 纯 echo | 23,952 msg/s | 5K/10K 连接建立失败 | capacity 单轮 echo-5000 吞吐峰值，但 failed=3,653 |
| 战斗广播 | 1,812 msg/s | battle-500 rejected 与 P99 退化 | baseline battle-100 通过，capacity battle-500 失败 |
| 稳定性浸泡 | _待测定_ | _待测定_ | 8 小时运行 |

---

## 3. 延迟基线 (B3)

### 3.1 Echo 端到端延迟 (客户端视角)

| 核心数 | 并发连接 | P50 (ms) | P90 (ms) | P99 (ms) | Min (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| 1 | 100 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 1 | 1000 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 4 | 100 | 5 | 5 | 10 | _待测定_ | _待测定_ |
| 4 | 1000 | 5 | 5 | 20 | _待测定_ | _待测定_ |
| 4 | 10000 | 2 | 5 | 20 | _待测定_ | _待测定_ |

### 3.2 网关→后端延迟 (服务端 `BackendMetrics` 视角)

| 后端 | 请求数 | 成功数 | P50 (us) | P99 (us) | 超时数 | 错误数 |
|---|---|---|---|---|---|---|
| login | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| room | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| battle | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |

**测量方式**: `GatewayServiceBridge::route()` 中 `send_request()` 前后打点，记录到 `BackendMetrics::record_latency()`。通过 `GET /metrics/diagnostics/json` 获取。

> `R1-2` 状态：采集脚本已保留每次运行的 diagnostics JSON，且
> `DemoServer::diagnostics_json()` 已补 `avg_latency_us` / `latency_sample_count`；
> 下一步从 baseline 目录提取并回填。

### 3.3 战斗输入广播延迟

| 房间数 | 输入到广播 P50 (ms) | 输入到广播 P99 (ms) | 备注 |
|---|---|---|---|
| 10 | 5 | 20 | `battle-20-30s` baseline |
| 50 | 50 | 200 | `battle-100-30s` baseline |
| 250 | 100 | 500 | `battle-500-30s` capacity，失败专项 |

---

## 4. 资源基线 (B4)

### 4.1 空载资源用量

| 指标 | gateway | login backend | room backend | battle backend |
|---|---|---|---|---|
| RSS (MB) | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 虚拟内存 (MB) | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 文件描述符 | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 线程数 | _待测定_ | _待测定_ | _待测定_ | _待测定_ |

### 4.2 负载资源用量

| 负载场景 | 进程 | RSS 峰值 (MB) | CPU (%) | fd 峰值 | 备注 |
|---|---|---|---|---|---|
| 1K 空闲连接 | gateway | _待测定_ | _待测定_ | _待测定_ | 仅 accept，无消息 |
| 1K echo | gateway | 20.42 | 21.8 | 37 | baseline 三轮 |
| 10K echo | gateway | 34.45 | 32.1 | 31 | capacity 单轮，failed=8,701 |
| 100 战斗房间 | gateway | 41.97 | 10.2 | 409 | capacity battle-500，实际 361/500 连接 |

### 4.3 每连接边际成本

| 指标 | 每连接增量 | 计算方式 |
|---|---|---|
| RSS | 16.876 KB | baseline echo-1000 RSS delta / connected clients |
| fd | 0.016 | baseline echo-1000 fd delta / connected clients |
| CPU (负载) | 68.66% | baseline echo-1000 CPU seconds delta estimate |

---

## 5. SLO/SLI 定义 (B5)

### 5.1 服务等级目标 (SLO)

| SLO | 目标值 | SLI 测量方式 | 测量窗口 |
|---|---|---|---|
| **可用性** | 99.9% | `success / total_requests` (BackendMetrics) | 30 天滚动 |
| **延迟** | P99 ≤ 50ms (端到端 echo) | `LatencyHistogram::p99_ms` | 5 分钟滚动 |
| **网关→后端延迟** | P99 ≤ 10ms | `BackendMetrics::avg_latency_us` | 5 分钟滚动 |
| **错误率** | ≤ 0.1% | `errors / total_requests` | 30 天滚动 |
| **吞吐量 (单核)** | ≥ 10K msg/s | `ThroughputTracker::rate_per_second` | 1 分钟滚动 |

### 5.2 烧录率告警阈值

基于 [Google SRE Workbook](https://sre.google/workbook/alerting-on-slos/) 的 2%/5% 烧录率阈值：

| 告警级别 | 烧录率 | 触发条件 | 通知方式 |
|---|---|---|---|
| **Warning** | 2% (14.4×) | 1h 窗口内错误消耗 2% 预算 | 日志 + 指标 |
| **Critical** | 5% (36×) | 6h 窗口内错误消耗 5% 预算 | PagerDuty/钉钉 |

**错误预算** = 30 天 × (1 − 0.999) = 43.2 分钟/月

### 5.3 错误预算策略

| 预算消耗 | 动作 |
|---|---|
| < 50% | 正常发布节奏 |
| 50%–80% | 冻结非紧急发布，优先修复 |
| 80%–100% | 冻结全部发布，全力修复 |
| 耗尽 | 启动事后复盘，下月发布需 VP 审批 |

### 5.4 当前 R1 门槛执行状态

| 检查项 | 当前状态 |
|---|---|
| `echo` smoke | 已通过（Windows） |
| `battle` smoke | 已通过（Windows，2 客户端 / 3 帧 / 结算结束） |
| baseline 矩阵 | 已通过，`runtime/perf/release-baseline/summary.json` |
| 自动聚合结果 | 已支持（`case_aggregates`） |
| 自动门槛判定 | 已执行，baseline `release_gates.overall_pass=true` |

---

## 6. 容量规划 (B6)

### 6.1 单实例连接上限

基于 B4 资源基线数据：

| 配置 | 最大连接数 | 瓶颈资源 | 备注 |
|---|---|---|---|
| 4 核, 8 GB | _待测定_ | _待测定_ | — |
| 8 核, 16 GB | _待测定_ | _待测定_ | — |
| 16 核, 32 GB | _待测定_ | _待测定_ | — |

### 6.2 扩容公式

```
所需实例数 = ceil(峰值并发连接 / 单实例连接上限 × 安全系数(1.3))
所需核心数 = 所需实例数 × 每实例核心数
```

### 6.3 硬件推荐

| 在线玩家规模 | 并发连接估计 | 推荐 gateway 配置 | 推荐后端配置 |
|---|---|---|---|
| 1K | ~1K | 2 核, 4 GB × 1 | 2 核, 4 GB × 1 组 |
| 10K | ~10K | 4 核, 8 GB × 1 | 4 核, 8 GB × 1 组 |
| 50K | ~50K | 8 核, 16 GB × 2 | 8 核, 16 GB × 2 组 |
| 100K | ~100K | 8 核, 16 GB × 4 | 8 核, 16 GB × 4 组 |

> **连接数估算**: 在线玩家 × 1.0 (移动端常驻连接) +
> 在线玩家 × 0.2 (WebSocket 备用线路) +
> 在线玩家 × 0.05 (观测/管理连接)

### 6.4 性能优化方向

基于基线数据的优化优先级：

| 优先级 | 方向 | 触发条件 | 预期收益 |
|---|---|---|---|
| P1 | 零拷贝路径优化 | 延迟 P99 > 50ms | −30% 延迟 |
| P2 | SO_REUSEPORT 多核 | 线性扩容系数 < 0.7 | +40% 吞吐量 |
| P3 | 内存池扩容 | RSS 超预期 20% | −15% 内存 |
| P4 | 连接复用 | fd 超 fd_limit 80% | −50% fd |

---

## 7. 基准数据采集清单

> 从 `v3.x` 生产就绪阶段开始，建议优先使用 `scripts/collect_v2_perf_baseline.py` 生成统一目录结构和结果文件；手工命令主要用于调试或补充单项数据。

以下命令需要在 Release 构建下运行，结果填入上表：

### 7.1 基础设施验证状态 (2026-05-12)

| 组件 | 状态 | 说明 |
|------|------|------|
| v2_gateway_pressure | ✅ 可用 | Debug/Release 均编译通过，echo/battle/stability 场景就绪 |
| LatencyHistogram | ✅ 可用 | 14 桶指数分桶，P50/P90/P99 计算正确，7 单元测试通过 |
| ThroughputTracker | ✅ 可用 | 5s 滑动窗口，rate_per_second 正确，6 单元测试通过 |
| BackendMetrics | ✅ 可用 | per-service 请求/成功/超时/错误/延迟计数 |
| DiagnosticsSnapshot | ✅ 可用 | JSON 格式，含 messages_per_second |
| gateway 独立启动 | ✅ 可用 | `v2_gateway_demo --io-cores N --management-port 9080` |
| 4 进程拓扑 | ⚠️ 待验证 | backend 服务需独立启动并按顺序编排 |

> **注意**: 性能数据采集需要在受控环境下进行（独占机器、关闭无关进程、预热后采集）。以下命令已验证可执行，但实际数据待填入上表。

```bash
# —— Echo 吞吐量 vs 核心数 ——
for cores in 1 2 4; do
  for clients in 100 1000; do
    ./build/release/examples/v2_gateway_pressure/v2_gateway_pressure \
      --scenario echo --clients $clients --duration 30 --port 9201 \
      | tee "results/echo_c${cores}_cli${clients}.json"
  done
done

# —— 战斗广播吞吐量 ——
for rooms in 10 50 100; do
  ./build/release/examples/v2_gateway_pressure/v2_gateway_pressure \
    --scenario battle --clients $((rooms * 2)) --duration 30 --port 9201 \
    | tee "results/battle_r${rooms}.json"
done

# —— 资源采样 (macOS) ——
# 在负载运行期间，每 5 秒采样
while true; do
  ps -o pid,rss,vsize,%cpu -p $(pgrep v2_gateway_demo) | tail -1
  sleep 5
done

# —— 服务端延迟 (通过 HTTP 诊断口) ——
curl -s http://localhost:9202/metrics/diagnostics/json | jq '.backends[].metrics'
```
