# Production Platform Boundaries

项目维护三个彼此独立的原生生产边界：`linux-x64`、`linux-arm64` 和
`macos-arm64`。`docs/platform-production-boundaries.json` 是机器可校验的能力与
迁移状态清单；`implemented` 才表示当前证据链已经闭环，`foundation` 和 `planned`
都不能用于发布声明。

| 平台 | 原生产物 | R5 运行模型 | 当前状态 |
|---|---|---|---|
| `linux-x64` | ELF x86_64 | `linux/amd64` Docker Compose 或原生进程 | 已实现的生产事实源 |
| `linux-arm64` | ELF ARM64 | `linux/arm64` Docker Compose 或原生进程 | 生产候选；Conan、R5/R6、JWKS、SDK、符号、Release/R0、三轮基线、容量/R4 与 2h soak 已有预冻结证据，等待 frozen-SHA refresh 和发布资产 |
| `macos-arm64` | Mach-O ARM64 | 原生进程编排，重启 gateway 后复跑 SDK full-flow | 生产候选；原生 R5、JWKS、SDK/dSYM、Release/R0、三轮基线、容量/R4 与 2h soak 已有预冻结证据，等待 frozen-SHA refresh 和 notarized 发布资产 |

Docker 只产生 Linux container。Mac runner 上的 Docker daemon 即使能够运行
`linux/arm64` 或仿真的 `linux/amd64` image，也不等同于 macOS 生产验证。macOS
生产证据必须运行 Apple Clang 构建的 Mach-O 服务；Linux ARM64 生产证据必须运行
ARM64 ELF 和 `linux/arm64` image。不同平台的 R5、长稳、容量、性能阈值和发布资产
不得互相替代或拼接。

两种 ARM64 runner 的 strict-offline Conan、原生 R5、JWKS、SDK package、符号资产、
Release/R0、三轮性能基线、容量/R4 和 2h soak 预冻结证据已经完成。当前迁移顺序
变为：冻结一个 SHA 并刷新三个平台的 required evidence，随后扩展 release manifest、
notarization、线上资产复验和按平台汇聚的 readiness decision。

默认分支已把 `release.yml`、
`production-candidate-evidence.yml`、`perf-regression.yml`、
`long-soak-capacity.yml`、`production-gates.yml`、`production-readiness.yml` 和
`release-asset-verification.yml` 接入统一的原生平台解析契约。解析器在 job 内校验
真实 OS/CPU，并固定 profile、Release/Debug lockfile、cache root、build directory、
Docker target 和 artifact suffix。tag Release 使用三平台矩阵；发布汇总要求恰好三份
archive 和三份 SPDX。ARM Release/R0/baseline/soak/capacity 已有预冻结运行事实；
该路由状态仍不能替代冻结 SHA 或已发布资产证据。

macOS 路径拒绝 Linux CPU affinity、kind 和 Redis Docker comparison 等 Linux 专属
开关；其 package consumer 直接检查 Mach-O ARM64、C ABI 和原生 CMake consumer。
Linux 发布消费同时检查 ELF 架构与 OCI image 架构，并强制 `linux/amd64` 或
`linux/arm64`。readiness artifact 名包含平台，且在汇聚前验证每个输入 artifact 的
`production-platform-summary.json`，禁止跨平台 run ID 拼接。
Linux R5/R6 继续由 `preprod-evidence.yml` 使用 `linux/amd64` 或 `linux/arm64`；Mac
readiness 则消费 `macos-arm64.yml` 产生的原生 R5 和 TLS multi-run 别名，不把 Mac
Docker daemon 作为证据来源。

`macos-arm64.yml` 的 performance/stability 输入只产生原生有界能力证据。默认
`smoke` 用于快速验证脚本、Mach-O 服务拓扑和 artifact 契约；冻结候选时应选择
`perf_preset=baseline`、`perf_repetitions=3` 重新采样。该结果建立 macOS 自身的
基线，不继承 Linux CPU affinity、cgroup、长稳时长或容量阈值。
最新预冻结能力 run `29927622379` 已在
`a355fb7500ad259ae8921db04effbe325483400f` 通过 strict-offline Conan、全量 build/CTest、
默认 smoke、原生 R5、package、SBOM、checksum 与 dSYM 候选；同 SHA SDK run
`29928355843` 又完成 `osx-arm64` wheel/NuGet、23/23 package checks 和 15/15 full-flow。
后续 runs `29952053505`、`29948796107` 与 `29961425142` 已分别补齐三次 baseline、
capacity/R4 与原生 2h soak；这些结果仍不替代最终冻结 SHA 的刷新证据。

macOS dSYM 使用同一个已准入的 Release Conan 图，仅对项目代码固定
`-O2 -g -DNDEBUG`。创建器先从未 strip 的 Mach-O 生成 dSYM，再执行 `strip -S`；
独立验证器要求每个 runtime/dSYM 的 ARM64 UUID、hash 和 DWARF source lookup 一致，
并验证 stripped runtime 的 ad-hoc signature 与 hello-world 行为。当前只接入候选
artifact；notarization、正式 release manifest 和线上资产复验仍是独立阻断项。
run `29927622379` 为 14 个 Mach-O 生成 pair，独立校验 `160/160`，两个 archive
及各自 SPDX checksum 均通过；artifact
`macos-arm64-a355fb7500ad259ae8921db04effbe325483400f` 仍是候选而非已发布资产。
