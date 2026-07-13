# CI/CD Architecture

BoostGateway 使用 GitHub Actions 进行持续集成和发布。当前主线回归和固定 runner 证据是两个不同场景：

- `ci.yml` 可在 GitHub-hosted `ubuntu-latest` 上执行，用于无 self-hosted Linux runner 时的主线 Conan build/test/gate 回归。
- `release.yml` 现在也可在 GitHub-hosted `ubuntu-latest` 上通过 `workflow_dispatch` 做有界 build/test/baseline 验证，但其结果仍不能替代 fixed-runner release/capacity/production evidence。
- release/capacity/production evidence/long soak 仍以 Linux self-hosted runner 作为证据事实源。

## Workflow 总览

| Workflow | Actions 显示名 | 触发方式 | 用途 |
|---|---|---|---|
| `ci.yml` | Mainline / Build, Test & Governance | 手动 | 主线构建 + 测试 + Conan 验证 + 静态治理 |
| `conan-validate.yml` | Dependencies / Conan Graph Validation | 手动 | Conan 依赖图验证 |
| `grpc-experimental.yml` | Experimental / gRPC | 手动 | gRPC 可选依赖图、构建、SDK consumer 与决策边界 |
| `long-soak-capacity.yml` | Stability / Fixed-Runner Soak & Capacity | 手动 | 2h/8h 长稳 + 容量测试 |
| `nightly-stability.yml` | Stability / Bounded Soak | 手动 | 有界 smoke/short/medium stability soak |
| `perf-regression.yml` | Performance / Baseline & Regression | 手动 | smoke / baseline / capacity 性能门禁 |
| `preprod-evidence.yml` | Production / Preproduction Evidence | 手动 | R5 预发恢复与 R6 TLS 多轮证据 |
| `production-candidate-evidence.yml` | Production / Candidate Evidence | 手动 | 独立 R0 production candidate 聚合 |
| `production-gates.yml` | Production / Gate Diagnostics | 手动 | P5 resilience 与 P6 production evidence 诊断入口 |
| `production-readiness.yml` | Production / Readiness Decision | 手动 | 跨 workflow 汇聚 artifact，生成 R2/R3 准入结论 |
| `release.yml` | Release / Package & Publish | v* tag / 手动 | 构建 → 测试 → 门禁 → baseline；仅 tag push 进入发布 |
| `specialized-e2e.yml` | Infrastructure / Redis, Raft & Operator E2E | 手动 | Raft/Redis/Operator 专项 E2E |

## Runner 要求

- **主线回归兜底**: GitHub-hosted `ubuntu-latest`
- **固定 runner 证据**: Linux (Ubuntu 22.04+) + `["self-hosted", "Linux", "X64"]`
- **预装工具**: CMake 3.21+, Ninja, GCC 11+, Python 3.10+, Go 1.21+
- **可选**: sccache, Conan 2, Redis, Docker

### Conan fixed-runner 缓存

固定 runner 上的 Conan home 必须保存在 checkout 同级目录
`${{ github.workspace }}/../.conan2-local`。新机器第一次运行时允许
`conan install` 从远端拉取完整依赖，成功后将填充后的 `.conan2-local`
复制到该路径，并在 runner 清理或重建 checkout 时保留它。这样后续
workflow 会直接复用本地包缓存。`ci.yml` 运行在 GitHub-hosted runner，
是唯一使用 checkout 内 `.conan2-local` + Actions cache 的例外；
`production-readiness.yml` 不运行 Conan。最终汇聚只能使用同一个候选提交产生的 R0、2h、R4、R5、R6；核心 summary 的 provenance 会校验 checkout、workflow/run、runner、构建配置和 Conan lockfile 摘要。

`preprod-evidence.yml` 的 R5 默认使用 `docker_pull_policy=missing`，只补齐 Compose 动态清单中缺失的 registry 镜像；镜像已完整预热的固定 runner 可使用 `never` 完全离线验证，`always` 仅用于明确要求刷新远端镜像的场景。

## 产物命名约定

- Release 包: `boost-gateway-{version}-linux-x64.tar.gz`
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
- 推荐策略: `ci.yml` 默认使用 GitHub-hosted `ubuntu-latest`；`release.yml` 可手动切到 GitHub-hosted `ubuntu-latest` 做有界验证；fixed-runner 证据 workflow 默认使用 self-hosted Linux labels，并可通过 workflow `runner` 输入或 `vars.*_RUNNER` 覆盖
