# CI/CD Architecture

BoostGateway 使用 GitHub Actions 进行持续集成和发布。所有 workflow 运行在 Linux self-hosted runner 上。

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
| `release.yml` | v* tag | 构建 → 测试 → 门禁 → 打包 → 发布 |
| `specialized-e2e.yml` | 手动 | Raft/Redis/Operator 专项 E2E |

## Runner 要求

- **平台**: Linux (Ubuntu 22.04+)
- **标签**: `["self-hosted", "Linux", "X64"]`
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
