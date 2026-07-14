# GitHub Actions Runner Inventory

更新时间：2026-07-14（补充本机 R5 验证）

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

## 2026-07-14 本机核验

| Runner | OS / toolchain | 本机状态 | 结论 |
|---|---|---|---|
| `myserver` | Ubuntu 24.04 x64, kernel 6.8, GCC 13.3, Conan 2.29, Docker 29.5, Compose 5.1 | 本地 `Runner.Listener` service 为 `active`；未使用 GitHub API 重新确认远端 online 状态 | R5 Docker Compose `never` drill 本机通过，见 `preprod-recovery-drill-summary.json`；仍需同一候选 SHA 的 GitHub workflow artifact。 |
| 第二台 Ubuntu runner | 未通过 SSH 或 GitHub API 访问 | 未核验 | 先按 `docs/fixed-runner-playbook.md` 创建 `/opt/boost-gateway/{conan,sccache}`，再执行 Conan 预热、Docker bundle import 和 `never` preflight。 |

本机核验不能更新上方 GitHub API 快照，也不能替代预发 runner 证据。第二台
runner 的 SSH 目标或已认证 GitHub API 是继续验证的前置条件。

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
| `specialized-e2e.yml` | `29145172304` | `e8ea86f` | success |
| `production-resilience.yml` | `29145497642` | `cbed12d` | success |
| `production-evidence.yml` | `29146018657` | `ecf624c` | success |
| `production-candidate-evidence.yml` | `29152333112` | `8cadbef` | success: R0/P5/P6/N5 summaries all passed |
| `long-soak-capacity.yml` | `29146495724` | `ea05045` | failure: long profile recorded 13.952s, not 2h; battle-500 P99=750ms; business-capacity UTF-8 decode failure |
| `long-soak-capacity.yml` | `29153158335` | `f5516be` | failure: Conan and SDK business flow passed; second sustained baseline gate failed after 209s; battle-500 P99=750ms persists |

2026-07-12 在 `main` 上补充完成的 gRPC fixed-runner 实验验证：

| Workflow | Run | 提交 | 结论 |
|---|---:|---|---|
| `grpc-experimental.yml` | `29195792943` | `5df1479` | failure: `use_existing_workspace=true` 时 runner workspace HEAD 与 `GITHUB_SHA` 不一致，命中 preflight 保护 |
| `grpc-experimental.yml` | `29196150703` | `0af5c91` | success: `use_existing_workspace=false` + `no_remote=true`；当时 runner 预置 Conan 缓存可完成 `BOOST_BUILD_GRPC=ON`、SDK consumer 与 decision-boundary 验证。当前缓存策略已按 Ubuntu release/GCC/arch/build type 分区。 |

上述 bounded workflow、专项 E2E、历史生产 resilience/evidence 和 R0 candidate 已形成真实 fixed-runner 事实。`perf-commit-check.yml`、`production-resilience.yml`、`production-evidence.yml` 已退役，当前分别由 `perf-regression.yml` 和 `production-gates.yml` 承接。long-soak workflow 的历史 artifact 未证明 2h 稳定性，只证明 long profile 的有界执行通过；真实时长 soak、capacity/business-capacity、R4 和 R2/R3 仍需按同一候选 SHA 刷新。

## 受影响的默认 fixed-runner workflow

- `release.yml`
- `perf-regression.yml`
- `nightly-stability.yml`
- `conan-validate.yml`
- `specialized-e2e.yml`
- `production-gates.yml`
- `production-candidate-evidence.yml`
- `preprod-evidence.yml`
- `long-soak-capacity.yml`

实验性 fixed-runner workflow：

- `grpc-experimental.yml`

## 说明

- `ci.yml` 当前默认走 GitHub-hosted `ubuntu-latest` fallback；其余 workflow 可使用在线 Linux fixed runner。
- `grpc-experimental.yml` 的当前事实是：默认应使用 fresh checkout；若对应 Ubuntu release/GCC/arch/build-type 分区已经预热，`no_remote=true` 可避免重复远端 Conan 拉取，且不得跨 Ubuntu release 复用二进制包。
- 上表的 bounded 成功 run 不替代 `specialized-e2e.yml`、`production-gates.yml`、`preprod-evidence.yml` 与 `long-soak-capacity.yml` 的固定 runner 生产证据。
