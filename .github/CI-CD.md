# CI/CD Architecture

BoostGateway 使用 GitHub Actions 进行持续集成和发布。当前主线回归和 fixed-runner 证据是两个不同场景：

- `ci.yml` 可在 GitHub-hosted `ubuntu-latest` 上执行，用于无 self-hosted Linux runner 时的主线 Conan build/test/gate 回归。
- `release.yml` 与其他 fixed-runner workflow 强制使用 runner 本地 Conan 虚拟环境和持久 cache，不再把 GitHub-hosted runner 作为回退路径。
- release/performance/stability/capacity/production evidence/long soak 以 Linux self-hosted runner 作为执行与证据事实源。

## Workflow 总览

| Workflow | Actions 显示名 | 触发方式 | 用途 |
|---|---|---|---|
| `ci.yml` | Mainline / Build, Test & Governance | 手动 | 主线构建 + 测试 + Conan 验证 + 静态治理 |
| `conan-validate.yml` | Dependencies / Conan Graph Validation | 手动 | Conan 依赖图验证 |
| `debug-symbols.yml` | Release / Linux Debug Symbols Candidate | 手动 | RelWithDebInfo runtime/symbol pair、build-id/debuglink、受控崩溃符号化候选证据 |
| `grpc-experimental.yml` | Experimental / gRPC | 手动 | gRPC 可选依赖图、构建、SDK consumer 与决策边界 |
| `jwks-rotation.yml` | Security / JWKS Rotation Drill | 手动 | 真实 HTTPS、多 `kid` 轮换、stale grace、outage 与静态 key-ring 回滚证据 |
| `long-soak-capacity.yml` | Stability / Fixed-Runner Soak & Capacity | 手动 | 2h/8h 长稳 + 容量测试 |
| `nightly-stability.yml` | Stability / Bounded Soak | 手动 | 有界 smoke/short/medium stability soak |
| `perf-regression.yml` | Performance / Baseline & Regression | 手动 | smoke / baseline / capacity 性能门禁 |
| `preprod-evidence.yml` | Production / Preproduction Evidence | 手动 | R5 预发恢复与 R6 TLS 多轮证据 |
| `production-candidate-evidence.yml` | Production / Candidate Evidence | 手动 | 独立 R0 production candidate 聚合 |
| `production-gates.yml` | Production / Gate Diagnostics | 手动 | P5 resilience 与 P6 production evidence 诊断入口 |
| `production-readiness.yml` | Production / Readiness Decision | 手动 | 跨 workflow 汇聚 artifact，生成 R2/R3 准入结论 |
| `release.yml` | Release / Package & Publish | v* tag / 手动 | 构建 → 测试 → 门禁 → baseline；仅 tag push 进入发布 |
| `release-asset-verification.yml` | Release / Published Asset Verification | 手动 | 从不可移动 tag checkout 验收线上 checksum、runtime consumer 和 attestations |
| `sdk-distribution.yml` | SDK / Wheel & NuGet Candidate | 手动 | Linux x64 wheel/NuGet clean install、真实 full-flow、SBOM 与 checksum 候选证据 |
| `specialized-e2e.yml` | Infrastructure / Redis, Raft & Operator E2E | 手动 | Raft/Redis/Operator 专项 E2E |
| `macos-arm64.yml` | Platform / macOS ARM64 Production Candidate | 手动 | 原生 ARM64 Conan build、CTest、gateway restart R5、install、SDK consumer 与候选资产 |

## Runner 要求

- **主线回归兜底**: GitHub-hosted `ubuntu-latest`
- **固定 runner 证据**: Linux (Ubuntu 22.04+) + `["self-hosted", "Linux", "X64"]`
- **macOS ARM64 候选**: 原生 Apple Silicon + `["self-hosted", "macOS", "ARM64"]`；不声明容量或生产长稳
- **SDK 分发候选**: Ubuntu 22.04/glibc 2.35 x64 + Python 3.12、.NET 8、Syft
- **Linux 调试符号候选**: Linux x64 + GNU binutils、支持 build-id 的 linker、Syft
- **JWKS 轮换证据**: Ubuntu 22.04/glibc 2.35 x64 + OpenSSL、localhost bind、临时 CA trust 和严格离线 Conan 图
- **预装工具**: CMake 3.21+, Ninja, GCC 11+, Python 3.10+, Go 1.21+
- **可选**: sccache, Conan 2, Redis, Docker

### Conan fixed-runner 缓存

固定 runner 上的 Conan home 由 `scripts/tools/resolve_runner_cache.py` 分区到
`/opt/boost-gateway/conan`：键包含 Ubuntu release、GCC、架构、build type、
Conan 图和 remote 配置。新机器先按 lockfile 预热，后续 fixed-runner 任务显式使用
`--no-remote --build=never`。sccache 使用相同平台分区。`ci.yml` 运行在
GitHub-hosted runner，使用 checkout 内 `.conan2-local` + Actions cache；
`conan-validate.yml` 是唯一允许操作者显式选择批准 remote 的预热入口；
`production-readiness.yml` 不运行 Conan。最终汇聚只能使用同一个候选提交产生的 R0、2h、R4、R5、R6；核心 summary 的 provenance 会校验 checkout、workflow/run、runner、构建配置和 Conan lockfile 摘要。

`preprod-evidence.yml` 的 R5 默认使用 `docker_pull_policy=never`；完整
Docker 缓存导入及 image preflight 后才可运行。`missing` 与 `always` 仅用于
明确标注的联网诊断，不能解除 R5 预发阻断。

## 产物命名约定

- Release 包: `boost-gateway-{version}-linux-x64.tar.gz`
- macOS ARM64 候选包: `boost-gateway-{version}-macos-arm64.tar.gz`
- SDK 候选包: `boost_gateway_sdk-4.2.0-*.whl`、`BoostGateway.Sdk.4.2.0.nupkg`
- Linux symbols: `boost-gateway-{version}-linux-x64-debug-symbols.tar.gz`
- JWKS 轮换证据: `jwks-rotation-{candidate-sha}`
- 验证 summary: `runtime/validation/*-summary.json`
- 性能基线: `runtime/perf/release-baseline/summary.json`

## 触发规则

- **普通 push/PR**: 不自动触发 CI（减少 runner 负载）
- **v* tag**: 自动触发 `release.yml`；`ci.yml` 保留为手动主线回归，避免 tag 发布重复构建
- **手动 dispatch**: 所有 workflow 支持 `workflow_dispatch`
- **定时任务**: 当前没有 `schedule` / `cron` workflow；固定 runner 长任务由人工选择候选 SHA 后启动

## 配置源

- Runner 标签和默认值: `.github/runner-matrix.json`
- Workflow 清单一致性: `scripts/check_workflow_catalog.py`
- CMake preset: `CMakePresets.json`（`default` = Debug, `release` = Release）
- 推荐策略: 仅 `ci.yml` 默认使用 GitHub-hosted `ubuntu-latest`；执行 Conan 的其他常规 workflow 默认使用 self-hosted Linux labels，并可通过 workflow `runner` 输入或 `vars.*_RUNNER` 定向到已完成相应 namespace 准入的 runner
