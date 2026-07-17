# GitHub Actions Runner Inventory

更新时间：2026-07-17（GitHub API 与 v3.5.2 Operator kind 结果）

本文档作为仓库 Actions runner 拓扑的单一事实源。`current-state.md` 与 `fixed-runner-playbook.md` 只引用这里的结论，不再各自维护 runner 在线状态描述。
runner 命名、custom labels、Conan/Docker/R5 准入规则见
`docs/runner-gate-standard.md`。

## 验证来源

- 仓库：`HoneyBury/boost_gateway`
- 验证时间：2026-07-17 14:21 UTC
- 验证命令：`gh api repos/HoneyBury/boost_gateway/actions/runners`

## 当前快照

| Runner | OS | 状态 | Busy | 版本 | Labels |
|---|---|---|---|---|---|
| `aoi-omen-gaming-laptop-16-am0xxx` | Linux | `online` | `false` | `2.335.1` | `self-hosted`, `X64`, `Linux`, `node-aoi-omen-gaming-laptop-16-am0xxx` |
| `MyDesktop-Win` | Windows | `online` | `false` | `2.334.0` | `self-hosted`, `Windows`, `X64` |
| `myserver` | Linux | `online` | `false` | `2.335.1` | `self-hosted`, `X64`, `Linux`, `preprod-r5`, `preprod-r5-myserver` |

## 当前结论

- Linux runner `aoi-omen-gaming-laptop-16-am0xxx` 已在线，并匹配 `["self-hosted","Linux","X64"]`。
- AOI 已安装并验签 kind `v0.32.0` 与 kubectl `v1.36.1`，固定 Kubernetes `v1.36.1` node digest 已进入本地 cache；run `29563770679` 的真实 Operator kind summary 为 `overall_pass=true`。
- 默认指向 Linux fixed-runner 的 workflow 可以开始实际执行；是否形成生产证据仍取决于各 workflow 的 preflight、summary 和 artifact，而不只是 job 被派发。
- `myserver` 已恢复在线，并对候选 `2b36333` 完成 G2 严格离线复验；G3/R5 若需刷新仍须先执行当前 Compose image preflight。
- Windows runner `MyDesktop-Win` 当前在线，但不是 Linux 主线的执行目标。

GitHub API 在 2026-07-17 14:21 UTC 确认三台 runner 均在线且 `busy=false`。
R5 机器专属复验必须使用目标机器的 unique custom label；不能用共享 label 代替
当前候选的 G2/G3 cache 准入。

## 2026-07-15 本机核验

| Runner | OS / toolchain | 本机状态 | 结论 |
|---|---|---|---|
| `myserver` | Ubuntu 24.04 x64, kernel 6.8, GCC 13.3, pinned venv Conan 2.8.1, Docker 29.5, Compose 5.1 | GitHub API 当前 `online`；唯一 label `preprod-r5-myserver` 与历史共享 label 仍保留 | 最终 `v3.5.2` SHA 的 strict Conan、Release `29587996645`、R0 `29588720453`、tag publish 与独立线上资产验收均通过。 |
| `aoi-omen-gaming-laptop-16-am0xxx` | Ubuntu 22.04 x64, GCC 13.4, Conan 2.8.1 | GitHub API `online`；唯一 label `node-aoi-omen-gaming-laptop-16-am0xxx` | `/opt/boost-gateway`、新图 namespace 和 runtime-only Docker cache 已预热；run `29415968573` 完成 strict offline Conan、R5 和 R6，结论 success。 |

本机核验不能替代预发 runner artifact。当前 AOI 使用机器唯一 label 定向执行；
共享 `preprod-r5` 能力池只应包含在线且通过 G0-G3 的 runner。

`myserver` 在 2026-07-15 对候选 `18abba26aba2c0fe3d7d59e399e84887f9279bab`
完成 G0-G5。主线 graph `3f740f23045ffb63d9a3` 在发现旧 seed 缺失 recipe export 后
已从批准 remote 干净重建；实验 gRPC graph `3d1aa0fa82959e8f09ad` 也已显式预热。
两者均已关闭全部 remote，并分别以 10/10、15/15 包完成离线验收。run `29428322350`
使用机器唯一 label 和 parallelism 2 完成 strict Conan、Release 全目标构建、真实 R5
gateway restart recovery drill 及两轮 R6；artifact `preprod-evidence-29428322350` 的
R5/R6 summary 均为 `overall_pass=true`。根分区在完整 run 后仍保留约 29GB 可用空间。

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
| `preprod-evidence.yml` | `29345674702` | `f6e0e57` | failure on `aoi-omen-gaming-laptop-16-am0xxx`: `/opt/boost-gateway` missing or not writable, so persistent cache resolution failed before Conan/Configure/R5. R6 build-dir failures are consequential, not independent TLS evidence. |
| `preprod-evidence.yml` | `29415647897` | `6f1a2ba` | failure on AOI runner: candidate graph key selected a new empty Conan namespace; strict offline install correctly failed at `boost/1.86.0`. |
| `preprod-evidence.yml` | `29415968573` | `6f1a2ba` | success on AOI runner: same-ABI cache seed accepted by `--no-remote --build=never`; strict Conan build, complete R5 `never` drill, two R6 runs and artifact upload passed. |
| `preprod-evidence.yml` | `29428322350` | `18abba2` | success on `myserver`: clean nosqlite cache, pinned Conan venv, Release build, complete R5 `never` drill, two R6 runs and artifact upload passed. |

2026-07-15 在 `develop` / `9c2421d` 上完成 strict-offline workflow 复验：

| Workflow | Run | 结论 |
|---|---:|---|
| `nightly-stability.yml` | `29420484420` | success: Release namespace 本地命中，bounded smoke 全链路 58 秒完成。 |
| `release.yml` | `29420589850` | success: strict Conan、build/test/gates/baseline/package 全部通过。 |
| `specialized-e2e.yml` | `29420833603` | success: Release Conan preflight 与专项 E2E 通过。 |
| `production-candidate-evidence.yml` | `29421017380` | success: R0 Release 构建与聚合通过。 |
| `production-gates.yml` | `29421305436` | success: P6 evidence 诊断入口通过。 |
| `grpc-experimental.yml` | `29416581124` | failure: strict offline runtime lockfile generation could not resolve the gRPC graph. |
| `grpc-experimental.yml` | `29420321189` | failure: 新 GCC 13 gRPC namespace `graph-a863bf92d6e235d2f53d` 缺少 recipe，严格离线快速阻断且未联网。 |
| `grpc-experimental.yml` | `29421659838` | failure: legacy cache 缺少 c-ares source；不可达代理阻止下载。旧 GCC 11 实际构建包不得冒充当前 GCC 13 cache。 |
| AOI local admission | 2026-07-16 | success: `graph-a863bf92d6e235d2f53d` passed 15/15 package offline install, followed by a 185/185 build, 17/17 focused tests, SDK consumer 5/5 and the N6 decision gate. A new GitHub run artifact remains pending. |

`v3.5.0` 的最终 AOI 证据已经在同一候选 `eed73cc` 上闭环：R0 `29508284109`、2h/capacity/R4 `29509769283`、R5/R6 `29509972609`、R2/R3 `29544170962`、tag package/publish `29544261478` 均成功。AOI 仍缺少 kind/kubectl，因此 Operator 真实集群专项保留给 `v3.5.2`；静态 manifest、fake-client/unit gate 已通过。

`v3.5.1` 发布治理候选 `d7ecb1a` 也已由 AOI 完成 Release package `29551112356`、增强 R0 `29551445037` 和 tag package/publish `29551782341`。该 patch 没有运行时代码变化；真实 gzip/layout/LICENSE、完整 Release notes、Redis live、runtime HTTP、release baseline 与 SDK/生产聚合均通过。

`v3.5.2` 阶段验证中，AOI Release run `29560450740`（`9945028`）已完成 clean Ubuntu package consumer 和 SPDX SBOM；Specialized E2E run `29563770679`（`21a4815`）已完成真实 kind Ready/scale/restart/rollback/delete/cleanup，artifact `8400330394` 的 summary 为 `overall_pass=true`。GitHub API 同期仍显示 `myserver` 离线，因此第二 Linux runner 的同资产复验没有完成，不能由 AOI 结果代替。

后续刷新中，Release run `29564215641`（`18de5ed`）再次通过 clean package/SBOM；增强 R0 run `29564768686`（`d8d8108`）在同一 AOI 上通过 Redis live、runtime HTTP、release baseline、P5/P6 两轮真实 kind 与 N5 SDK，artifact `8400890077` 的顶层 provenance 与 checkout/runner/lockfile 一致。2026-07-17 `myserver` 恢复在线并对 `2b36333` 完成 G2，但本机 workflow artifact 尚未生成，因此仍不构成第二机器发行证据。

最终 `v3.5.2` tag 固定在 `a0c6d051d09129a5330326c533bc65e4067b025d`。`myserver` Release run `29587996645` / artifact `8409986789`、增强 R0 run `29588720453` / artifact `8410441198` 与 tag package/publish run `29589708378` attempt 2 全部成功。AOI 发布后独立下载验收 run `29591469812` / artifact `8411254103` 记录同一 tarball/SPDX digest、offline runtime consumer 和两类 attestation 均通过；`myserver` 也独立验收同一线上资产。

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
- `grpc-experimental.yml` 的当前事实是：默认使用 fresh checkout，并无条件执行 `--no-remote --build=never`；对应 Ubuntu release/GCC/arch/build-type/gRPC lockfile 分区必须预先完成准入，不得跨 Ubuntu release 或实际编译器版本复用二进制包。
- 上表的 bounded 成功 run 不替代 `specialized-e2e.yml`、`production-gates.yml`、`preprod-evidence.yml` 与 `long-soak-capacity.yml` 的固定 runner 生产证据。
