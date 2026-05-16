# Windows R1 性能基线结果

> 日期：`2026-05-16`
> 构建目录：`build/windows-msvc-debug`
> 提交：`6a440b4b31948c92ff7018ac4579f9786df1947b`
> 结果来源：`runtime/perf/r1-baseline-long-battle/summary.json`
> 范围：R1 Windows 基线修复后验证，包含长时间 battle workload

## 1. 执行内容

命令：

```bash
python ./scripts/collect_v2_perf_baseline.py \
  --build-dir build/windows-msvc-debug \
  --run-preset baseline \
  --repetitions 1 \
  --output-root runtime/perf/r1-baseline-long-battle
```

场景：

- `echo-100-30s`
- `echo-1000-30s`
- `battle-20-30s`
- `battle-100-30s`

采集链保护：

- `v2_gateway_pressure` 支持 `--output`，结果 JSON 由程序直接落盘。
- collector 对每个 pressure case 设置超时，超时后会清理子进程。
- emergency result 会标记 `forced_timeout=true`，release gate 会判定失败。
- baseline 模式会提升 v2 ingress rate limit，包括 connection、message type、IP、user、login 五类 bucket。
- battle 场景会按 duration / interval 自动设置 `V2_BATTLE_MAX_FRAMES`，本次 baseline 为 `300` 帧。

## 2. 结果

### 2.1 Echo

| 场景 | 吞吐 msg/s | P50 ms | P90 ms | P99 ms | 连接数 | 拒绝数 | 总消息数 | 结果 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `echo-100-30s` | 1545.14 | 2.0 | 5.0 | 5.0 | 100 | 0 | 46815 | 通过 |
| `echo-1000-30s` | 12265.32 | 20.0 | 50.0 | 50.0 | 1000 | 0 | 371293 | 通过 |

### 2.2 Battle

| 场景 | 吞吐 msg/s | P50 ms | P90 ms | P99 ms | 连接数 | 拒绝数 | 总消息数 | 结果 |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `battle-20-30s` | 512.25 | 10.0 | 10.0 | 10.0 | 20 | 0 | 8851 | 通过 |
| `battle-100-30s` | 1979.57 | 50.0 | 50.0 | 100.0 | 100 | 0 | 42237 | 通过 |

解释：

- Echo 已不再被 ingress rate limit 污染。
- Battle 已不再卡住，也不会再产生 `forced_timeout=true` 的结果。
- 当前 battle 数据已经从 3 帧生命周期样本扩展为 300 帧长时间 workload，可用于观察持续战斗流量。后续仍可继续增加 repetitions、房间数和更长 soak。

## 3. Release Gate

`summary.json -> release_gates`：

- `echo-100-30s`：通过
- `echo-1000-30s`：通过
- `battle-20-30s`：通过
- `battle-100-30s`：通过

整体 gate：**通过**

## 4. R1 状态

已完成：

- 跨平台 collector 主入口。
- per-run 输出、diagnostics、进程资源快照、聚合 summary、release gate。
- Windows smoke 与 baseline 验证，且不会卡住终端。
- rate limit 覆盖 connection、message type、IP、user、login。
- 修复 battle pressure 重复 async read 与 bridge 模式 BattleStatePush 消费问题。
- battle 长时间 workload 已接入 `V2_BATTLE_MAX_FRAMES`，collector 会按 case 自动设置最大帧数。

后续跟进：

- 增加 repetitions 和长时间 soak，观察更稳定的 median / max，以及 battle-100 在 P99=100ms 门槛附近的波动。
