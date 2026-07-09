# v2.0.2 性能基线报告 — Windows Release P0 实测版

更新时间：2026-05-28

## 工程效率与固定 Runner 口径

- PR/CI 默认仍以 bounded smoke 和多平台快速反馈为主。
- `ci.yml`、`perf-commit-check.yml`、`release.yml`、`nightly-stability.yml`、`perf-regression.yml` 已接入 `sccache` 与 `actions/cache`，用于降低 configure/build/test 等待时间。
- Conan 2 依赖治理已从 PoC 阶段提升为主线默认依赖路径（`BOOST_USE_CONAN_DEPS=ON`）；性能基线结论以 Conan 构建链为准，缺失 Conan 时会自动回退到 FetchContent/third_party。
- Conan 收口入口现已统一到 `conan/README.md`、仓库内 profile 和 `scripts/bootstrap_conan.py`；后续 fixed-runner / CI 需继续补 lockfile 与 cache key 量化。
- 本机 Windows/macOS baseline 继续作为开发回归参考。
- 最终容量、2h/8h soak、business-capacity 和 release/capacity 投产口径应优先以 Ubuntu fixed-runner summary 为准，而不是本机短样本。

### P1 Conan 依赖治理验证（已升级为主线默认路径）

**验证时间**: 2026-07-08
**验证环境**: Orbstack Docker, `--platform linux/amd64`, Ubuntu 24.04, GCC 13.3.0
**验证目的**: 证明 `conan/locks/linux-gcc-x64-release-nogrpc-nosqlite.lock` 可在 Ubuntu x86_64 环境中成功安装并用于项目构建。

**Conan lockfile 安装结果 (`conan install`)**:

| 依赖 | 版本 | 构建状态 |
|------|------|---------|
| zlib | 1.3.1 | header-only, 无需编译 |
| bzip2 | 1.0.8 | 从源码构建 |
| fmt | 11.1.0 | 静态库 (`-o fmt/*:header_only=False`) |
| spdlog | 1.15.2 | 从源码构建 |
| nlohmann_json | 3.11.3 | header-only |
| OpenSSL | 3.3.2 | 从源码构建 |
| hiredis | 1.2.0 | 从源码构建 |
| GTest | 1.16.0 | 从源码构建 |
| Boost | 1.86.0 | 从源码构建（46 静态库） |

**`project_v2` 构建结果**: `[77/77] Linking CXX static library src/v2/libproject_v2.a` ✅

**治理脚本结果**:
- `check_conan_lockfile_workflows.py` — PASS (27/27 checks)
- `check_fixed_runner_evidence_plan.py` — 40/44 checks (4 项因 `production-candidate-evidence-manifest.json` 尚未落仓而预期失败)
- `check_fixed_runner_environment.py` — `fixed runner preflight passed`

**已知问题**: OpenSSL/3.3.2 在 x86_64 模拟环境下构建依赖 `perl` 完整安装包（`FindBin.pm`）；已在 `Dockerfile.conan` 中固定包含 `perl`。

**下一步**: 
- 将 lockfile 验证整合到 CI 常规流程中 ✅ CI 中已运行 conan install 与 lockfile
- 锁定 Conan 版本至 2.8.x 以避免新版 CMakeDeps 生成的兼容性问题（fmt header-only 依赖需 `-o fmt/*:header_only=False` 做静态编译）✅ conanfile.py + 所有 workflow + Dockerfile 已锁定 `>=2.0,<2.9`
- 落仓 `production-candidate-evidence-manifest.json` 以通过全部 44 项门禁 ✅ 已就位于 `docs/production/`

### 构建时间基线 (S3)

当前 CI 使用 CMake preset + Ninja 构建系统，5 个 workflow 已启用 sccache，配置如下：

| 工作流 | CMake Preset | 编译器 | sccache | 缓存键模式 | 备注 |
|--------|-------------|--------|---------|-----------|------|
| `ci.yml` | `release` | GCC 12 | 是 | `sccache-Linux-release-` | 全量单元测试 + 多个治理门禁；条件性启用 sccache |
| `release.yml` | `release` | GCC 12 | 是 | `sccache-Linux-release-`（与 ci.yml 同键） | 发布包构建 + 合约门禁 |
| `perf-commit-check.yml` | `release` | GCC 12 | 是 | `sccache-Linux-perf-release-` | 性能冒烟 + 基线对比 |
| `nightly-stability.yml` | `default` / `release` | GCC 12 (Linux), AppleClang | 是（Linux） | `sccache-Linux-nightly-` | 夜间稳定性浸泡 |
| `perf-regression.yml` | `release` | GCC 12 | 是 | `sccache-Linux-perf-regression-` | 多轮回归基线 |

**sccache 缓存键策略:**

- `ci.yml` 与 `release.yml` 共享精确缓存键 `sccache-${{ runner.os }}-release-${{ hashFiles('CMakeLists.txt', 'CMakePresets.json', 'cmake/**', 'sdk/CMakeLists.txt', 'proto/CMakeLists.txt') }}`，两者之间可复现 exact-match 缓存命中。
- 其余 workflow 使用独立缓存键分段（`perf-release`、`perf-regression`、`nightly`），通过 `sccache-${{ runner.os }}-` fallback restore key 跨 workflow 共享。
- hashFiles 输入因 workflow 而异：ci/release/perf-commit-check 使用 5 个文件/目录模式；nightly/perf-regression 使用 3 个。
- 所有 workflow 的 `restore-keys` 均以 `sccache-${{ runner.os }}-` 结尾，允许精确键未命中时回退到同一 OS 的任意历史缓存。

**构建时间测量方法:**

- 每次 CI 运行在 `Build` 步骤前记录 `BUILD_START_EPOCH`，在 `Show sccache stats` 后计算持续时长，输出到 `runtime/perf/build-times/build-time.json`。
- 同时以 JSON 格式归档 sccache 统计数据至 `runtime/perf/build-times/sccache-stats.json`（包含缓存命中/未命中次数、缓存大小等）。
- 数据随 workflow artifact 一起上传，可在 CI 运行完成后下载分析。
- 基线格式与字段说明见 `runtime/perf/build-times/BASELINE_TEMPLATE.json`。

> **当前状态**: 构建时间数据采集基础设施已就位。5 个 sccache workflow 每次 CI 运行自动归档 `build-time.json` + `sccache-stats.json` 至 `runtime/perf/build-times/`。cold/warm 数据在 CI 实际运行后自动积累，待多轮数据后可汇总分析。

> **基线版本**: `8387a13dcb` (P0 优化收束)
> **测量日期**: 2026-05-23
> **测量环境**:
>   - OS: Windows 11 Pro 10.0.26200
>   - CPU: 4 核 (io-cores=4)
>   - 构建：`build/Release` (MSVC Release, `/O2`)
>   - 后端连接池: 8 (通过 `V2_BACKEND_CONNECTION_POOL_SIZE`)
>   - 工具: `scripts/collect_v2_perf_baseline.py --run-preset baseline --repetitions 3 --backend-pool-size 8`
>   - 产物原始数据: `runtime/perf/20260523-165827/summary.json`

## 状态总览

| 维度 | 状态 |
|------|------|
| echo-100/1000 baseline | **已测定** (P99 1ms/5ms, 吞吐 1,945/17,846 msg/s) |
| battle-20/100 baseline | **已测定** (P99 10ms/200ms, 吞吐 553/1,424 msg/s) |
| Release gates | **PASS** (4/4 gates, overall_pass=true) |
| 后端延迟 (login/room/battle) | **已测定** (avg 2.3-3.7ms, P99 5ms) |
| echo-5000/10000 大连接 | **已测定** (P0 capacity, P99 20ms/50ms, 0 failed) |
| battle-500 容量 | **已测定** (P0 business-capacity, P99 400ms) |
| 1/2 核线性扩容 | **待固定 runner 实测** |
| 2h/8h 稳定性浸泡 | **待固定 runner 实测** |
| TLS on/off 损耗 | **待固定 runner 实测** |
| OTel export 损耗 | **待固定 runner 实测** |
| matchmaking/leaderboard 专项 | **待测定** |

---

## 1. 测量方法

### 1.1 测量工具

| 组件 | 位置 | 用途 |
|---|---|---|
| `v2_gateway_pressure` | `examples/v2_gateway_pressure/` | 多客户端并发负载生成器，支持 echo/battle/stability 场景 |
| `LatencyHistogram` | `include/v2/benchmark/latency_histogram.h` | 指数分桶延迟直方图，20 桶 (1ms→30s) |
| `ThroughputTracker` | `include/v2/benchmark/throughput_tracker.h` | 滑动窗口吞吐量计数器 (5s 窗口, 10 子桶) |
| `BackendMetrics::record_latency()` | `include/v2/gateway/backend_metrics.h` | 服务端后端路由延迟记录 |
| `DiagnosticsSnapshot` | `include/v2/diagnostics/diagnostics_manager.h` | 聚合诊断快照 (含 `messages_per_second`) |

### 1.2 拓扑

```
v2_gateway_pressure ──TCP── v2_gateway_demo (:9201)
                                   │
                                   ├── v2_login_backend (:9202)
                                   ├── v2_room_backend  (:9302)
                                   ├── v2_battle_backend (:9303)
                                   ├── v2_match_backend (:9304)
                                   └── v2_leaderboard_backend (:9305)
```

### 1.3 标准采集命令

完整基线（3 轮重复）：
```bash
python scripts/collect_v2_perf_baseline.py ^
  --build-dir build/Release ^
  --run-preset baseline ^
  --repetitions 3 ^
  --backend-pool-size 8
```

容量专项（5K/10K echo, 500 battle）：
```bash
python scripts/collect_v2_perf_baseline.py ^
  --build-dir build/Release ^
  --run-preset capacity ^
  --repetitions 3 ^
  --backend-pool-size 8
```

业务闭环性能：
```bash
python scripts/collect_v2_perf_baseline.py ^
  --build-dir build/Release ^
  --run-preset business-capacity ^
  --repetitions 3 ^
  --include-business-flow ^
  --business-flow-clients 3
```

快速冒烟验证：
```bash
python scripts/collect_v2_perf_baseline.py ^
  --build-dir build/Release ^
  --run-preset smoke
```

### 1.4 输出产物结构

| 产物 | 说明 |
|---|---|
| `summary.json` | 机器可读事实源，包含原始 case、聚合结果、release gates、资源分析和进程快照 |
| `report.md` | 人工可读性能报告 |
| `results/*.result.json` | 每次压测工具输出的原始 JSON |
| `results/*.gateway.diagnostics.json` | 每次 case 后抓取的 gateway diagnostics 快照 |
| `logs/*.stdout.log` / `logs/*.stderr.log` | gateway/backend 进程日志 |

---

## 2. 已测定数据（Windows Release P0 Baseline）

### 2.1 综合结果

| Case | P99 | 吞吐量 (msg/s) | 总消息 | 失败 | 拒绝 | 通过门禁 |
|------|-----|----------------|--------|------|------|---------|
| echo-100-30s | **1ms** | **1,945/s** | 58,443 | 0 | 0 | PASS |
| echo-1000-30s | **5ms** | **17,846/s** | 537,194 | 0 | 0 | PASS |
| battle-20-30s | **10ms** | **553/s** | 8,898 | 0 | 0 | PASS |
| battle-100-30s | **200ms** | **1,424/s** | 42,864 | 0 | 0 | PASS |

**Release Gates 判定**: **overall_pass=true** (无 warning)

Echo 场景 P99 均值为 1-5ms，远低于 50ms gate；battle 场景满足各自的 P99 gate（20人≤100ms, 100人≤250ms）。

### 2.2 Echo 吞吐量

| 核心数 | 并发连接 | 持续时间 | 总消息数(中位数) | 吞吐量 (msg/s, 中位数) | 数据来源 |
|--------|---------|---------|-----------------|----------------------|---------|
| 4 | 100 | 30s | 58,443 | **1,945** | Windows Release 实测 |
| 4 | 1000 | 30s | 537,194 | **17,846** | Windows Release 实测 |
| 1 | 100 | 30s | ~14,600 (extrapolated) | ~486 (extrapolated) | 待固定 runner 实测 |
| 2 | 100 | 30s | ~29,200 (extrapolated) | ~973 (extrapolated) | 待固定 runner 实测 |

> **注意**: 1/2 核值是根据 4 核实测值按线性扩容系数 ~0.95 推算的。1/2 核真实基线需要固定 runner 分别设置 `--io-cores 1/2` 后验证。

### 2.3 Battle 广播吞吐量

| 房间组 | 每房间人数 | 输入间隔 | 总消息数(中位数) | 吞吐量 (msg/s) | 数据来源 |
|--------|-----------|---------|-----------------|----------------|---------|
| 10 rooms x 2 players | 2 | 100ms | 8,898 | **553** | battle-20-30s Windows 实测 |
| 50 rooms x 2 players | 2 | 100ms | 42,864 | **1,424** | battle-100-30s Windows 实测 |

### 2.4 Echo 端到端延迟（4 核）

| 并发连接 | P50 (ms) | P90 (ms) | P99 (ms) | Min (ms) | Max (ms) |
|---------|---------|---------|---------|---------|---------|
| 100 | 1 | 1 | **1** | 0.5 | 3.5 |
| 1000 | 1 | 2 | **5** | 0.5 | 7.5 |

### 2.5 Battle 端到端延迟

| 场景 | P50 (ms) | P90 (ms) | P99 (ms) | Min (ms) | Max (ms) |
|------|---------|---------|---------|---------|---------|
| battle-20-30s | 5 | 5 | **10** | 1.5 | 25 |
| battle-100-30s | 100 | 100 | **200** | 15 | 350 |

> **battle-100 尾部抖动**: 约 1.5% 消息落在 200-300ms 区间，由 actor 线程广播推送造成。run3 的 P99 为 400ms（相较于 run1/run2 的 200ms），说明在多轮重复下尾部延迟存在波动。后续可通过广播推送卸载到工作线程进一步优化。

### 2.6 后端延迟（服务端视角）

| Service | Avg | P50 | P90 | P99 | 请求数 | 错误 | 超时 |
|---------|-----|-----|-----|-----|--------|------|------|
| login | **2.8ms** | 5ms | 5ms | 5ms | 3,660 | 0 | 0 |
| room | **3.7ms** | 5ms | 5ms | 5ms | 900 | 0 | 0 |
| battle | **2.3ms** | 2ms | 5ms | 5ms | 50,251 | 0 | 0 |
| matchmaking | _待业务流量_ | - | - | - | - | - | - |
| leaderboard | _待业务流量_ | - | - | - | - | - | - |

> **说明**: login P50 > avg 的原因是大多数请求分布在 1-2ms 桶中，但少量分布在 2-5ms 桶，导致均值低于中位数所在的桶边界。battle 后端因为直连端口和帧同步模式延迟最低。

### 2.7 内存成本

| Case | Gateway RSS | Delta | KB/连接 | 线程 | Handles |
|------|------------|-------|---------|------|---------|
| idle | 9.2 MB | - | - | 21 | 119 |
| echo-100 | 10.0 MB | 0.8 MB | **8.4** | 20 | 127-137 |
| echo-1000 | 12.6 MB | 3.4 MB | **3.5** | 22-24 | 143-153 |
| battle-20 | 12.8 MB | 3.6 MB | **185** | 27-30 | 178-193 |
| battle-100 | 13.4 MB | 4.2 MB | **42.5** | 31-35 | 198-208 |

> KB/连接: (RSS_delta_MB × 1024) / connected_clients

Echo 场景每连接边际成本仅 3.5-8.4 KB，适合大规模连接场景。Battle 场景每连接成本较高（42-185 KB）因为需要维护房间状态、战斗帧和广播推送。

### 2.8 空载资源用量（6 进程拓扑）

| 进程 | RSS (MB) | Virtual (MB) | Handles | 线程 |
|------|---------|-------------|---------|------|
| v2_login_backend | 7.7 | 4,191 | 87 | 5 |
| v2_room_backend | 7.7 | 4,192 | 88 | 6 |
| v2_battle_backend | 7.8 | 4,192 | 87 | 5 |
| v2_match_backend | 7.8 | 4,192 | 88 | 6 |
| v2_leaderboard_backend | 7.8 | 4,191 | 87 | 5 |
| v2_gateway_demo | 9.2 | 4,209 | 119 | 21 |

---

## 3. P0 Capacity 容量实测（已测定）

2026-05-18 本机 capacity 三轮实测结果：

| 场景 | 连接 | P99 | 吞吐量 | failed | rejected | 状态 |
|------|------|-----|--------|--------|----------|------|
| echo-5000-30s | 5,000 | **20ms** | 55,020 msg/s | 0 | 0 | PASS |
| echo-10000-30s | 10,000 | **50ms** | 49,588 msg/s | 0 | 0 | PASS（贴近 50ms gate）|
| battle-500-30s | 500 | **400ms** | 977 msg/s | 0 | 0 | PASS（贴近 500ms gate）|

> 详细数据见 `runtime/perf/p0-capacity-local/` 和 `runtime/perf/p0-business-capacity-local-r2/`。
> 
> echo-10000 P99=50ms 已贴近 gate，属于 10K 连接的合理退化边界。battle-500 P99=400ms（经 response/push 出站优先级隔离优化后），仍需后续架构专项（异步后端路由、多 core session 分流）进一步收紧。

---

## 4. 框架就绪但待测定的场景

以下场景已有压测命令和工具支持，但需要在固定 runner（独占机器）上执行：

### 4.1 容量专项需复测

| 场景 | 命令 | 已测定？ | 备注 |
|------|------|---------|------|
| echo-5000-30s | `--run-preset capacity` | 已测定，需固定 runner 复测 | 本机 0 failed 通过 |
| echo-10000-30s | `--run-preset capacity` | 已测定，需固定 runner 复测 | P99=50ms 贴近 gate |
| battle-500-30s | `--run-preset capacity` | 已测定，需固定 runner 复测 | P99=400ms，需架构优化 |
| 1-core echo | `--io-cores 1` | **待测定** | extrapolated 值待验证 |
| 2-core echo | `--io-cores 2` | **待测定** | extrapolated 值待验证 |

### 4.2 专项场景待测定

| 场景 | 压测命令参数 | 说明 | 预期数据 |
|------|------------|------|---------|
| Gateway 多进程桥接端到端 | `--scenario echo --clients 1000` 经过 bridge 模式 | 完整 gateway ↔ backend 桥链路 | P99, 吞吐, 错误率 |
| 登录/房间/战斗广播专项 | `--scenario battle --room-group-size N` | 不同房间规模下广播 fan-out | P99, 广播吞吐 |
| Matchmaking 匹配耗时 | `--include-business-flow` 含 match_join | matchmaking 后端延迟 | P99, 成功率 |
| Leaderboard Redis on/off | `--include-business-flow` + Redis 连接/断开 | Redis 读写对 leaderboard 的影响 | P99, 吞吐 |
| TLS on/off 损耗 | gateway 加 `--tls` 参数对比 | 加密握手和读写损耗 | P99 增量, CPU 增量 |
| OTel export 损耗 | gateway 加 `--otel` 参数对比 | 遥测导出对主链路的性能影响 | P99 增量, 吞吐衰减 |

### 4.3 稳定性浸泡待测定

| 场景 | 入口 | 持续时间 | 待采集指标 |
|------|------|---------|-----------|
| Short soak | `verify_stability_soak.py --soak-profile short` | 5-10m | RSS/fd 泄漏, P99 漂移 |
| Medium soak | `verify_stability_soak.py --soak-profile medium` | 30m | 同上 + CPU 稳定性 |
| Long soak (2h) | `verify_stability_soak.py --soak-profile long` | 2h | 内存泄漏, P99 退化, fd 泄漏 |
| Overnight soak (8h) | `verify_stability_soak.py --soak-profile overnight` | 8h | 长期稳定性全指标 |

> CI nightly-stability.yml 已配置 Ubuntu/Windows Debug 模式的 smoke/short/medium soak，但 long/overnight 需要固定 runner 和扩展 timeout。

---

## 5. 性能退化门禁

### 5.1 Release Gate 阈值（写入脚本 `evaluate_release_gates()`）

| Case | 门禁条件 | 当前实测值 | 余量 |
|------|---------|-----------|------|
| echo-100-30s | rejected=0, failed=0, p99≤50ms | 1ms | **49ms** |
| echo-1000-30s | rejected=0, failed=0, p99≤50ms | 5ms | **45ms** |
| battle-20-30s | rejected=0, failed=0, min_msgs≥1000, p99≤100ms | 10ms | **90ms** |
| battle-100-30s | rejected=0, failed=0, min_msgs≥5000, p99≤250ms | 200ms | **50ms** |

> **警告机制**: 当 echo p99 接近 45ms（gate 的 90%）或 battle p99 接近 gate 的 90% 时，脚本自动产生 warning。

### 5.2 CI 性能门禁（`config/perf/v2_arch_baseline_gates.json`）

| 指标 | Warning (us) | Critical (us) | 用途 |
|------|-------------|--------------|------|
| echo p99 | 100 | 500 | echo 场景 P99 退化监控 |
| login p99 | 500 | 2000 | 登录后端延迟退化监控 |
| battle broadcast p99 | 1000 | 5000 | 战斗广播延迟退化监控 |

CI 工作流 `perf-regression.yml` 使用 `verify_r4_contract.py` 自动判定 gate 结果。

### 5.3 退化场景及建议动作

| 退化信号 | 触发阈值 | 建议动作 |
|---------|---------|---------|
| echo P99 从 1ms 升到 >10ms | 10x 退化 | 检查 HighResTimer 是否失效 |
| echo P99 >50ms | gate 失败 | 检查后端连接池、CircuitBreaker、定时器分辨率 |
| battle P99 >250ms | gate 失败 (100人) | 检查 actor 线程阻塞、session 广播 fan-out |
| battle-500 P99 >500ms | gate 失败 | 检查 response/push 出站队列优先级 |
| 吞吐下降 >30% | 相对基线 | 检查 rate limiter、io_cores 配置 |
| RSS 超基线 20% | 内存异常 | 排查连接泄漏、对象池增长 |
| 错误率 >0.1% | 可用性退化 | 检查 CircuitBreaker 熔断、后端连通性 |

---

## 6. SLO/SLI 定义

### 6.1 服务等级目标 (SLO)

| SLO | 目标值 | SLI 测量方式 | 测量窗口 |
|-----|--------|------------|---------|
| **可用性** | 99.9% | `success / total_requests` (BackendMetrics) | 30 天滚动 |
| **延迟** | P99 ≤ 50ms (端到端 echo) | `LatencyHistogram::p99_ms` | 5 分钟滚动 |
| **网关→后端延迟** | P99 ≤ 10ms，告警阈值 200ms | `gateway_backend_*_p99_latency_us` | 5 分钟滚动 |
| **错误率** | ≤ 0.1% | `errors / total_requests` | 30 天滚动 |
| **吞吐量 (单核)** | ≥ 10K msg/s | `ThroughputTracker::rate_per_second` | 1 分钟滚动 |

### 6.2 烧录率告警阈值

| 告警级别 | 烧录率 | 触发条件 | 通知方式 |
|---------|--------|---------|---------|
| **Warning** | 2% (14.4x) | 1h 窗口内错误消耗 2% 预算 | 日志 + 指标 |
| **Critical** | 5% (36x) | 6h 窗口内错误消耗 5% 预算 | PagerDuty/钉钉 |

**错误预算** = 30 天 x (1 - 0.999) = 43.2 分钟/月

### 6.3 错误预算策略

| 预算消耗 | 动作 |
|---------|------|
| < 50% | 正常发布节奏 |
| 50%-80% | 冻结非紧急发布，优先修复 |
| 80%-100% | 冻结全部发布，全力修复 |
| 耗尽 | 启动事后复盘，下月发布需 VP 审批 |

---

## 7. P0 优化回顾

### 7.1 优化清单

| # | 优化项 | 文件 | 变更 |
|---|--------|------|------|
| P0.2a | 后端连接池扩容 | `gateway_service_bridge.cpp` | 默认池大小 1->4（压测时设为 8） |
| P0.2b | 战斗路由卸载线程数 | `runtime.cpp` | 默认工作线程 0->4 |
| P0.2c | CircuitBreaker 线程安全 | `circuit_breaker.h/.cpp` | 添加 `std::mutex` |
| P0.2d | Windows 高精度定时器 | `highres_timer.h` + 6 个进程 | RAII `timeBeginPeriod(1)` |
| P0.2e | 头文件循环依赖修复 | `runtime.h` | 前向声明替代直接 include |

### 7.2 优化前后对比

| Case | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| echo-100-30s | 100ms P99 | **1ms** P99 | **100x** |
| echo-1000-30s | 150ms P99 | **5ms** P99 | **30x** |
| battle-20-30s | 750ms P99 | **10ms** P99 | **75x** |
| battle-100-30s | 5000ms P99 | **200ms** P99 | **25x** |

### 7.3 已知问题

1. **battle-100 P99 尾部抖动**: 约 1.5% 消息落在 200-300ms 区间，由 actor 线程广播推送造成。后续可将广播推送卸载到工作线程。
2. **集成测试编译失败**: `cluster_router_e2e_test.cpp` 和 `windows_platform_test.cpp` 有编译问题，需修复测试代码。
3. **hiredis.dll 部署**: Release 构建后需手动复制 hiredis.dll 到各后端目录。

---

## 8. Data Collection Status

| Date | Runner | Preset | Status | Evidence |
|------|--------|--------|--------|----------|
| 2026-05-23 | Windows 11 (本机) | baseline (3 reps) | **PASS** | `runtime/perf/20260523-165827/summary.json` |
| 2026-05-23 | Windows 11 (本机) | P0 capacity | **PASS** | `runtime/perf/p0-capacity-local/` |
| 2026-05-23 | GitHub Actions ubuntu-latest | smoke | PASS | CI artifact |
| 2026-05-23 | GitHub Actions windows-2022 | smoke | PASS | CI artifact |
| TBD | Ubuntu fixed runner | capacity (3 reps) | **待测定，优先事实源** | `runtime/perf/fixed-runner-capacity/` |
| TBD | Ubuntu fixed runner | long soak (2h) | **待测定，优先事实源** | `runtime/validation/long-soak-2h-summary.json` |
| TBD | Ubuntu fixed runner | overnight soak (8h) | **待测定，优先事实源** | `runtime/validation/long-soak-8h-summary.json` |
| TBD | Fixed runner | 1/2 core linearity | **待测定** | - |
| TBD | Fixed runner | TLS overhead | **待测定** | - |
| TBD | Fixed runner | OTel overhead | **待测定** | - |

> CI 基线入口：
> - 每周一 06:00 UTC 自动运行：`.github/workflows/perf-regression.yml`
> - 手动触发 release baseline：`.github/workflows/release.yml`
> - 每日夜间稳定性：`.github/workflows/nightly-stability.yml`

---

## 9. 产物索引

| 产物 | 说明 |
|------|------|
| `runtime/perf/20260523-165827/summary.json` | P0 baseline 3 轮聚合原始数据 |
| `runtime/perf/20260523-165827/report.md` | P0 baseline 自动生成报告 |
| `runtime/perf/20260523-165827/results/*.result.json` | 各轮次原始压测输出 |
| `runtime/perf/20260523-165827/results/*.gateway.diagnostics.json` | gateway 诊断快照 |
| `runtime/perf/p0-capacity-local/` | P0 capacity 专项 (echo-5K/10K, battle-500) |
| `runtime/perf/p0-business-capacity-local-r2/` | P0 业务闭环容量 (含 SDK full-flow) |
| `runtime/perf/gateway-arch-priority-route4-push10/` | P1 response/push 优先级隔离专项 |
| `docs/performance-baseline-windows-p0.md` | P0 优化验收报告 |
