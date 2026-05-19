# 生产证据固定 Runner 配置说明

日期：2026-05-18

本文档用于收束 P2：把 P6 生产证据从本地手动命令推进到可归档的固定 runner 流水线。默认 PR/Release 仍保持有界 smoke，真实依赖、长项和容量数据必须在固定 runner 上显式开启。

## N0 Summary 契约

固定 runner 相关 summary 统一要求如下：

- 顶层使用 `summary_version=2`
- `overall_pass` 用于表示统一口径的通过/失败；`passed` 继续兼容历史脚本
- `failed_category` 用于区分 `preflight`、`build`、`specialized`、`stability`、`data_recovery`、`observability`、`release_baseline`、`configuration`
- `environment` 记录 `platform`、`python`、`host`
- `artifacts` 明确列出子 summary、性能 summary、性能报告等路径

GitHub Actions Step Summary 统一通过 `scripts/render_validation_summary.py` 渲染，避免不同 workflow 的输出格式继续漂移。

## Runner 标签

GitHub Actions 的 `production-evidence.yml` 使用 JSON 输入解析 runner：

| 场景 | `runner` 输入 | 必需能力 |
| --- | --- | --- |
| 有界默认证据 | `"ubuntu-latest"` 或 `["self-hosted","production-evidence"]` | CMake、Ninja、Python、可绑定本地端口 |
| Redis live | `["self-hosted","production-evidence","redis-live"]` | Redis `127.0.0.1:6379` 可达 |
| Operator kind | `["self-hosted","production-evidence","operator-kind"]` | Docker daemon、kind、kubectl、make 可用 |
| Release baseline | `["self-hosted","production-evidence","release-baseline"]` | 固定 CPU/OS、低后台噪声、Release 构建目录 |
| Full evidence | `["self-hosted","production-evidence","redis-live","operator-kind","release-baseline","observability"]` | 上述全部能力，且允许测试进程绑定 loopback 端口 |

`runner` 输入必须是合法 JSON。单个 label 用带引号的字符串，例如 `"ubuntu-latest"`；多个 label 用数组，例如 `["self-hosted","production-evidence"]`。

## Workflow 场景

| 场景 | 关键输入 | 产物 |
| --- | --- | --- |
| Bounded default | 所有 include 设为 `false`，`soak_profile=smoke` | `production-evidence-summary.json`、`p6-*-summary.json`、`p6-candidate-audit-summary.json` |
| Redis + kind | `include_redis_live=true`、`include_operator_kind=true` | 额外验证 Redis live 与 Operator kind smoke |
| Observability runtime | `include_observability_runtime=true` | `p2-observability-runtime-summary.json`、`gateway-observability-runtime-summary.json` |
| Release baseline | `configuration=Release`、`include_release_baseline=true`、`perf_repetitions=3` | `p6-release-baseline-summary.json`、`runtime/perf/release-baseline/**` |
| Capacity baseline | `include_capacity_baseline=true`、`perf_repetitions=3`、`step_timeout_seconds=1800` | capacity profile perf summary |

生产候选推荐先跑 Redis + kind + observability runtime，再在 release-baseline 固定机器上独立跑 Release baseline 和 capacity baseline。Full evidence 可以作为手动最终归档，但不建议频繁触发。

## 预检与失败归因

Workflow 在正式执行前运行：

```bash
python scripts/check_fixed_runner_environment.py \
  --profile production-evidence \
  --build-dir <build-dir> \
  --summary-path runtime/validation/fixed-runner-preflight-summary.json
```

当启用 Redis 或 kind 时，workflow 会自动追加 `--require-redis` 或 `--require-kind`。预检 summary 会记录：

- `passed`：环境是否满足当前输入。
- `checks[]`：命令、Redis TCP、kind cluster、构建目录形态。
- `warnings[]`：非阻断能力缺失，例如未安装 Ninja。
- `errors[]`：阻断项，例如 Redis 不可达、Docker/kind 不可用。

如果 `fixed-runner-preflight-summary.json` 失败，优先修 runner 环境；如果预检通过但 `production-evidence-summary.json` 失败，再按 `failed_category` / `failed_step` 修业务门禁。

## 归档标准

每次 P2 生产证据流水线必须归档：

- `runtime/validation/fixed-runner-preflight-summary.json`
- `runtime/validation/production-evidence-summary.json`
- `runtime/validation/p6-stability-soak-summary.json`
- `runtime/validation/p6-data-recovery-summary.json`
- `runtime/validation/p6-specialized-e2e-summary.json`
- `runtime/validation/p6-candidate-audit-summary.json`
- 启用性能时归档 `runtime/validation/p6-release-baseline-summary.json` 与 `runtime/perf/release-baseline/**`
- 启用 runtime observability 时归档 `runtime/validation/p2-observability-runtime-summary.json` 与 `runtime/validation/gateway-observability-runtime-summary.json`

通过标准是所有启用项 summary 的 `passed=true`。容量专项如果用于发现上限，可以允许性能 gate 失败，但必须在发布说明中标注它是容量边界证据，不得把失败 capacity 结果声明为生产基线通过。

## R2 生产候选 Evidence Manifest

R2 新增 `docs/production-candidate-evidence-manifest.json`，用于把本机有界证据、固定 runner 证据和预发演练证据统一成可校验清单。

默认本机检查：

```bash
python3 scripts/check_production_evidence_manifest.py
```

默认检查会要求 R0/R1 已产出的候选证据存在、通过且未超过 freshness 窗口，并校验 R0 子 summary 被 R0 aggregate artifacts 引用。

投产前固定 runner / 预发准入检查：

```bash
python3 scripts/check_production_evidence_manifest.py --require-fixed-runner
```

该模式会把 manifest 中 `fixed_runner_required=true` 的条目提升为阻断项，当前包括：

- `fixed_runner_release_capacity`
- `preprod_recovery_drill`
- `tls_preprod_multi_run`

这些条目必须由固定性能机器或预发环境生成真实 summary 后再通过，不能用本机 smoke 结果替代。

`fixed_runner_release_capacity` 的 R4 入口：

```bash
python3 scripts/verify_fixed_runner_release_capacity.py
```

默认会消费 release baseline、capacity profile 和 business-capacity profile 的既有 summary，并输出 `runtime/validation/fixed-runner-release-capacity-summary.json`。在固定性能机器上可以先刷新对应性能产物，再运行该脚本；也可追加 `--collect-smoke` 先跑一次 fresh smoke 作为当前环境 sanity check。

`preprod_recovery_drill` 的 R5 入口：

```bash
python3 scripts/verify_preprod_recovery_drill.py --build-dir build/release
```

Docker 可用且镜像存在时，该脚本会执行真实 Docker Compose gateway restart 演练，并生成 recovery drill record；云端预发和 Kubernetes 演练应复用同一个 record schema 归档。

R5 实测注意：生产镜像 builder 需要 `python3` 支持当前 CMake/proto/gRPC 配置；gateway 生产默认应使用 `V2_BACKEND_CONNECTION_POOL_SIZE=1`，多连接池仍按实验能力处理，不能作为默认投产路径。

`tls_preprod_multi_run` 的 R6 入口：

```bash
python3 scripts/verify_tls_preprod_multi_run.py --build-dir build/release --skip-build
```

该脚本多轮调用 R1 TLS readiness，聚合证书轮换、CA mismatch expected failure 和 plain-vs-TLS overhead ratio。

R6 会启动本机服务并绑定临时 TCP 端口；在 macOS 沙箱或未授权环境下可能因端口绑定权限失败，固定 runner/预发环境应显式授予本机端口权限后刷新 summary。

R3 会把 R2 的判断渲染成投产评审报告：

```bash
python3 scripts/render_production_readiness_report.py
```

默认输出：

- `runtime/validation/r3-production-readiness-report.md`
- `runtime/validation/r3-production-readiness-report-summary.json`

报告中的 `Bounded local candidate evidence` 表示当前本机/有界证据是否健康；`Final production fixed-runner/pre-production readiness` 表示固定 runner / 预发证据是否已经满足投产准入。两者必须分开看，不能用前者替代最终投产审批。

## 推荐运行矩阵

| 阶段 | 建议运行项 | 目标 |
| --- | --- | --- |
| 每周例行 | bounded default + runtime observability | 确认默认生产证据链和 HTTP 观测没有回归 |
| Redis / kind 例行 | Redis live + Operator kind | 持续沉淀真实依赖场景证据 |
| 性能例行 | release baseline + capacity baseline | 沉淀 baseline/capacity 趋势和退化点 |
| 发布前 | Redis + kind + runtime observability + release baseline | 形成完整生产候选 evidence |
