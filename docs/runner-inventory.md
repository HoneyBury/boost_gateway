# GitHub Actions Runner Inventory

更新时间：2026-07-11

本文档作为仓库 Actions runner 拓扑的单一事实源。`current-state.md` 与 `fixed-runner-playbook.md` 只引用这里的结论，不再各自维护 runner 在线状态描述。

## 验证来源

- 仓库：`HoneyBury/boost_gateway`
- 验证时间：2026-07-11
- 验证命令：`gh api repos/HoneyBury/boost_gateway/actions/runners`

## 当前快照

| Runner | OS | 状态 | Busy | 版本 | Labels |
|---|---|---|---|---|---|
| `MyDesktop-Win` | Windows | `offline` | `false` | `2.334.0` | `self-hosted`, `Windows`, `X64` |

## 当前结论

- 当前仓库只存在一个离线的 Windows self-hosted runner：`MyDesktop-Win`。
- 当前没有在线的 Linux self-hosted runner 能匹配 `["self-hosted","Linux","X64"]`。
- 因此默认指向 Linux fixed-runner 的 workflow 如果直接 dispatch，会停留在 `queued`，直到 Linux runner 注册并上线。

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

- `ci.yml` 当前默认走 GitHub-hosted `ubuntu-latest` fallback，不受上述 self-hosted Linux 缺失直接阻断。
- `release.yml`、`perf-commit-check.yml`、`perf-regression.yml`、`nightly-stability.yml` 现已验证可以在手动覆盖 `runner="ubuntu-latest"` 时完成 bounded hosted 回归；但这些结果不能替代 fixed-runner production evidence。
