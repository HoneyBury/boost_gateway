# GitHub Actions Runner Inventory

更新时间：2026-07-11（workflow 实跑刷新）

本文档作为仓库 Actions runner 拓扑的单一事实源。`current-state.md` 与 `fixed-runner-playbook.md` 只引用这里的结论，不再各自维护 runner 在线状态描述。

## 验证来源

- 仓库：`HoneyBury/boost_gateway`
- 验证时间：2026-07-11
- 验证命令：`gh api repos/HoneyBury/boost_gateway/actions/runners`

## 当前快照

| Runner | OS | 状态 | Busy | 版本 | Labels |
|---|---|---|---|---|---|
| `aoi-omen-gaming-laptop-16-am0xxx` | Linux | `online` | `false` | `2.335.1` | `self-hosted`, `X64`, `Linux` |
| `MyDesktop-Win` | Windows | `offline` | `false` | `2.334.0` | `self-hosted`, `Windows`, `X64` |

## 当前结论

- Linux runner `aoi-omen-gaming-laptop-16-am0xxx` 已在线，并匹配 `["self-hosted","Linux","X64"]`。
- 默认指向 Linux fixed-runner 的 workflow 可以开始实际执行；是否形成生产证据仍取决于各 workflow 的 preflight、summary 和 artifact，而不只是 job 被派发。
- Windows runner `MyDesktop-Win` 仍离线，不是当前 Linux 主线的执行目标。

## 最近验证

2026-07-11 在 `develop` 上完成的手动 workflow 验证：

| Workflow | Run | 提交 | 结论 |
|---|---:|---|---|
| `release.yml` | `29142235214` | `cb1c853` | success |
| `conan-validate.yml` | `29142663279` | `cb1c853` | success |
| `nightly-stability.yml` | `29142649741` | `cb1c853` | success |
| `ci.yml` | `29143332897` | `cb1c853` | success |
| `perf-regression.yml` | `29143057517` | `cb1c853` | success |
| `perf-commit-check.yml` | `29144040776` | `cb1c853` | success |

上述六个有界 workflow 已在 `cb1c853` 上成功；它们不替代固定 runner 的 long-soak/capacity 与 production evidence。

## 受影响的默认 fixed-runner workflow

- `release.yml`
- `perf-commit-check.yml`
- `perf-regression.yml`
- `nightly-stability.yml`
- `conan-validate.yml`
- `specialized-e2e.yml`
- `production-evidence.yml`
- `production-resilience.yml`
- `long-soak-capacity.yml`

## 说明

- `ci.yml` 当前默认走 GitHub-hosted `ubuntu-latest` fallback；其余 workflow 可使用在线 Linux fixed runner。
- 上表的 bounded 成功 run 不替代 `specialized-e2e.yml`、`production-resilience.yml`、`production-evidence.yml` 与 `long-soak-capacity.yml` 的固定 runner 生产证据。
