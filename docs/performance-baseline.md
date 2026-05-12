# v2.0.2 性能基线报告

> 基准版本: `develop` (v2.0.1 + v2.0.2 B1-B3 测量基础设施)
> 测量日期: 2026-05-12
> 测量工具: `v2_gateway_pressure` (新增), `LatencyHistogram`, `ThroughputTracker`

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

---

## 2. 吞吐量基线 (B2)

### 2.1 Echo 吞吐量 vs 核心数

| 核心数 | 并发连接 | 持续时间 | 总消息数 | 吞吐量 (msg/s) | 线性扩容系数 |
|---|---|---|---|---|---|
| 1 | 100 | 30s | _待测定_ | _待测定_ | 1.00× |
| 2 | 100 | 30s | _待测定_ | _待测定_ | _待测定_ |
| 4 | 100 | 30s | _待测定_ | _待测定_ | _待测定_ |
| 1 | 1000 | 30s | _待测定_ | _待测定_ | — |
| 2 | 1000 | 30s | _待测定_ | _待测定_ | _待测定_ |
| 4 | 1000 | 30s | _待测定_ | _待测定_ | _待测定_ |
| 4 | 10000 | 30s | _待测定_ | _待测定_ | — |

**线性扩容系数** = `吞吐量(N核) / (N × 吞吐量(1核))`

### 2.2 战斗广播吞吐量

| 房间数 | 每房间人数 | 输入间隔 | 总消息数 | 吞吐量 (msg/s) | 广播 fan-out 系数 |
|---|---|---|---|---|---|
| 10 | 2 | 100ms | _待测定_ | _待测定_ | _待测定_ |
| 50 | 2 | 100ms | _待测定_ | _待测定_ | _待测定_ |
| 100 | 2 | 100ms | _待测定_ | _待测定_ | _待测定_ |

**广播 fan-out 系数** = `(总出站消息) / (总入站消息)`

### 2.3 消息吞吐量上限

| 场景 | 峰值吞吐量 (msg/s) | 瓶颈组件 | 备注 |
|---|---|---|---|
| 纯 echo | _待测定_ | _待测定_ | — |
| 战斗广播 | _待测定_ | _待测定_ | — |
| 稳定性浸泡 | _待测定_ | _待测定_ | 8 小时运行 |

---

## 3. 延迟基线 (B3)

### 3.1 Echo 端到端延迟 (客户端视角)

| 核心数 | 并发连接 | P50 (ms) | P90 (ms) | P99 (ms) | Min (ms) | Max (ms) |
|---|---|---|---|---|---|---|
| 1 | 100 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 1 | 1000 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 4 | 100 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 4 | 1000 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| 4 | 10000 | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |

### 3.2 网关→后端延迟 (服务端 `BackendMetrics` 视角)

| 后端 | 请求数 | 成功数 | P50 (us) | P99 (us) | 超时数 | 错误数 |
|---|---|---|---|---|---|---|
| login | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| room | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |
| battle | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ | _待测定_ |

**测量方式**: `GatewayServiceBridge::route()` 中 `send_request()` 前后打点，记录到 `BackendMetrics::record_latency()`。通过 `GET /metrics/diagnostics/json` 获取。

### 3.3 战斗输入广播延迟

| 房间数 | 输入到广播 P50 (ms) | 输入到广播 P99 (ms) | 备注 |
|---|---|---|---|
| 10 | _待测定_ | _待测定_ | — |
| 50 | _待测定_ | _待测定_ | — |
| 100 | _待测定_ | _待测定_ | — |

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
| 1K echo (100 msg/s) | gateway | _待测定_ | _待测定_ | _待测定_ | — |
| 10K 空闲连接 | gateway | _待测定_ | _待测定_ | _待测定_ | — |
| 10K echo (1000 msg/s) | gateway | _待测定_ | _待测定_ | _待测定_ | — |
| 100 战斗房间 | gateway | _待测定_ | _待测定_ | _待测定_ | 每房间 2 人，10 input/s |

### 4.3 每连接边际成本

| 指标 | 每连接增量 | 计算方式 |
|---|---|---|
| RSS | _待测定_ KB | (RSS@10K − RSS@1K) / 9000 |
| fd | _待测定_ | (fd@10K − fd@1K) / 9000 |
| CPU (空闲) | _待测定_% | (CPU@10K_idle − CPU@1K_idle) / 9000 |

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
