# CI/CD Architecture

BoostGateway 使用 GitHub Actions 进行持续集成和发布。当前主线回归和固定 runner 证据是两个不同场景：

- `ci.yml` 可在 GitHub-hosted `ubuntu-latest` 上执行，用于无 self-hosted Linux runner 时的主线 Conan build/test/gate 回归。
- `release.yml` 现在也可在 GitHub-hosted `ubuntu-latest` 上通过 `workflow_dispatch` 做有界 build/test/baseline 验证，但其结果仍不能替代 fixed-runner release/capacity/production evidence。
- release/capacity/production evidence/long soak 仍以 Linux self-hosted runner 作为证据事实源。

## Workflow 总览

| Workflow | 触发方式 | 用途 |
|---|---|---|
| `ci.yml` | v* tag / 手动 | 主线构建 + 测试 + Conan 验证 |
| `conan-validate.yml` | 手动 | Conan 依赖图验证 |
| `long-soak-capacity.yml` | 定时 | 2h/8h 长稳 + 容量测试 |
| `nightly-stability.yml` | 定时 | 夜间稳定性 soak |
| `perf-commit-check.yml` | 手动 | 性能 smoke 检测 |
| `perf-regression.yml` | 手动 | 性能回归分析 |
| `production-evidence.yml` | 手动 | 生产证据聚合 |
| `production-resilience.yml` | 定时 | 生产韧性验证 |
| `release.yml` | v* tag / 手动 | 构建 → 测试 → 门禁 → baseline；仅 tag push 进入发布 |
| `specialized-e2e.yml` | 手动 | Raft/Redis/Operator 专项 E2E |

## Runner 要求

- **主线回归兜底**: GitHub-hosted `ubuntu-latest`
- **固定 runner 证据**: Linux (Ubuntu 22.04+) + `["self-hosted", "Linux", "X64"]`
- **预装工具**: CMake 3.21+, Ninja, GCC 11+, Python 3.10+, Go 1.21+
- **可选**: sccache, Conan 2, Redis, Docker

## 产物命名约定

- Release 包: `boost-gateway-{version}-linux-x64.tar.gz`
- 验证 summary: `runtime/validation/*-summary.json`
- 性能基线: `runtime/perf/release-baseline/summary.json`

## 触发规则

- **普通 push/PR**: 不自动触发 CI（减少 runner 负载）
- **v* tag**: 自动触发 `ci.yml` 和 `release.yml`
- **手动 dispatch**: 所有其他 workflow 支持 `workflow_dispatch`
- **定时任务**: `nightly-stability.yml`、`production-resilience.yml`、`long-soak-capacity.yml`

## 配置源

- Runner 标签和默认值: `.github/runner-matrix.json`
- CMake preset: `CMakePresets.json`（`default` = Debug, `release` = Release）
- 推荐策略: `ci.yml` 默认使用 GitHub-hosted `ubuntu-latest`；`release.yml` 可手动切到 GitHub-hosted `ubuntu-latest` 做有界验证；fixed-runner 证据 workflow 默认使用 self-hosted Linux labels，并可通过 workflow `runner` 输入或 `vars.*_RUNNER` 覆盖
