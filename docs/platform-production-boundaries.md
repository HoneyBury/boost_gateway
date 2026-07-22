# Production Platform Boundaries

项目维护三个彼此独立的原生生产边界：`linux-x64`、`linux-arm64` 和
`macos-arm64`。`docs/platform-production-boundaries.json` 是机器可校验的能力与
迁移状态清单；`implemented` 才表示当前证据链已经闭环，`foundation` 和 `planned`
都不能用于发布声明。

| 平台 | 原生产物 | R5 运行模型 | 当前状态 |
|---|---|---|---|
| `linux-x64` | ELF x86_64 | `linux/amd64` Docker Compose 或原生进程 | 已实现的生产事实源 |
| `linux-arm64` | ELF ARM64 | `linux/arm64` Docker Compose 或原生进程 | profile、lockfile、Docker/R5 参数契约已落地，等待 runner 缓存和全门禁证据 |
| `macos-arm64` | Mach-O ARM64 | 原生进程编排，重启 gateway 后复跑 SDK full-flow | 生产候选；同 SHA 构建、安装、原生 R5 和有界 smoke 已在线通过，JWKS 已接线，等待完整基线、长稳/容量阈值和发布资产闭环 |

Docker 只产生 Linux container。Mac runner 上的 Docker daemon 即使能够运行
`linux/arm64` 或仿真的 `linux/amd64` image，也不等同于 macOS 生产验证。macOS
生产证据必须运行 Apple Clang 构建的 Mach-O 服务；Linux ARM64 生产证据必须运行
ARM64 ELF 和 `linux/arm64` image。不同平台的 R5、长稳、容量、性能阈值和发布资产
不得互相替代或拼接。

当前迁移顺序：先完成两种 ARM64 runner 的 strict offline Conan 准入和原生 R5，
再分别建立性能/容量基线、debug symbol 与 SDK package，最后扩展 release manifest、
线上资产复验和按平台汇聚的 readiness decision。

`macos-arm64.yml` 的 performance/stability 输入只产生原生有界能力证据。默认
`smoke` 用于快速验证脚本、Mach-O 服务拓扑和 artifact 契约；冻结候选时应选择
`perf_preset=baseline`、`perf_repetitions=3` 重新采样。该结果建立 macOS 自身的
基线，不继承 Linux CPU affinity、cgroup、长稳时长或容量阈值。
预冻结能力 run `29900276243` 已在
`d5dfef545b2be09539b8edea2be42ef16cc6723d` 通过默认 smoke、原生 R5、package、
SBOM 和 checksum；它不替代最终冻结 SHA 的三次 baseline 与长稳/容量证据。

macOS dSYM 使用同一个已准入的 Release Conan 图，仅对项目代码固定
`-O2 -g -DNDEBUG`。创建器先从未 strip 的 Mach-O 生成 dSYM，再执行 `strip -S`；
独立验证器要求每个 runtime/dSYM 的 ARM64 UUID、hash 和 DWARF source lookup 一致，
并验证 stripped runtime 的 ad-hoc signature 与 hello-world 行为。当前只接入候选
artifact；notarization、正式 release manifest 和线上资产复验仍是独立阻断项。
