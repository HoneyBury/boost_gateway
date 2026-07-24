# Production Platform Boundaries

更新时间：2026-07-24

BoostGateway 维护三个彼此独立的原生生产平台。机器可读事实源是
[`platform-production-boundaries.json`](platform-production-boundaries.json)；本文档解释
发布和证据边界。

## 当前状态

| 平台 | 原生产物 | 容器/运行模型 | v3.6.2 状态 |
|---|---|---|---|
| `linux-x64` | ELF x86_64 | 原生进程或 `linux/amd64` Compose | runtime、SDK、debug symbols 已发布复验 |
| `linux-arm64` | ELF ARM64 | 原生进程或 `linux/arm64` Compose | runtime、SDK、debug symbols 已发布复验 |
| `macos-arm64` | Mach-O ARM64 | macOS 原生进程编排 | runtime、SDK、dSYM 已发布复验；未声明 notarized |

v3.6.2 Release run `30063021104` 生成三平台 runtime、SDK、symbol/dSYM、SPDX、
provenance 和 checksum 资产。线上复验 runs `30063950242`、`30063441646`、
`30063444082` 分别在目标平台完成独立消费。详细交付记录位于
[v3.6 实现状态归档](archive/releases/v3.6-implementation-status.md)。

## 不可替代原则

- macOS 证据必须运行 Apple Clang 生成的 Mach-O；Mac 上的 Linux VM/Docker 结果不等于
  macOS runtime 证据。
- Linux ARM64 证据必须运行 ARM64 ELF 和 `linux/arm64` image；Rosetta 或 amd64 image
  不能替代。
- Linux x64、Linux ARM64、macOS ARM64 的 R5、性能、长稳、SDK、符号和发布消费结果
  不得跨平台拼接。
- GitHub-hosted runner 可执行有界治理或聚合任务，但不能替代要求固定机器、原生 ABI、
  Docker cache 或性能隔离的证据。

## Workflow 路由

以下 workflow 已接入平台解析契约：

- `release.yml`
- `release-asset-verification.yml`
- `production-candidate-evidence.yml`
- `production-gates.yml`
- `production-readiness.yml`
- `perf-regression.yml`
- `long-soak-capacity.yml`
- `preprod-evidence.yml` / `macos-arm64.yml`
- `sdk-distribution.yml`
- `debug-symbols.yml`
- `jwks-rotation.yml`

workflow 必须校验实际 OS/architecture，并选择对应 profile、lockfile、Conan cache、build
directory、Docker target 和 artifact suffix。跨 workflow 聚合前还必须验证每份 artifact
中的 candidate SHA、platform summary、runner identity 和 lockfile provenance。

## 平台特有边界

### Linux

- Docker image 必须精确匹配 `linux/amd64` 或 `linux/arm64`。
- R5 使用 Compose gateway restart 前后 SDK full-flow，并包含 Redis degraded/recovery。
- CPU affinity、cgroup、kind 和 Docker comparison 只用于 Linux 原生证据。
- debug-symbol package 通过 ELF build-id、debuglink、source lookup 和 crash probe 绑定。

### macOS ARM64

- R5 使用原生 Mach-O 六服务进程，不使用 Docker 作为 runtime 证据。
- 不继承 Linux 的 affinity、cgroup、kind 或容量阈值。
- dSYM 必须与 stripped runtime UUID 一致，并通过 DWARF source lookup。
- v3.6.2 GitHub Release 资产已验证，但 Apple signing/notarization 仍是独立状态。

## 当前限制

- 三平台发布支持不等于多节点 HA、任意云环境或统一容量声明。
- `grpc-experimental` 不是默认生产 transport；各平台专项状态见 JSON 清单。
- Windows 和 macOS x64 不在当前维护平台中；恢复支持需要新的 ADR、工具链和原生证据。
- 新平台只有在 profile/lockfile、runtime、SDK、符号、R5、性能和发布后消费全部闭环后，
  才能加入 `production_platforms`。

runner 的当前身份和在线状态见 [runner-inventory.md](runner-inventory.md)，准入规则见
[runner-gate-standard.md](runner-gate-standard.md)。
