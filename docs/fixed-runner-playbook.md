# 固定 Runner 执行手册

本文档用于把 P1 的固定机器任务从“人工约定”收束为可执行入口。默认 CI/release 仍使用有界 smoke；以下任务只在固定 runner 或手动 workflow 上执行。

P2 生产证据 runner 的详细配置、workflow 输入和归档标准见 `docs/production-evidence-runner.md`。

## Runner 标签建议

| 用途 | 建议 label | Workflow | 必需能力 |
| --- | --- | --- | --- |
| Release baseline | `self-hosted,release-baseline` | `release-baseline.yml` | 稳定 CPU、固定 OS、CMake、Ninja、Python、可绑定本地端口 |
| Redis live | `self-hosted,redis-live` | `specialized-e2e.yml` | Redis `127.0.0.1:6379` 可达，CMake、Ninja、Python |
| Operator kind | `self-hosted,operator-kind` | `specialized-e2e.yml` | Docker、kind、kubectl、make、CMake、Ninja、Python |
| Observability | `self-hosted,observability` | 手动命令或 release gate | CMake、Ninja、Python、可绑定本地端口；可选 fake OTel collector 与真实 gateway HTTP runtime 测试 |
| Control plane | `self-hosted,operator-kind` | 手动命令或 `specialized-e2e.yml` | Go、Docker、kind、kubectl、make、Python；可选 envtest assets |
| Production resilience | `self-hosted,production-resilience` | `production-resilience.yml` | CMake、Ninja、Python、可绑定本地端口；可选 Redis、Docker/kind、Release baseline 固定性能环境、runtime observability |
| Production evidence | `self-hosted,production-evidence` | `production-evidence.yml` | CMake、Ninja、Python、可绑定本地端口；可选 Redis、Docker/kind、Release baseline 固定性能环境、runtime observability |

GitHub Actions 手动触发时，`runner` 输入填实际 label。`production-evidence.yml` 的 `runner` 输入必须是 JSON：单 runner 使用 `"ubuntu-latest"`，多个 label 使用 `["self-hosted","production-evidence"]`。

## Release Baseline

手动触发 `.github/workflows/release-baseline.yml`：

| 输入 | baseline 建议值 | capacity 建议值 |
| --- | --- | --- |
| `runner` | `["self-hosted","release-baseline"]` | `["self-hosted","release-baseline"]` |
| `configure_preset` | `release` 或 `windows-ninja-release` | 同 baseline |
| `build_dir` | `build/release` 或 `build/windows-ninja-release` | 同 baseline |
| `configuration` | `Release` | `Release` |
| `perf_preset` | `baseline` | `capacity` |
| `perf_repetitions` | `3` | `3` |
| `perf_timeout_seconds` | `900` | `1800` |

通过标准：

- `runtime/validation/release-baseline-summary.json` 中 `passed=true`。
- `runtime/perf/release-baseline/summary.json` 中 `release_gates.overall_pass=true`。
- GitHub Step Summary 显示 R4、业务性能步骤均为 `PASS`。

## Specialized E2E

默认专项 E2E 不要求 Redis/kind，只跑 Raft 与 Redis degraded。固定 Redis/kind runner 上再显式开启：

| 场景 | `runner` | `include_redis_live` | `include_operator_kind` |
| --- | --- | --- | --- |
| Raft + Redis degraded | `ubuntu-latest` 或自托管普通 runner | `false` | `false` |
| Redis live | `["self-hosted","redis-live"]` | `true` | `false` |
| Operator kind | `["self-hosted","operator-kind"]` | `false` | `true` |
| 全专项 | `["self-hosted","redis-live","operator-kind"]` | `true` | `true` |

通过标准：

- `runtime/validation/specialized-e2e-summary.json` 中 `passed=true`。
- Redis live 场景必须确认 runner 上 Redis 服务可达。
- Operator kind 场景必须确认 Docker daemon、kind、kubectl、make 可用。

## Observability / P4

默认 release gate 已运行 `scripts/verify_observability_gate.py`，覆盖 rate limit、trace、OTel buffer、backend RED metrics、gateway metrics 与 audit。固定观测 runner 可追加 fake OTel collector POST 验证和真实 gateway HTTP 观测入口验证：

```bash
python scripts/check_fixed_runner_environment.py --profile observability --build-dir build/default
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-otel-collector
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-runtime-http
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-otel-collector --include-runtime-http
```

通过标准：

- `runtime/validation/observability-gate-summary.json` 中 `passed=true`。
- `--include-otel-collector` 场景必须确认 runner 允许测试进程绑定 `127.0.0.1` 随机端口。
- `--include-runtime-http` 场景会启动真实 `v2_gateway_demo`，用 SDK full-flow 产生业务流量，并验证 `/health`、`/ready`、`/metrics`、`/metrics/json`、`/metrics/diagnostics/json`；子 summary 位于 `runtime/validation/gateway-observability-runtime-summary.json`。
- 如需验证真实 collector，运行 `examples/v2_gateway_demo` 时设置 `OTEL_EXPORT_ENDPOINT=http://<collector>/v1/traces`；默认 P4 gate 不依赖真实外部 collector。

## Control Plane / P5

默认 release gate 已运行 `scripts/verify_control_plane_gate.py`，只依赖 Operator manifest 静态契约和 Go fake-client/unit tests，不要求 Docker 或 kind。固定控制面 runner 可追加：

```bash
python scripts/check_operator_manifests.py --summary-path runtime/validation/operator-manifests-summary.json
python scripts/check_fixed_runner_environment.py --profile control-plane --build-dir build/default --require-kind
python scripts/verify_control_plane_gate.py --include-kind
python scripts/verify_control_plane_gate.py --include-envtest --include-kind
```

本机收束 P5 时，如 Redis、Docker/kind、Go、kubectl 已配置完成，推荐先跑预检再跑专项聚合：

```bash
python scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-redis
python scripts/check_fixed_runner_environment.py --profile control-plane --build-dir build/default --require-kind
python scripts/verify_specialized_e2e.py --build-dir build/default --skip-build --include-redis-live --include-operator-kind --summary-path runtime/validation/dev-p5-specialized-e2e-summary.json --operator-timeout-seconds 1200
python scripts/verify_control_plane_gate.py --include-kind --summary-path runtime/validation/dev-p5-control-plane-kind-summary.json --kind-timeout-seconds 1200
```

通过标准：

- `runtime/validation/control-plane-gate-summary.json` 中 `passed=true`。
- 默认门禁会额外写出 `runtime/validation/operator-manifests-summary.json`，要求 CRD/status schema、RBAC、manager probes 和 sample 六组件静态契约通过。
- 控制面 gate 会固定使用仓库内 `runtime/go-cache`，并在执行 kind/envtest 前先做 preflight；缺少 Docker/kind 访问权限或 `KUBEBUILDER_ASSETS` 时，summary 应显示 `failed_category=preflight` 和可执行的失败原因。
- 本机收束 summary `runtime/validation/dev-p5-specialized-e2e-summary.json` 中 `passed=true`，且 `include_redis_live=true`、`include_operator_kind=true`。
- `--include-kind` 场景必须断言 sample `BoostGatewayCluster` 的 `Ready=True`、`Progressing=False`、`Degraded=False`、`TLSReady=False`，六个 `status.components[]` 均存在且可用，并验证 sample CR 删除完成。
- `--include-envtest` 场景要求 runner 已准备 controller-runtime envtest assets，例如 `KUBEBUILDER_ASSETS`。

## Production Resilience / P5

P5 长稳、故障注入与回滚演练使用 `scripts/verify_production_resilience_gate.py` 作为统一入口。默认模式保持有界，只跑固定 runner 预检、bounded stability soak、data recovery 和 Redis/Raft/Operator failure-path 专项；真实 Redis、kind、runtime HTTP、release/capacity baseline 必须显式启用。

手动触发 `.github/workflows/production-resilience.yml` 时，`runner` 输入必须是 JSON：单 runner 使用 `"ubuntu-latest"`，多个 label 使用 `["self-hosted","production-resilience"]`。

推荐本机或固定 runner 命令：

```bash
python scripts/check_fixed_runner_environment.py --profile production-resilience --build-dir build/default
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --summary-path runtime/validation/dev-p5-production-resilience-summary.json
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --soak-profile short --include-redis-live --include-runtime-http --summary-path runtime/validation/dev-p5-production-resilience-live-summary.json
python scripts/verify_production_resilience_gate.py --build-dir build/default --skip-build --include-operator-kind --kind-timeout-seconds 1200 --summary-path runtime/validation/dev-p5-production-resilience-kind-summary.json
```

通过标准：

- `runtime/validation/production-resilience-summary.json` 或指定 summary 中 `passed=true`。
- 子 summary `p5-long-soak-summary.json`、`p5-fault-data-recovery-summary.json`、`p5-specialized-failure-summary.json` 均通过。
- 启用 `--include-redis-live` 时，Redis live persistence/event-store 和 Redis service live gates 必须通过。
- 启用 `--include-operator-kind` 时，Operator kind status smoke 与 control-plane kind gate 必须通过，并覆盖 Ready/Progressing/Degraded/TLSReady、六组件 status 和 sample CR 删除。
- 启用 `--include-runtime-http` 时，真实 gateway HTTP `/health`、`/ready` 与 `/metrics*` 必须通过 runtime observability gate。

## 本地预检

执行长任务前可先跑：

```bash
python scripts/check_fixed_runner_environment.py --profile release-baseline --build-dir build/release
python scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-redis
python scripts/check_fixed_runner_environment.py --profile specialized-e2e --build-dir build/default --require-kind
python scripts/check_fixed_runner_environment.py --profile observability --build-dir build/default
python scripts/check_fixed_runner_environment.py --profile control-plane --build-dir build/default --require-kind
python scripts/check_fixed_runner_environment.py --profile production-resilience --build-dir build/default --require-redis --require-kind
```

预检只检查工具链和外部服务可达性，不替代实际测试。

## P6 Production Evidence

P6 聚合入口用于把固定 runner 上的稳定性、数据恢复、Redis/Raft/Operator 和 release baseline 证据收束到一个 summary。默认命令只跑有界任务：

```bash
python scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build
```

手动触发 `.github/workflows/production-evidence.yml` 时，`runner` 建议填 `["self-hosted","production-evidence"]`。如同时启用 Redis live 或 Operator kind，runner 需具备对应服务/工具链。

本机或固定 runner 已具备 Redis + Docker/kind 时：

```bash
python scripts/check_fixed_runner_environment.py --profile production-evidence --build-dir build/default --require-redis --require-kind
python scripts/verify_production_evidence_gate.py --build-dir build/default --skip-build --include-redis-live --include-operator-kind
```

Runtime observability 固定 runner 建议：

```bash
python scripts/verify_observability_gate.py --build-dir build/default --skip-build --include-runtime-http --summary-path runtime/validation/p2-observability-runtime-summary.json
```

Release baseline / capacity 固定机器建议：

```bash
python scripts/verify_production_evidence_gate.py --build-dir build/release --configuration Release --skip-build --soak-profile short --baseline-profile release --include-release-baseline --perf-repetitions 3
python scripts/verify_production_evidence_gate.py --build-dir build/release --configuration Release --skip-build --include-capacity-baseline --perf-repetitions 3 --step-timeout-seconds 1800
```

通过标准：

- `runtime/validation/production-evidence-summary.json` 中 `passed=true`。
- `runtime/validation/fixed-runner-preflight-summary.json` 中 `passed=true`，且 Redis/kind 必需项与 workflow 输入一致。
- 子 summary `p6-stability-soak-summary.json`、`p6-data-recovery-summary.json`、`p6-specialized-e2e-summary.json` 均为 `passed=true`。
- 启用 release/capacity baseline 时，`p6-release-baseline-summary.json` 和 `runtime/perf/release-baseline/summary.json` 必须同步归档。
- 启用 runtime observability 时，`p2-observability-runtime-summary.json` 和 `gateway-observability-runtime-summary.json` 必须同步归档。
