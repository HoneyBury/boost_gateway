# Observability / Trace Runbook

更新时间：2026-05-18

本文档对应生产业务闭环 P5。目标是让真实业务流可以通过 gateway diagnostics、backend RED counters、trace/span 和可选 OTel collector 观察，而不是只依赖单元测试。

## 默认链路

默认 Docker Compose 生产链路不强依赖外部 OTel collector。gateway 会继续通过：

- `GET /metrics`
- `GET /metrics/json`
- `GET /metrics/diagnostics/json`
- Prometheus backend RED counters
- Grafana backend/Redis 面板

提供生产观测能力。后端 TCP 服务仍不直接暴露 HTTP `/metrics`。

## OTel Collector Profile

本地或固定 runner 需要验证 OTLP HTTP 导出时启用：

```bash
OTEL_EXPORT_ENDPOINT=http://otel-collector:4318/v1/traces \
docker compose -f env/docker/docker-compose.yml --profile otel up -d otel-collector gateway
```

配置文件：

- `env/monitoring/otel-collector.yml`
- `env/docker/docker-compose.yml` 的 `otel` profile

`OTEL_EXPORT_ENDPOINT` 为空时 gateway 不启用 OTel exporter。collector 不可用不得影响主业务流程；对应验证入口是：

```bash
python3 scripts/verify_observability_gate.py \
  --build-dir build/default \
  --skip-build \
  --include-otel-collector \
  --include-runtime-http
```

## 业务闭环验证

P5-P8 聚合入口：

```bash
python3 scripts/verify_p5_p8_business_closure.py \
  --build-dir build/default \
  --skip-build \
  --include-otel-collector \
  --include-runtime-http
```

P5 子产物：

- `runtime/validation/p5-observability-summary.json`
- `runtime/validation/gateway-observability-runtime-summary.json`

覆盖范围：

- rate limit / blocked packet 主路径
- trace context 与 W3C traceparent 兼容
- gateway -> backend trace/span 传播
- typed envelope trace/error 保留
- backend RED metrics 与 diagnostics
- fake OTel collector POST
- OTel exporter 未配置时不崩溃

## 延迟指标边界

当前 Prometheus 默认导出 backend RED counters 和 gateway 运行指标，route latency 以 diagnostics JSON 的 `avg_latency_us` / `latency_sample_count` 和性能采集报告为准。Prometheus histogram/summary 尚未作为默认 scrape 指标上线；容量、P99 和波动分析仍以 `collect_v2_perf_baseline.py --include-business-flow`、release baseline 和固定 runner soak 归档为事实源。
