# Production Platform Boundaries

项目维护三个彼此独立的原生生产边界：`linux-x64`、`linux-arm64` 和
`macos-arm64`。`docs/platform-production-boundaries.json` 是机器可校验的能力与
迁移状态清单；`implemented` 才表示当前证据链已经闭环，`foundation` 和 `planned`
都不能用于发布声明。

| 平台 | 原生产物 | R5 运行模型 | 当前状态 |
|---|---|---|---|
| `linux-x64` | ELF x86_64 | `linux/amd64` Docker Compose 或原生进程 | 已实现的生产事实源 |
| `linux-arm64` | ELF ARM64 | `linux/arm64` Docker Compose 或原生进程 | profile、lockfile、Docker/R5 参数契约已落地，等待 runner 缓存和全门禁证据 |
| `macos-arm64` | Mach-O ARM64 | 原生进程编排，重启 gateway 后复跑 SDK full-flow | 生产候选；构建、安装与原生 R5 已接线，等待线上 run、平台基线和发布资产闭环 |

Docker 只产生 Linux container。Mac runner 上的 Docker daemon 即使能够运行
`linux/arm64` 或仿真的 `linux/amd64` image，也不等同于 macOS 生产验证。macOS
生产证据必须运行 Apple Clang 构建的 Mach-O 服务；Linux ARM64 生产证据必须运行
ARM64 ELF 和 `linux/arm64` image。不同平台的 R5、长稳、容量、性能阈值和发布资产
不得互相替代或拼接。

当前迁移顺序：先完成两种 ARM64 runner 的 strict offline Conan 准入和原生 R5，
再分别建立性能/容量基线、debug symbol 与 SDK package，最后扩展 release manifest、
线上资产复验和按平台汇聚的 readiness decision。
